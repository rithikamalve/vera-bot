from datetime import datetime

# Common WhatsApp Business auto-reply phrases — detect on first occurrence
WA_AUTO_REPLY_PATTERNS = [
    "thank you for contacting",
    "thanks for contacting",
    "team will respond",
    "team will get back",
    "our team will",
    "automated message",
    "automated reply",
    "auto-reply",
    "autoresponder",
    "i am currently away",
    "i'm currently away",
    "currently unavailable",
    "unable to respond",
    "out of office",
]

INTENT_YES_TOKENS = [
    "yes", "ok", "okay", "go ahead", "karo", "haan", "bilkul", "sure",
    "let's do it", "chalega", "theek hai", "let us do it", "lets do it",
]
INTENT_NO_TOKENS = [
    "no", "nahi", "nope", "not interested", "stop", "mat karo",
]
WAIT_TOKENS = [
    "later", "baad mein", "busy", "will check", "thodi der mein", "abhi nahi",
]
QUESTION_STARTS = ["what", "how", "when", "why", "kya", "kaise", "kab", "kaun", "where", "which"]
HOSTILE_WORDS = [
    "idiot", "stupid", "bakwas", "bekar", "chutiya", "mc", "bc",
    "fraud", "scam", "fake", "harassment",
    # stop-contact variants
    "spam", "useless", "stop messaging", "stop sending",
    "don't contact", "dont contact", "don't message", "dont message",
    "do not contact", "do not message",
    "remove me", "remove my number", "delete my number",
    "unsubscribe", "opt out", "please stop",
    # Hindi/Hinglish
    "bakwaas", "band karo", "chup", "mat bhejo",
    "pareshaan mat", "disturb mat", "nahi chahiye",
    "annoying", "irritating", "leave me alone",
]


