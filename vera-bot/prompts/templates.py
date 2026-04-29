from datetime import datetime


def get_user_prompt(category, merchant, trigger, customer, conversation_history, facts):
    current_month = datetime.utcnow().strftime("%B")

    # --- CATEGORY CONTEXT ---
    voice = category.get("voice", {})
    peer_stats = category.get("peer_stats", {})
    digest_items = category.get("digest", [])
    seasonal_beats = category.get("seasonal_beats", [])
    trend_signals = category.get("trend_signals", [])

    # Prioritise digest item by trigger payload top_item_id
    top_item_id = trigger.get("payload", {}).get("top_item_id")
    if top_item_id:
        relevant_digest = [d for d in digest_items if d.get("id") == top_item_id]
        remaining = [d for d in digest_items if d.get("id") != top_item_id]
        relevant_digest = (relevant_digest + remaining)[:2]
    else:
        relevant_digest = digest_items[:2]

    relevant_beats = [b for b in seasonal_beats if current_month[:3].lower() in b.get("month", "").lower()]
    relevant_trends = trend_signals[:2]

    category_section = f"""### CATEGORY CONTEXT
Slug: {category.get('slug', '')}
Voice tone: {voice.get('tone', '')}
Taboos: {', '.join(voice.get('taboos', voice.get('vocab_taboo', [])))}
Peer stats: avg_rating={peer_stats.get('avg_rating', 'N/A')}, avg_ctr={peer_stats.get('avg_ctr', 'N/A')}, scope="{peer_stats.get('scope', '')}"

Digest items (top 2):
{chr(10).join(f'- [{d.get("source", "")}] {d.get("title", "")}' for d in relevant_digest) if relevant_digest else '- (none)'}

Seasonal beats (current month: {current_month}):
{chr(10).join(f'- {b.get("month", "")}: {b.get("note", "")}' for b in relevant_beats) if relevant_beats else '- (none this month)'}

Trend signals (top 2):
{chr(10).join(f'- {t.get("topic", "")}: {t.get("delta_yoy", "")} ({t.get("scope", "")})' for t in relevant_trends) if relevant_trends else '- (none)'}
"""

    # --- MERCHANT CONTEXT ---
    identity = merchant.get("identity", {})
    subscription = merchant.get("subscription", {})
    performance = merchant.get("performance", {})
    offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
    signals = merchant.get("signals", [])
    cust_agg = merchant.get("customer_aggregate", {})

    ctr = performance.get("ctr")
    peer_ctr = peer_stats.get("avg_ctr")
    ctr_line = ""
    if ctr is not None and peer_ctr is not None:
        gap = round(float(ctr) - float(peer_ctr), 4)
        ctr_line = f"CTR: {float(ctr)*100:.1f}% | peer median: {float(peer_ctr)*100:.1f}% | gap: {gap*100:+.1f}%"
    else:
        ctr_line = f"CTR: {ctr}" if ctr else "CTR: N/A"

    last_3 = conversation_history[-3:] if len(conversation_history) >= 3 else conversation_history
    history_str = "\n".join(
        f'[{t.get("from","?")}] {t.get("body","")}'
        for t in last_3
    ) if last_3 else "(no prior turns)"

    merchant_section = f"""### MERCHANT CONTEXT
Name: {identity.get('name', merchant.get('name', ''))}
Locality: {identity.get('locality', '')}, {identity.get('city', '')}
Languages: {', '.join(identity.get('languages', []))}
Owner: {identity.get('owner_first_name', '')}

Subscription: status={subscription.get('status', '')}, days_remaining={subscription.get('days_remaining', 'N/A')}, plan={subscription.get('plan', '')}

Performance (30d): views={performance.get('views', 'N/A')}, calls={performance.get('calls', 'N/A')}, directions={performance.get('directions', 'N/A')}
{ctr_line}
Delta 7d: {performance.get('delta_7d', 'N/A')}

Active offers:
{chr(10).join(f'- {o.get("title", o.get("name", ""))} @ {o.get("price", "")}' for o in offers) if offers else '- (none active)'}

Signals: {', '.join(str(s) for s in signals) if signals else '(none)'}

Customer aggregate: active={cust_agg.get('active_count', 'N/A')}, lapsed_180d_plus={cust_agg.get('lapsed_180d_plus', cust_agg.get('lapsed_count', 'N/A'))}, retention_6mo={cust_agg.get('retention_6mo', 'N/A')}

Last 3 conversation turns:
{history_str}
"""

    # --- TRIGGER CONTEXT ---
    trigger_section = f"""### TRIGGER CONTEXT
Kind: {trigger.get('kind', '')}
Source: {trigger.get('source', '')}
Urgency: {trigger.get('urgency', '')}
Expires at: {trigger.get('expires_at', '')}
Scope: {trigger.get('scope', '')}
Payload: {trigger.get('payload', {})}

Extracted facts:
{chr(10).join(f'- {k}: {v}' for k, v in facts.items() if v is not None)}
"""

    # --- CUSTOMER CONTEXT (optional) ---
    customer_section = ""
    if customer:
        cust_identity = customer.get("identity", {})
        relationship = customer.get("relationship", {})
        preferences = customer.get("preferences", {})
        consent = customer.get("consent", {})
        customer_section = f"""### CUSTOMER CONTEXT
Name: {cust_identity.get('name', '')}
Language preference: {cust_identity.get('language_pref', '')}
State: {customer.get('state', '')}

Relationship: last_visit={relationship.get('last_visit', 'N/A')}, visits_total={relationship.get('visits_total', 'N/A')}, services={relationship.get('services_received', [])}
Preferred slots: {preferences.get('preferred_slots', 'N/A')}
Consent scope: {consent.get('scope', [])}
"""

    scope = trigger.get("scope", "merchant")
    scope_note = "Send as vera (merchant-facing)" if scope == "merchant" else "Send as merchant_on_behalf (customer-facing)"
    first_msg_note = (
        "This is the FIRST message in this conversation — use WhatsApp template style "
        "(introduce Vera if vera scope, introduce clinic if merchant_on_behalf scope)."
        if len(conversation_history) == 0
        else "NOT the first message — do NOT re-introduce Vera."
    )

    task_section = f"""### TASK
Based on ALL the above, compose the next Vera message.

Category: {category.get('slug', '')}
Trigger kind: {trigger.get('kind', '')}
Scope: {scope} — {scope_note}

Conversation history so far: {len(conversation_history)} turns. {first_msg_note}

Return only the JSON object.
"""

    return "\n".join([
        category_section,
        merchant_section,
        trigger_section,
        customer_section,
        task_section,
    ])
