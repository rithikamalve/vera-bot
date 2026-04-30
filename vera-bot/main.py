import sys
import time
import os
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from store import ContextStore
from composer import compose
from fsm import ConversationFSM
from validators import validate

app = FastAPI(title="Vera Bot", version="1.0.0")

store = ContextStore()
fsm = ConversationFSM()
START_TIME = time.time()

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

TEAM_NAME = os.environ.get("TEAM_NAME", "Team Vera")
TEAM_EMAIL = os.environ.get("TEAM_EMAIL", "vera@example.com")


@app.get("/v1/healthz")
async def healthz():
    counts = store.count_by_scope()
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts,
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_email": TEAM_EMAIL,
        "bot_name": "Vera",
        "version": "1.0.0",
        "model": "gpt-4o (primary) / llama-3.3-70b-versatile via Groq (fallback)",
        "description": "Trigger-routed WhatsApp message composer for magicpin merchants",
    }


@app.post("/v1/context")
async def context_push(request: Request):
    try:
        body = await request.json()
        scope = body.get("scope")
        context_id = body.get("context_id")
        version = body.get("version")
        payload = body.get("payload", {})

        if scope not in VALID_SCOPES:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {sorted(VALID_SCOPES)}"},
            )

        result = store.set(scope, context_id, version, payload)

        if result in ("accepted", "updated"):
            return {
                "accepted": True,
                "ack_id": f"ack_{context_id}_v{version}",
                "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        else:  # stale
            return JSONResponse(
                status_code=409,
                content={
                    "accepted": False,
                    "reason": "stale_version",
                    "current_version": store.get_version(scope, context_id),
                },
            )
    except Exception as e:
        print(f"[ERROR] /v1/context: {e}", file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/v1/tick")
async def tick(request: Request):
    try:
        body = await request.json()
        now_str = body.get("now", datetime.now(timezone.utc).isoformat())
        available_triggers = body.get("available_triggers", [])

        try:
            now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
        except Exception:
            now_dt = datetime.now(timezone.utc)

        # Pass 1: candidate selection — fast in-memory checks, no LLM calls
        candidates = []
        for trigger_id in available_triggers:
            if len(candidates) >= 20:
                break

            trigger_payload = store.get("trigger", trigger_id)
            if not trigger_payload:
                continue

            expires_at = trigger_payload.get("expires_at", "")
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if now_dt > exp_dt:
                        continue
                except Exception:
                    pass

            suppression_key = trigger_payload.get("suppression_key", "")
            if suppression_key and store.is_suppressed(suppression_key):
                continue

            merchant_id = (
                trigger_payload.get("merchant_id", "")
                or trigger_payload.get("payload", {}).get("merchant_id", "")
            )
            if not merchant_id:
                continue
            merchant_payload = store.get("merchant", merchant_id)
            if not merchant_payload:
                continue

            category_slug = merchant_payload.get("category_slug") or merchant_payload.get("identity", {}).get("category")
            if not category_slug:
                continue
            category_payload = store.get("category", category_slug)
            if not category_payload:
                continue

            customer_id = trigger_payload.get("customer_id") or trigger_payload.get("payload", {}).get("customer_id")
            customer_payload = store.get("customer", customer_id) if customer_id else None

            already_active = False
            all_convs = store.get_all_conversations()
            for conv_id, turns in all_convs.items():
                if not turns:
                    continue
                conv_merchant_id = conv_id.split("_")[1] if "_" in conv_id else ""
                if conv_merchant_id != merchant_id:
                    continue
                last_vera = next((t for t in reversed(turns) if t.get("from") == "vera"), None)
                if last_vera:
                    try:
                        ts = datetime.fromisoformat(last_vera["ts"].replace("Z", "+00:00"))
                        if (now_dt - ts).total_seconds() < 300:
                            already_active = True
                            break
                    except Exception:
                        pass
            if already_active:
                continue

            conversation_id = f"conv_{merchant_id}_{trigger_id}"
            candidates.append({
                "trigger_id": trigger_id,
                "trigger_payload": trigger_payload,
                "merchant_payload": merchant_payload,
                "merchant_id": merchant_id,
                "category_payload": category_payload,
                "customer_payload": customer_payload,
                "customer_id": customer_id,
                "suppression_key": suppression_key,
                "conversation_id": conversation_id,
                "conv_history": store.get_history(conversation_id),
            })

        # Pass 2: parallel LLM calls — all candidates composed concurrently
        async def compose_one(c):
            try:
                output = await asyncio.to_thread(
                    compose,
                    c["category_payload"], c["merchant_payload"],
                    c["trigger_payload"], c["customer_payload"], c["conv_history"],
                )
                validate(output, c["merchant_payload"], c["category_payload"], c["conv_history"])
                return c, output
            except Exception as e:
                print(f"[ERROR] tick compose/validate for {c['trigger_id']}: {e}", file=sys.stderr)
                return c, None

        results = await asyncio.gather(*[compose_one(c) for c in candidates])

        # Pass 3: commit suppressions and conversation turns, build action list
        actions = []
        for c, output in results:
            if output is None:
                continue

            if c["suppression_key"]:
                store.suppress(c["suppression_key"])

            store.add_turn(c["conversation_id"], {
                "from": "vera",
                "body": output.get("body", ""),
                "ts": now_dt.isoformat().replace("+00:00", "Z"),
                "turn_number": len(c["conv_history"]) + 1,
            })

            merchant_name = c["merchant_payload"].get("identity", {}).get("name", c["merchant_id"])
            actions.append({
                "conversation_id": c["conversation_id"],
                "merchant_id": c["merchant_id"],
                "customer_id": c["customer_id"],
                "send_as": output.get("send_as", "vera"),
                "trigger_id": c["trigger_id"],
                "template_name": "vera_v1",
                "template_params": [merchant_name, c["trigger_payload"].get("kind", ""), ""],
                "body": output.get("body", ""),
                "cta": output.get("cta", "none"),
                "suppression_key": output.get("suppression_key", c["suppression_key"]),
                "rationale": output.get("rationale", ""),
            })

        return {"actions": actions}

    except Exception as e:
        print(f"[ERROR] /v1/tick: {e}", file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/v1/reply")
async def reply(request: Request):
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id", "")
        merchant_id = body.get("merchant_id", "")
        customer_id = body.get("customer_id")
        from_role = body.get("from_role", "merchant")
        message = body.get("message", "")
        received_at = body.get("received_at", datetime.now(timezone.utc).isoformat())
        turn_number = body.get("turn_number", 1)

        # Record incoming turn
        incoming_turn = {
            "from": from_role,
            "body": message,
            "ts": received_at,
            "turn_number": turn_number,
        }
        store.add_turn(conversation_id, incoming_turn)
        # Track message per-merchant for cross-conversation auto-reply detection
        if merchant_id:
            store.record_merchant_message(merchant_id, message)

        merchant_payload = store.get("merchant", merchant_id)
        if not merchant_payload:
            return JSONResponse(status_code=404, content={"error": f"merchant {merchant_id!r} not found"})

        category_slug = merchant_payload.get("category_slug") or merchant_payload.get("identity", {}).get("category")
        category_payload = store.get("category", category_slug) if category_slug else {}
        if not category_payload:
            category_payload = {}

        result = fsm.handle_reply(
            conversation_id,
            message,
            merchant_payload,
            category_payload,
            store,
            compose,
            merchant_id=merchant_id,
        )

        if result.get("action") == "send" and result.get("body"):
            vera_turn = {
                "from": "vera",
                "body": result["body"],
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "turn_number": turn_number + 1,
            }
            store.add_turn(conversation_id, vera_turn)

        return result

    except Exception as e:
        print(f"[ERROR] /v1/reply: {e}", file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/v1/teardown")
async def teardown():
    try:
        store.clear()
        return {"ok": True}
    except Exception as e:
        print(f"[ERROR] /v1/teardown: {e}", file=sys.stderr)
        return JSONResponse(status_code=500, content={"error": str(e)})