class ConversationFSM:

    def classify_reply(
        self, conversation_id: str, merchant_message: str, store, merchant_id: str = ""
    ) -> str:
        msg = merchant_message.strip()
        msg_lower = msg.lower()

        # Pattern-based detection: known WA Business auto-reply phrases → detect on turn 1
        if any(p in msg_lower for p in WA_AUTO_REPLY_PATTERNS):
            return "auto_reply"
        # Cross-conversation auto-reply: same message sent 2+ times by this merchant
        if merchant_id and store.get_merchant_message_count(merchant_id, msg) >= 2:
            return "auto_reply"
        # Within-conversation auto-reply fallback
        if store.is_auto_reply(conversation_id, msg):
            return "auto_reply"

        for word in HOSTILE_WORDS:
            if word in msg_lower:
                return "hostile"

        for token in INTENT_YES_TOKENS:
            if token in msg_lower:
                return "intent_yes"

        for token in INTENT_NO_TOKENS:
            if token in msg_lower:
                return "intent_no"

        if msg.endswith("?") or any(msg_lower.startswith(w) for w in QUESTION_STARTS):
            return "question"

        return "neutral"

    def _count_auto_replies(self, conversation_id: str, store, merchant_id: str = "") -> int:
        """Return how many times this merchant's current message has been seen (cross-conv)."""
        if merchant_id:
            history = store.get_history(conversation_id)
            current_msg = next(
                (t.get("body", "").strip() for t in reversed(history)
                 if t.get("from") in ("merchant", "customer")),
                ""
            )
            if current_msg:
                return store.get_merchant_message_count(merchant_id, current_msg)
            return 0
        # Fallback: count within-conversation repeated merchant messages
        history = store.get_history(conversation_id)
        seen: set[str] = set()
        count = 0
        for t in history:
            if t.get("from") in ("merchant", "customer"):
                body = t.get("body", "").strip()
                if body in seen:
                    count += 1
                seen.add(body)
        return count

    def _turn_count(self, conversation_id: str, store) -> int:
        return len(store.get_history(conversation_id))

    def handle_reply(
        self,
        conversation_id: str,
        merchant_message: str,
        merchant_payload: dict,
        category_payload: dict,
        store,
        compose_fn,
        merchant_id: str = "",
    ) -> dict:
        classification = self.classify_reply(
            conversation_id, merchant_message, store, merchant_id=merchant_id
        )
        history = store.get_history(conversation_id)

        # --- Bug 1: Auto-reply detection ---
        if classification == "auto_reply":
            auto_count = self._count_auto_replies(conversation_id, store, merchant_id=merchant_id)

            # 2nd+ confirmed auto-reply: exit immediately, no further API call
            if auto_count >= 3:
                return {
                    "action": "end",
                    "body": "",
                    "cta": "none",
                    "rationale": "Detected WA Business auto-reply twice; exiting gracefully",
                }

            # 1st confirmed auto-reply: one short targeted nudge for the owner (hardcoded, not composed —
            # the LLM ignores extra_instruction and generates full CTR content instead)
            owner = merchant_payload.get("identity", {}).get("owner_first_name", "")
            name_part = f" {owner}" if owner else ""
            return {
                "action": "send",
                "body": f"Looks like an auto-reply 😊 When you're free{name_part}, just reply YES and I'll pick up from where we left off.",
                "cta": "yes_stop",
                "rationale": "Detected WA Business auto-reply pattern; one short prompt to reach the real owner.",
            }

        # --- Bug 2: Intent YES — forward-action, no re-pitch ---
        if classification == "intent_yes":
            # Inject system hint so the LLM knows not to re-pitch
            modified_history = list(history) + [{
                "from": "system_hint",
                "body": (
                    "Merchant said YES. Move to action/confirmation mode. "
                    "Do NOT re-pitch. Do NOT repeat CTR stats or peer averages. "
                    "Acknowledge the yes and state the specific next step you are taking for them. "
                    "Be concrete about what happens next."
                ),
            }]
            trigger_payload = {
                "id": f"yes_{conversation_id}",
                "scope": "merchant",
                "kind": "intent_yes_followthrough",
                "source": "internal",
                "payload": {
                    "merchant_id": merchant_payload.get("merchant_id", ""),
                    "extra_instruction": (
                        "Merchant confirmed YES. Do NOT re-pitch. "
                        "Move straight to the action step."
                    ),
                },
                "urgency": 4,
                "suppression_key": f"yes:{conversation_id}",
                "expires_at": "",
            }
            try:
                result = compose_fn(
                    category_payload, merchant_payload, trigger_payload, None, modified_history
                )
                return {
                    "action": "send",
                    "body": result.get("body", ""),
                    "cta": result.get("cta", "none"),
                    "rationale": result.get("rationale", ""),
                }
            except Exception as e:
                return {
                    "action": "end",
                    "body": "",
                    "cta": "none",
                    "rationale": f"compose error on yes followthrough: {e}",
                }

        if classification == "intent_no":
            return {
                "action": "end",
                "body": "",
                "cta": "none",
                "rationale": "merchant declined; graceful exit",
            }

        # --- Bug 3: Hostile — end immediately, no API call ---
        if classification == "hostile":
            return {
                "action": "end",
                "body": "",
                "cta": "none",
                "rationale": "Merchant signaled hostility; exiting without escalating",
            }

        if classification == "neutral":
            msg_lower = merchant_message.lower()
            if any(t in msg_lower for t in WAIT_TOKENS):
                return {
                    "action": "wait",
                    "body": "",
                    "cta": "none",
                    "rationale": "merchant indicated they will check later; waiting",
                    "wait_seconds": 3600,
                }
            turn_count = self._turn_count(conversation_id, store)
            if turn_count >= 5:
                return {
                    "action": "end",
                    "body": "",
                    "cta": "none",
                    "rationale": "3-strike rule reached; graceful exit",
                }

        # question or neutral (compose next nudge)
        trigger_payload = {
            "id": f"reply_{conversation_id}",
            "scope": "merchant",
            "kind": "follow_up",
            "source": "internal",
            "payload": {
                "merchant_id": merchant_payload.get("merchant_id", ""),
                "merchant_message": merchant_message,
                "extra_instruction": (
                    "Merchant asked a question — answer directly and concisely."
                    if classification == "question"
                    else "Continue the conversation naturally."
                ),
            },
            "urgency": 3,
            "suppression_key": f"reply:{conversation_id}:{len(history)}",
            "expires_at": "",
        }
        try:
            result = compose_fn(category_payload, merchant_payload, trigger_payload, None, history)
            return {
                "action": "send",
                "body": result.get("body", ""),
                "cta": result.get("cta", "none"),
                "rationale": result.get("rationale", ""),
            }
        except Exception as e:
            return {
                "action": "end",
                "body": "",
                "cta": "none",
                "rationale": f"compose error: {e}",
            }
