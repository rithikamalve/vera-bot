from threading import Lock


class ContextStore:
    def __init__(self):
        self._lock = Lock()
        # (scope, context_id) -> {"version": int, "payload": dict}
        self._contexts: dict[tuple, dict] = {}
        # conversation_id -> list of turns
        self._conversations: dict[str, list] = {}
        # suppression keys
        self._suppressed: set[str] = set()
        # merchant_id -> {normalized_message -> count} for cross-conversation auto-reply detection
        self._merchant_msg_counts: dict[str, dict[str, int]] = {}

    # --- context store ---

    def get(self, scope: str, context_id: str):
        entry = self._contexts.get((scope, context_id))
        return entry["payload"] if entry else None

    def get_version(self, scope: str, context_id: str):
        entry = self._contexts.get((scope, context_id))
        return entry["version"] if entry else None

    def set(self, scope: str, context_id: str, version: int, payload: dict) -> str:
        key = (scope, context_id)
        with self._lock:
            existing = self._contexts.get(key)
            if existing is None:
                self._contexts[key] = {"version": version, "payload": payload}
                return "updated"
            if existing["version"] == version:
                return "accepted"
            if version > existing["version"]:
                self._contexts[key] = {"version": version, "payload": payload}
                return "updated"
            return "stale"

    def list_by_scope(self, scope: str) -> list:
        return [
            v["payload"]
            for (s, _), v in self._contexts.items()
            if s == scope
        ]

    def count_by_scope(self) -> dict:
        counts: dict[str, int] = {}
        for (scope, _) in self._contexts:
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    # --- conversation store ---

    def add_turn(self, conversation_id: str, turn: dict):
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = []
            self._conversations[conversation_id].append(turn)

    def get_history(self, conversation_id: str) -> list:
        return self._conversations.get(conversation_id, [])

    def is_auto_reply(self, conversation_id: str, message: str) -> bool:
        """True if this message is identical to any prior merchant turn in this conversation.
        Requires count >= 2 because the current turn is already in history when this is called."""
        msg_normalized = message.strip().lower()
        if not msg_normalized:
            return False
        history = self.get_history(conversation_id)
        count = sum(
            1 for t in history
            if t.get("from") in ("merchant", "customer")
            and t.get("body", "").strip().lower() == msg_normalized
        )
        return count >= 2

    def record_merchant_message(self, merchant_id: str, message: str):
        """Track how many times this merchant has sent this exact message (across all convs)."""
        key = message.strip()
        if not key:
            return
        with self._lock:
            if merchant_id not in self._merchant_msg_counts:
                self._merchant_msg_counts[merchant_id] = {}
            self._merchant_msg_counts[merchant_id][key] = (
                self._merchant_msg_counts[merchant_id].get(key, 0) + 1
            )

    def get_merchant_message_count(self, merchant_id: str, message: str) -> int:
        """Return how many times this merchant has sent this exact message."""
        return self._merchant_msg_counts.get(merchant_id, {}).get(message.strip(), 0)

    def get_all_conversations(self) -> dict:
        return dict(self._conversations)

    # --- suppression store ---

    def is_suppressed(self, key: str) -> bool:
        return key in self._suppressed

    def suppress(self, key: str):
        with self._lock:
            self._suppressed.add(key)

    # --- teardown ---

    def clear(self):
        with self._lock:
            self._contexts.clear()
            self._conversations.clear()
            self._suppressed.clear()
            self._merchant_msg_counts.clear()
