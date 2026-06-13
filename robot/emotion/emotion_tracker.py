"""
emotion/emotion_tracker.py
─────────────────────────────────────────────────────────────────────────────
Session-level emotion state machine and DB persistence layer.

Responsibilities
────────────────
• Maintain a running emotion timeline for the current session.
• Detect *sustained* emotional states (e.g. 3 consecutive 'angry' detections).
• Calculate session-level mood summary (mood_start / mood_end / dominant_mood).
• Persist per-minute mood snapshots to the DB for dashboard trend charts.
• Emit 'emotion.sustained' events so TherapyEngine can react.
"""

import asyncio
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple

from robot.database.connection import db
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

NEGATIVE_EMOTIONS = {"angry", "sad", "scared", "frustrated"}
POSITIVE_EMOTIONS = {"happy", "excited"}
NEUTRAL_EMOTIONS  = {"neutral"}

# How many consecutive same-emotion readings to trigger 'sustained' event
# Set to 5 to avoid comfort mode being triggered by 1-2 noisy readings
SUSTAINED_THRESHOLD = 5


class EmotionTracker:
    """
    Maintains a rolling emotion timeline for the ongoing session.
    Fires 'emotion.sustained' when an emotion persists for SUSTAINED_THRESHOLD readings.
    """

    def __init__(self, snapshot_interval_seconds: int = 60):
        self.snapshot_interval = snapshot_interval_seconds

        self.active_child_id:   Optional[int] = None
        self.active_session_id: Optional[int] = None

        # Timeline: list of (emotion, confidence, timestamp)
        self.timeline: List[Tuple[str, float, float]] = []

        # Per-session summary fields
        self.mood_start:   Optional[str] = None
        self.mood_end:     Optional[str] = None
        self.last_snapshot_time: float   = 0.0

        # Running deque for sustained-detection
        self._recent: deque = deque(maxlen=SUSTAINED_THRESHOLD)
        self._last_sustained_event: Optional[str] = None

        # Subscribe to fused emotion events
        event_bus.subscribe("emotion.detected",   self._on_emotion)
        event_bus.subscribe("session.started",    self._on_session_started)
        event_bus.subscribe("session.ended",      self._on_session_ended)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_session_started(self, _event: str, data: Dict):
        self.active_child_id   = data.get("child_id")
        self.active_session_id = data.get("session_id")
        self.timeline.clear()
        self._recent.clear()
        self.mood_start = None
        self.mood_end   = None
        self._last_sustained_event = None
        self.last_snapshot_time = asyncio.get_event_loop().time()
        logger.debug("[EmotionTracker] Session started, tracker reset.")

    async def _on_session_ended(self, _event: str, data: Dict):
        await self._take_snapshot()
        summary = self.get_session_summary()
        await event_bus.publish("emotion.session_summary", summary)
        self.active_child_id   = None
        self.active_session_id = None

    async def _on_emotion(self, _event: str, data: Dict):
        emotion    = data.get("emotion", "neutral")
        confidence = data.get("confidence", 0.5)
        now        = asyncio.get_event_loop().time()

        # Record to timeline
        self.timeline.append((emotion, confidence, now))

        # Set mood_start (first reading of session)
        if self.mood_start is None:
            self.mood_start = emotion

        self.mood_end = emotion  # continuously updated

        # Rolling deque for sustained detection
        self._recent.append(emotion)
        if len(self._recent) == SUSTAINED_THRESHOLD:
            if len(set(self._recent)) == 1:  # all same
                sustained = self._recent[0]
                if sustained != self._last_sustained_event:
                    self._last_sustained_event = sustained
                    logger.info(f"[EmotionTracker] Sustained emotion: {sustained}")
                    await event_bus.publish("emotion.sustained", {
                        "emotion":        sustained,
                        "child_id":       self.active_child_id,
                        "session_id":     self.active_session_id,
                        "is_negative":    sustained in NEGATIVE_EMOTIONS,
                        "is_positive":    sustained in POSITIVE_EMOTIONS,
                    })

        # Periodic snapshot
        if (now - self.last_snapshot_time) >= self.snapshot_interval:
            await self._take_snapshot()
            self.last_snapshot_time = now

    # ── Snapshot & Summary ────────────────────────────────────────────────────

    async def _take_snapshot(self):
        """Persist a mood snapshot to DB for dashboard timeline charts."""
        if not self.active_child_id or not self.timeline:
            return

        dominant, avg_conf = self._dominant_in_window(self.timeline[-20:])

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._write_snapshot, dominant, avg_conf)

    def _write_snapshot(self, emotion: str, confidence: float):
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO emotion_log
                        (child_id, session_id, source, emotion, confidence)
                    VALUES (?, ?, 'snapshot', ?, ?)
                    """,
                    (self.active_child_id, self.active_session_id, emotion, confidence),
                )
        except Exception as e:
            logger.error(f"[EmotionTracker] Snapshot DB error: {e}")

    def get_session_summary(self) -> Dict:
        """Return a dict summarising the emotional arc of the session."""
        if not self.timeline:
            return {
                "mood_start": self.mood_start or "neutral",
                "mood_end":   self.mood_end   or "neutral",
                "dominant":   "neutral",
                "positive_pct": 0.0,
                "negative_pct": 0.0,
            }

        dominant, _ = self._dominant_in_window(self.timeline)
        total = len(self.timeline)

        pos_count = sum(1 for e, _, _ in self.timeline if e in POSITIVE_EMOTIONS)
        neg_count = sum(1 for e, _, _ in self.timeline if e in NEGATIVE_EMOTIONS)

        return {
            "mood_start":    self.mood_start or "neutral",
            "mood_end":      self.mood_end   or "neutral",
            "dominant":      dominant,
            "positive_pct":  round(pos_count / total * 100, 1),
            "negative_pct":  round(neg_count / total * 100, 1),
            "total_readings": total,
        }

    # ── Dashboard queries ─────────────────────────────────────────────────────

    def get_emotion_timeline_db(
        self, child_id: int, session_id: int
    ) -> List[Dict]:
        """Return all emotion log rows for a session (for charting)."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT emotion, confidence, timestamp
                    FROM emotion_log
                    WHERE child_id=? AND session_id=?
                    ORDER BY timestamp ASC
                    """,
                    (child_id, session_id),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[EmotionTracker] timeline DB error: {e}")
            return []

    def get_weekly_mood_distribution(self, child_id: int) -> Dict[str, int]:
        """Emotion counts over the last 7 days for the dashboard pie chart."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT emotion, COUNT(*) as cnt
                    FROM emotion_log
                    WHERE child_id=?
                      AND timestamp >= datetime('now', '-7 days')
                      AND source != 'snapshot'
                    GROUP BY emotion
                    """,
                    (child_id,),
                )
                return {r["emotion"]: r["cnt"] for r in cursor.fetchall()}
        except Exception as e:
            logger.error(f"[EmotionTracker] weekly mood error: {e}")
            return {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _dominant_in_window(
        window: List[Tuple[str, float, float]]
    ) -> Tuple[str, float]:
        """Find dominant emotion + avg confidence in a timeline slice."""
        if not window:
            return "neutral", 0.5
        counts: Dict[str, int]   = {}
        conf:   Dict[str, float] = {}
        for emotion, confidence, _ in window:
            counts[emotion] = counts.get(emotion, 0) + 1
            conf[emotion]   = conf.get(emotion, 0.0) + confidence
        dominant = max(counts, key=counts.__getitem__)
        return dominant, round(conf[dominant] / counts[dominant], 2)
