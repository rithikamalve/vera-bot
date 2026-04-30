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
{chr(10).join(f'- [{d.get("source", "")}] {d.get("title", "")}{(" | trial_n=" + str(d["trial_n"])) if d.get("trial_n") else ""}{(" | patient_segment=" + d["patient_segment"]) if d.get("patient_segment") else ""}{(" | summary: " + d["summary"]) if d.get("summary") else ""}' for d in relevant_digest) if relevant_digest else '- (none)'}

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
    # Only show CTR/peer stats for merchant-facing messages — never expose to customers
    is_customer_scope = trigger.get("scope") == "customer"
    ctr_line = ""
    if not is_customer_scope:
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
    # Render delta_pct correctly: -0.50 means -50%, not -0.5%
    trigger_payload = trigger.get('payload', {})
    delta_pct_raw = trigger_payload.get('delta_pct')
    delta_pct_rendered = ""
    if delta_pct_raw is not None:
        try:
            delta_pct_rendered = f"\nMetric delta: {float(delta_pct_raw)*100:+.0f}% (e.g. -0.50 = -50% drop)"
        except Exception:
            pass

    trigger_section = f"""### TRIGGER CONTEXT
Kind: {trigger.get('kind', '')}
Source: {trigger.get('source', '')}
Urgency: {trigger.get('urgency', '')}
Expires at: {trigger.get('expires_at', '')}
Scope: {trigger.get('scope', '')}
Payload: {trigger_payload}{delta_pct_rendered}

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

    # Trigger-kind routing: tell the model what to lead with
    kind = trigger.get('kind', '')
    kind_instruction = {
        'research_digest':      'Lead with the specific research finding. Quote the exact numbers from the digest title (percentages, trial_n, patient_segment) — do not paraphrase them. Cite source at the end. Do NOT open with CTR or performance metrics.',
        'regulation_change':    'Lead with the compliance deadline and what specifically changed. Be precise about dates and requirements.',
        'perf_dip':             'Lead with the exact metric drop (use the "Metric delta" percentage above — e.g. -50%, not -0.50%). Name the specific metric (calls, views, CTR). Keep to 2-3 sentences. End with a single yes/stop CTA asking if they want you to act on it. Do NOT list other metrics or offers in the same message.',
        'perf_spike':           'Lead with the positive number and frame it as momentum to act on now.',
        'recall_due':           'Send as the merchant to the customer. State their recall is due, name the available slots from the payload, state the price from active offers. Do NOT include any research findings, clinical statistics, or internal metrics (CTR, peer median). No preamble. End with exactly: "Reply 1 for [slot1], Reply 2 for [slot2]".',
        'festival_upcoming':    'Lead with the festival name and days remaining. Connect to a specific offer or service.',
        'ipl_match_today':      'Check is_weeknight in the payload. If false (weekend), advise skipping the promo (IPL weekends shift footfall to home). If true, recommend a targeted offer.',
        'curious_ask_due':      'Ask a single short question about what service is most in demand this week. Offer to turn the answer into a Google post or WhatsApp reply.',
        'winback_eligible':     'Lead with what the merchant is missing since their subscription lapsed (lapsed customers added, performance delta). Single binary CTA.',
        'renewal_due':          'Lead with days remaining and what they risk losing (visibility, active customers, offers). Not a generic renewal pitch. Do NOT include research findings, clinical studies, digest content, or CTR peer comparisons — those are irrelevant here. End with yes/stop CTA.',
        'review_theme_emerged': 'Name the specific review theme and occurrence count. Offer a concrete action (response template, operational fix).',
        'dormant_with_vera':    'Short re-engagement. Reference something specific from their data. Single easy ask.',
        'intent_yes_followthrough': 'Merchant said YES. Do NOT re-pitch or repeat any stats/CTR/peer metrics. Acknowledge the yes in one short sentence, then state exactly what you are doing for them right now (drafting, scheduling, activating). Be concrete and brief.',
        'follow_up':            'Answer the merchant question directly and concisely. No re-introduction. One clear next step.',
        'auto_reply_nudge':     'One short sentence only. Ask if the owner is available. No stats, no CTR, no offers.',
        'competitor_opened':    'Name the proximity and what it means for their GBP visibility. Curiosity hook.',
        'milestone_reached':    'Celebrate the specific number. Connect to a next milestone or action.',
        'wedding_package_followup': 'Reference the trial date and exact days to wedding. Name the next concrete step (skin prep program). No preamble ("We hope you\'re doing great" etc). Do NOT push generic haircut/hair spa offers. End with a single binary booking ask.',
        # Expanded trigger kinds from the full dataset
        'active_planning_intent':   'Merchant expressed intent on the topic in the payload. Build directly on their last message — skip the re-pitch entirely. State in one sentence exactly what you are drafting or building for them right now. Keep to 2 sentences max. yes/stop CTA.',
        'seasonal_perf_dip':        'Check is_expected_seasonal in the payload. If true, frame the dip as a known seasonal pattern (name the season_note), then suggest one proactive action to counter it — do NOT alarm them. If false, treat as a real perf_dip: lead with exact metric and percentage drop.',
        'customer_lapsed_hard':     'Send as merchant to customer. State exactly how many days since their last visit (days_since_last_visit from payload). Reference their previous focus. Name the active offer price. Single yes/stop CTA. No preamble.',
        'trial_followup':           'Send as merchant to customer. Reference the trial date from the payload. Name the next available session using the label field from next_session_options. Single booking CTA.',
        'supply_alert':             'Lead with the specific molecule and affected batch numbers from the payload. Name the manufacturer. State the immediate action the merchant must take (check stock, quarantine affected batches, notify patients on this molecule). Urgency is high — no soft language. yes/stop CTA.',
        'chronic_refill_due':       'Send as merchant (pharmacy) to customer. Name the specific medications from molecule_list. State when stock runs out using stock_runs_out_iso. If delivery_address_saved is true, offer home delivery — do NOT invent prices, minimums, or charges not present in active offers. Single yes/stop CTA. No preamble.',
        'category_seasonal':        'Lead with the single highest-demand trend from the trends list (the one with the largest positive delta). Name one concrete shelf or menu action. If shelf_action_recommended is true, make the action specific. yes/stop CTA.',
        'gbp_unverified':           'Lead with the visibility uplift they are missing (estimated_uplift_pct × 100 = percentage). Name the verification method (verification_path). One sentence on what unverified status costs them right now. yes/stop CTA.',
        'cde_opportunity':          'Lead with the credit hours and cost (if fee is free_for_members say "no cost for you"). Name one professional benefit of the credits. Curiosity hook to learn more. open_ended CTA.',
        'customer_lapsed_soft':     'Send as merchant to customer. Use their last_visit date and services_received from customer relationship context. Warm, short re-engagement — reference one specific service they received. Name the active offer. Single booking CTA.',
        'appointment_tomorrow':     'Send as merchant to customer. Confirm their appointment is tomorrow. Name the service and time if available in context. Add one brief category-appropriate preparation note. No CTA — this is a confirmation message, cta="none".',
    }.get(kind, (
        'Lead with the most specific and verifiable fact from the trigger payload. '
        'Do NOT reference category digest items, research papers, or clinical findings. '
        'Apply one compulsion lever (loss_aversion or specificity). End with a yes/stop CTA.'
    ))

    # Fix 1: Context isolation — block digest/research bleed for non-research trigger kinds
    research_kinds = {'research_digest', 'regulation_change', 'trend_signal'}
    if kind not in research_kinds:
        isolation_rule = (
            "ISOLATION RULE: Do NOT reference category digest items, research papers, "
            "clinical statistics, seasonal beats, or trend signals in this message. "
            "Pull facts only from the trigger payload and merchant context (offers, performance, customer aggregate)."
        )
    else:
        isolation_rule = (
            "RESEARCH RULE: Lead with the digest finding. "
            "All numbers must come verbatim from the digest item — do not round or paraphrase."
        )

    task_section = f"""### TASK
Based on ALL the above, compose the next Vera message.

Category: {category.get('slug', '')}
Trigger kind: {kind}
Scope: {scope} — {scope_note}

TRIGGER ROUTING INSTRUCTION: {kind_instruction}

{isolation_rule}

COMPULSION REQUIREMENT: You MUST apply exactly one lever from this list and name it in your rationale: specificity | loss_aversion | social_proof | effort_externalization | curiosity | reciprocity | binary_commit

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
