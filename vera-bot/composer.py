import json
import os
import sys
from datetime import datetime

from groq import Groq

from prompts.base import SYSTEM_PROMPT
from prompts.templates import get_user_prompt
from validators import validate

_groq_client = None
_openai_client = None

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_TIMEOUT = int(os.environ.get("OPENAI_TIMEOUT", "20"))


def _get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    return _groq_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        from openai import OpenAI
        _openai_client = OpenAI(api_key=key)
    return _openai_client


def extract_facts(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    facts = {}

    # 1. CTR gap — only for merchant-facing messages (never expose internal metrics to customers)
    is_customer_scope = trigger.get("scope") == "customer"
    perf = merchant.get("performance", {})
    peer_stats = category.get("peer_stats", {})
    ctr = perf.get("ctr")
    peer_ctr = peer_stats.get("avg_ctr")
    if not is_customer_scope and ctr is not None and peer_ctr is not None:
        gap = round(float(ctr) - float(peer_ctr), 4)
        facts["ctr_gap"] = f"{float(ctr)*100:.1f}% vs peer {float(peer_ctr)*100:.1f}% ({gap*100:+.1f}%)"
    else:
        facts["ctr_gap"] = None

    # 2. Top digest item
    digest = category.get("digest", [])
    top_item_id = trigger.get("payload", {}).get("top_item_id")
    top_digest = None
    if top_item_id:
        for item in digest:
            if item.get("id") == top_item_id:
                top_digest = item
                break
    if top_digest is None and digest:
        top_digest = digest[0]
    if top_digest:
        facts["top_digest_item"] = f'{top_digest.get("title", "")} [{top_digest.get("source", "")}]'
    else:
        # Also check trigger payload directly for digest items
        payload = trigger.get("payload", {})
        top_item = payload.get("top_item")
        if top_item:
            facts["top_digest_item"] = f'{top_item.get("title", "")} [{top_item.get("source", "")}]'
        else:
            facts["top_digest_item"] = None

    # 3. Active offer
    offers = merchant.get("offers", [])
    active_offer = next((o for o in offers if o.get("status") == "active"), None)
    if active_offer:
        price = active_offer.get("price", "")
        title = active_offer.get("title", active_offer.get("name", ""))
        facts["active_offer"] = f"{title} @ {price}" if price else title
    else:
        facts["active_offer"] = None

    # 4. Lapsed customers
    cust_agg = merchant.get("customer_aggregate", {})
    facts["lapsed_count"] = cust_agg.get("lapsed_180d_plus", cust_agg.get("lapsed_count"))

    # 5. Customer recall window
    if customer:
        rel = customer.get("relationship", {})
        last_visit = rel.get("last_visit")
        if last_visit:
            try:
                lv_date = datetime.strptime(last_visit[:10], "%Y-%m-%d")
                months = (datetime.utcnow() - lv_date).days // 30
                facts["months_since_last_visit"] = months
            except Exception:
                facts["months_since_last_visit"] = None
        else:
            facts["months_since_last_visit"] = None
    else:
        facts["months_since_last_visit"] = None

    # 6. Seasonal beat (current month)
    current_month = datetime.utcnow().strftime("%B")
    beats = category.get("seasonal_beats", [])
    seasonal = next(
        (b for b in beats if current_month[:3].lower() in b.get("month", "").lower()),
        None,
    )
    facts["seasonal_beat"] = seasonal.get("note") if seasonal else None

    # 7. Top trend signal (highest delta_yoy)
    trends = category.get("trend_signals", [])
    if trends:
        def _delta(t):
            raw = str(t.get("delta_yoy", "0")).replace("%", "").replace("+", "").strip()
            try:
                return float(raw)
            except Exception:
                return 0.0
        top_trend = max(trends, key=_delta)
        facts["top_trend_signal"] = f'{top_trend.get("topic", "")} {top_trend.get("delta_yoy", "")}'
    else:
        facts["top_trend_signal"] = None

    # 8. Pre-formatted citation for research/regulation triggers
    kind = trigger.get("kind", "")
    digest = category.get("digest", [])
    top_item_id = trigger.get("payload", {}).get("top_item_id")
    top_digest = None
    if top_item_id:
        top_digest = next((d for d in digest if d.get("id") == top_item_id), None)
    if top_digest is None and digest:
        top_digest = digest[0]
    if kind in ("research_digest", "regulation_change") and top_digest:
        source = top_digest.get("source", "")
        title = top_digest.get("title", "")
        page = top_digest.get("page", "")
        citation = f"[{source}] {title}"
        if page:
            citation += f", p.{page}"
        facts["formatted_citation"] = citation
    else:
        facts["formatted_citation"] = None

    # 9. Supply alert: pre-computed affected patient estimate
    if kind == "supply_alert":
        active_count = cust_agg.get("active_count") or 0
        payload_counts = trigger.get("payload", {})
        direct = payload_counts.get("affected_patient_count") or payload_counts.get("affected_customers")
        if direct:
            facts["supply_alert_patient_estimate"] = int(direct)
        elif active_count:
            facts["supply_alert_patient_estimate"] = max(1, round(int(active_count) * 0.15))
        else:
            facts["supply_alert_patient_estimate"] = None
    else:
        facts["supply_alert_patient_estimate"] = None

    return facts


_CTA_FORCED = {
    "recall_due": "reply_1_2",
    "appointment_tomorrow": "none",
}
_CTA_OPEN_ENDED_KINDS = {"research_digest", "cde_opportunity", "curious_ask_due"}
_CTA_ACTION_KINDS = {
    "perf_dip", "perf_spike", "renewal_due", "winback_eligible", "regulation_change",
    "dormant_with_vera", "competitor_opened", "active_planning_intent", "seasonal_perf_dip",
    "customer_lapsed_hard", "trial_followup", "supply_alert", "chronic_refill_due",
    "gbp_unverified", "customer_lapsed_soft", "intent_yes_followthrough",
    "review_theme_emerged", "festival_upcoming", "milestone_reached",
    "wedding_package_followup", "category_seasonal",
}
_VALID_CTA = {"open_ended", "yes_stop", "none", "reply_1_2"}


def _coerce_output(output: dict, trigger: dict) -> dict:
    kind = trigger.get("kind", "")
    scope = trigger.get("scope", "merchant")

    # Fix 1: send_as — customer scope must always be merchant_on_behalf
    if scope == "customer":
        output["send_as"] = "merchant_on_behalf"
    elif output.get("send_as") not in ("vera", "merchant_on_behalf"):
        output["send_as"] = "vera"

    # Fix 2: CTA coercion
    cta = output.get("cta", "")
    if kind in _CTA_FORCED:
        output["cta"] = _CTA_FORCED[kind]
    elif kind in _CTA_OPEN_ENDED_KINDS and cta not in _VALID_CTA:
        output["cta"] = "open_ended"
    elif kind in _CTA_ACTION_KINDS and cta not in _VALID_CTA:
        output["cta"] = "yes_stop"
    elif cta not in _VALID_CTA:
        output["cta"] = "yes_stop"

    # Fix 3: suppression_key — generate one if missing or empty
    if not output.get("suppression_key", "").strip():
        output["suppression_key"] = f"{kind}:{trigger.get('id', trigger.get('suppression_key', 'auto'))}"

    # Fix 4: recall_due body must end with exact slot footer
    if kind == "recall_due":
        slots = trigger.get("payload", {}).get("available_slots", [])
        if len(slots) >= 2:
            def _slot_label(s):
                return s if isinstance(s, str) else s.get("label", s.get("time", str(s)))
            s1, s2 = _slot_label(slots[0]), _slot_label(slots[1])
            footer = f"Reply 1 for {s1}, Reply 2 for {s2}"
            body = output.get("body", "").rstrip()
            if footer not in body:
                output["body"] = body + f"\n{footer}"

    return output


def _call_llm(user_prompt: str) -> str:
    # Primary: OpenAI
    openai_client = _get_openai_client()
    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=600,
                temperature=0,
                timeout=OPENAI_TIMEOUT,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[WARN] OpenAI failed, falling back to Groq: {e}", file=sys.stderr)

    # Fallback: Groq
    response = _get_groq_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=600,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def compose(
    category_payload: dict,
    merchant_payload: dict,
    trigger_payload: dict,
    customer_payload: dict | None = None,
    conversation_history: list | None = None,
) -> dict:
    if conversation_history is None:
        conversation_history = []

    facts = extract_facts(category_payload, merchant_payload, trigger_payload, customer_payload)
    user_prompt = get_user_prompt(
        category_payload,
        merchant_payload,
        trigger_payload,
        customer_payload,
        conversation_history,
        facts,
    )

    raw = _call_llm(user_prompt)

    # Strip markdown fences if model added them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    def _parse(text: str) -> dict:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)

    try:
        result = _parse(raw)
    except json.JSONDecodeError:
        retry_prompt = user_prompt + "\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no explanation."
        result = _parse(_call_llm(retry_prompt))

    _coerce_output(result, trigger_payload)

    # Validation retry: if output fails validation, retry once with the error surfaced
    try:
        validate(result, merchant_payload, category_payload, conversation_history)
    except Exception as ve:
        fix_prompt = (
            user_prompt
            + f"\n\nCRITICAL: Your previous output failed validation — {ve}. "
            "Fix only that issue and return ONLY valid JSON."
        )
        try:
            retried = _parse(_call_llm(fix_prompt))
            _coerce_output(retried, trigger_payload)
            result = retried
        except Exception:
            pass  # Return best attempt; caller's validate() is the final gate

    return result
