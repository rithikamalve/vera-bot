VALID_CTA = {"open_ended", "yes_stop", "none", "reply_1_2"}
VALID_SEND_AS = {"vera", "merchant_on_behalf"}

MULTI_CTA_PHRASES = ["YES", "STOP", "Reply 1", "Reply 2"]


def validate(
    output: dict,
    merchant_payload: dict,
    category_payload: dict,
    conversation_history: list,
) -> None:
    body = output.get("body", "")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("body must be a non-empty string")

    cta = output.get("cta", "")
    if cta not in VALID_CTA:
        raise ValueError(f"cta must be one of {VALID_CTA}, got: {cta!r}")

    send_as = output.get("send_as", "")
    if send_as not in VALID_SEND_AS:
        raise ValueError(f"send_as must be one of {VALID_SEND_AS}, got: {send_as!r}")

    suppression_key = output.get("suppression_key", "")
    if not isinstance(suppression_key, str) or not suppression_key.strip():
        raise ValueError("suppression_key must be a non-empty string")

    rationale = output.get("rationale", "")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale must be a non-empty string")

    # Taboo word check
    taboos = category_payload.get("voice", {}).get("vocab_taboo", [])
    taboos += category_payload.get("voice", {}).get("taboos", [])
    body_lower = body.lower()
    for word in taboos:
        if word.lower() in body_lower:
            raise ValueError(f"taboo word detected in body: {word!r}")

    # Repeat body check
    for turn in conversation_history:
        if turn.get("from") == "vera" and turn.get("body", "") == body:
            raise ValueError("repeat body detected")

    # Multiple CTA check
    detected = sum(1 for phrase in MULTI_CTA_PHRASES if phrase in body)
    if detected > 2:
        raise ValueError("multiple CTAs detected")
