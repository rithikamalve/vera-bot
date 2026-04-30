import json
import os
import sys
from datetime import datetime

from groq import Groq

from prompts.base import SYSTEM_PROMPT
from prompts.templates import get_user_prompt

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

    return facts


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

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Retry once with explicit JSON instruction
        retry_prompt = user_prompt + "\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no explanation."
        raw2 = _call_llm(retry_prompt)
        if raw2.startswith("```"):
            raw2 = raw2.split("```")[1]
            if raw2.startswith("json"):
                raw2 = raw2[4:]
            raw2 = raw2.strip()
        result = json.loads(raw2)

    return result
