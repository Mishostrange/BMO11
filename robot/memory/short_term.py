from collections import deque
from typing import List, Dict, Optional, Tuple


class ShortTermMemory:
    """In-memory rolling buffer for the current session's conversation context."""

    def __init__(self, max_turns: int = 10, emotion_window: int = 8):
        self.max_turns = max_turns
        self.messages: List[Dict[str, str]] = []
        self.current_activity: str = "casual_conversation"
        self.activity_state: dict = {}

        # Linked child for MemoryManager coordination
        self.active_child_id: Optional[int] = None

        # Rolling emotion history — stores (emotion, confidence) tuples
        self.recent_emotions: deque = deque(maxlen=emotion_window)

    # ── Conversation turns ────────────────────────────────────────────────────

    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant_message(self, text: str):
        self.messages.append({"role": "assistant", "content": text})
        self._trim()

    def get_messages(self) -> List[Dict[str, str]]:
        return list(self.messages)

    # ── Activity tracking ─────────────────────────────────────────────────────

    def set_activity(self, activity_type: str, state: dict = None):
        """Update what the child and robot are currently doing."""
        self.current_activity = activity_type
        if state:
            self.activity_state.update(state)
        else:
            self.activity_state = {}

    def get_context_string(self) -> str:
        """Get a concise string describing the current short-term state."""
        ctx = f"Current Activity: {self.current_activity}"
        if self.activity_state:
            ctx += f" | State: {self.activity_state}"
        dom_emotion, _ = self.get_dominant_emotion()
        if dom_emotion and dom_emotion != "neutral":
            ctx += f" | Recent mood: {dom_emotion}"
        return ctx

    # ── Emotion tracking ──────────────────────────────────────────────────────

    def set_emotion(self, emotion: str, confidence: float):
        """Record a new emotion observation into the rolling window."""
        self.recent_emotions.append((emotion, confidence))

    def get_dominant_emotion(self) -> Tuple[Optional[str], float]:
        """
        Return the most frequently detected emotion in the recent window
        and its average confidence.  Returns ('neutral', 0.0) if empty.
        """
        if not self.recent_emotions:
            return "neutral", 0.0

        counts: Dict[str, float] = {}
        conf_sum: Dict[str, float] = {}
        for emotion, conf in self.recent_emotions:
            counts[emotion]   = counts.get(emotion, 0) + 1
            conf_sum[emotion] = conf_sum.get(emotion, 0.0) + conf

        dominant = max(counts, key=counts.__getitem__)
        avg_conf = conf_sum[dominant] / counts[dominant]
        return dominant, round(avg_conf, 2)

    def is_sustained_frustration(self, threshold: int = 3) -> bool:
        """True if the last `threshold` emotions are all negative."""
        if len(self.recent_emotions) < threshold:
            return False
        negative = {"angry", "sad", "frustrated", "scared"}
        last_n = list(self.recent_emotions)[-threshold:]
        return all(e in negative for e, _ in last_n)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _trim(self):
        """Keep only the last max_turns pairs (user + assistant)."""
        max_messages = self.max_turns * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def clear(self):
        """Clear the memory, typically at the start of a new session."""
        self.messages = []
        self.current_activity = "casual_conversation"
        self.activity_state = {}
        self.active_child_id = None
        self.recent_emotions.clear()

