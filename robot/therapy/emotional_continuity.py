"""
robot/therapy/emotional_continuity.py
──────────────────────────────────────────────────────────────────────────────
Emotional Continuity Engine — fully Python-based, NOT delegated to the LLM.

Tracks:
  - current_emotion
  - previous_emotion
  - emotional_trend (improving / worsening / stable / shifting)

The LLM only receives a short natural-language summary of this state.
TherapyEngine reads this engine before building any LLM prompt.
"""

import logging
import time
from collections import deque
from typing import Dict, Optional, Tuple

from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

NEGATIVE = {"angry", "sad", "scared", "frustrated", "anxious"}
POSITIVE  = {"happy", "excited", "surprised"}
NEUTRAL   = {"neutral", "calm"}


class EmotionalContinuityEngine:
    """
    Single source of truth for the child's emotional state.
    All emotion data flows in via the 'emotion.detected' event.
    """

    def __init__(self, window_size: int = 10):
        self._window: deque = deque(maxlen=window_size)
        self._current_emotion: str = "neutral"
        self._previous_emotion: str = "neutral"
        self._trend: str = "stable"
        self._last_update: float = 0.0

        # Subscribe to emotion events so this stays up-to-date automatically
        event_bus.subscribe("emotion.detected", self._on_emotion)
        event_bus.subscribe("session.started",  self._on_session_started)

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _on_session_started(self, _event: str, _data: dict):
        """Reset on a new session so we start fresh."""
        self._window.clear()
        self._current_emotion = "neutral"
        self._previous_emotion = "neutral"
        self._trend = "stable"
        logger.debug("[EmotionalContinuity] Reset for new session.")

    async def _on_emotion(self, _event: str, data: dict):
        emotion    = data.get("emotion", "neutral")
        confidence = data.get("confidence", 0.5)

        # Low-confidence reading: only accept if it matches current trend
        if confidence < 0.35 and emotion != self._current_emotion:
            return

        self._window.append(emotion)
        self._update_state()
        self._last_update = time.time()
        logger.debug(
            f"[EmotionalContinuity] {self._previous_emotion} → {self._current_emotion} "
            f"(trend={self._trend})"
        )

    # ── State computation ──────────────────────────────────────────────────────

    def _update_state(self):
        if not self._window:
            return

        old_current = self._current_emotion

        # New current = dominant in most recent half
        recent = list(self._window)
        half   = max(1, len(recent) // 2)
        new_current = self._dominant(recent[half:])
        new_previous = self._dominant(recent[:half]) if len(recent) >= 4 else self._current_emotion

        self._previous_emotion = new_previous
        self._current_emotion  = new_current
        self._trend            = self._compute_trend(new_previous, new_current)

    def _dominant(self, seq) -> str:
        if not seq:
            return "neutral"
        counts: Dict[str, int] = {}
        for e in seq:
            counts[e] = counts.get(e, 0) + 1
        return max(counts, key=counts.__getitem__)

    def _compute_trend(self, prev: str, curr: str) -> str:
        if prev == curr:
            return "stable"
        if curr in NEGATIVE and prev not in NEGATIVE:
            return "worsening"
        if curr in POSITIVE and prev not in POSITIVE:
            return "improving"
        return "shifting"

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def current_emotion(self) -> str:
        return self._current_emotion

    @property
    def previous_emotion(self) -> str:
        return self._previous_emotion

    @property
    def trend(self) -> str:
        return self._trend

    def is_negative(self) -> bool:
        return self._current_emotion in NEGATIVE

    def is_positive(self) -> bool:
        return self._current_emotion in POSITIVE

    def snapshot(self) -> Dict[str, str]:
        """Return a plain dict for logging or LLM context injection."""
        return {
            "current":  self._current_emotion,
            "previous": self._previous_emotion,
            "trend":    self._trend,
        }

    def build_context_line(self) -> str:
        """
        Build a short natural-language summary injected into the LLM system prompt.
        The LLM is NOT asked to compute the emotion — only to respond to it.
        """
        snap = self.snapshot()
        curr, trend = snap["current"], snap["trend"]

        if trend == "worsening":
            return (
                f"[EMOTIONAL CONTEXT] The child seems to be feeling {curr} and getting more upset. "
                f"PRIORITY: Acknowledge their feeling warmly before anything else. "
                f"Do NOT suggest games or tasks right now."
            )
        if trend == "improving":
            return (
                f"[EMOTIONAL CONTEXT] The child is starting to feel {curr}. "
                f"Build on this positive momentum gently."
            )
        if curr in NEGATIVE:
            return (
                f"[EMOTIONAL CONTEXT] The child is feeling {curr}. "
                f"Respond with empathy and calm support first."
            )
        if curr in POSITIVE:
            return (
                f"[EMOTIONAL CONTEXT] The child is feeling {curr}. "
                f"Match their energy with warmth and enthusiasm."
            )
        return f"[EMOTIONAL CONTEXT] The child seems calm and neutral. Continue naturally."


# Global singleton
emotion_engine = EmotionalContinuityEngine()
