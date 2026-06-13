"""
analytics/progress_tracker.py
─────────────────────────────────────────────────────────────────────────────
Comprehensive skill-based progress tracking for autistic children.

Skill Domains Tracked
─────────────────────
• speech       – articulation, vocabulary, sentence length
• social       – turn-taking, eye contact (engagement), conversation initiations
• emotional    – correct emotion identification, self-regulation incidents
• attention    – time-on-task, barge-in count, focus game scores
• motor        – (placeholder, touch-screen interaction latency)

Progress Model
──────────────
Each domain has a rolling_score (0-100) that blends:
    score_t = α * new_observation + (1-α) * score_{t-1}
where α = 0.25 (responds to change within ~4 sessions).

A "skill level" (1-5) is derived from the rolling score:
    1: 0-20   2: 21-40   3: 41-60   4: 61-80   5: 81-100
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

from robot.database.connection import db
from robot.services.event_bus  import event_bus

logger = logging.getLogger(__name__)

ALPHA = 0.25   # EMA smoothing factor

DOMAIN_LABELS = {
    "speech":    "Speech & Language",
    "social":    "Social Skills",
    "emotional": "Emotional Awareness",
    "attention": "Attention & Focus",
}


def _score_to_level(score: float) -> int:
    """Map 0-100 rolling score to skill level 1-5."""
    if score <= 20: return 1
    if score <= 40: return 2
    if score <= 60: return 3
    if score <= 80: return 4
    return 5


class ProgressTracker:
    """
    Tracks, persists, and exposes multi-domain skill progress for each child.
    """

    def __init__(self):
        event_bus.subscribe("game.finished",       self._on_game_finished)
        event_bus.subscribe("emotion.detected",    self._on_emotion_detected)
        event_bus.subscribe("engagement.update",   self._on_engagement)
        event_bus.subscribe("speech.transcribed",  self._on_speech_transcribed)
        event_bus.subscribe("emotion.sustained",   self._on_sustained_emotion)
        event_bus.subscribe("session.ended",       self._on_session_ended)

        # In-memory per-session counters (reset on session start)
        self._session: Dict[str, Any] = {}

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def start_session(self, child_id: int, session_id: int):
        self._session = {
            "child_id":          child_id,
            "session_id":        session_id,
            "words_spoken":      0,
            "longest_utterance": 0,
            "correct_emotions":  0,
            "total_emotions":    0,
            "engaged_ticks":     0,
            "total_ticks":       0,
            "frustration_events": 0,
            "game_scores":       {},   # game_type → [scores]
        }

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_game_finished(self, _event: str, data: Dict):
        child_id   = data.get("child_id") or self._session.get("child_id")
        game_type  = data.get("game_type", "unknown")
        score      = float(data.get("score", 0.0))
        correct    = int(data.get("correct_count", 0))
        total      = int(data.get("total_count", 1))
        difficulty = int(data.get("difficulty", 1))

        if not child_id:
            return

        # Accumulate in session dict
        self._session.setdefault("game_scores", {})
        self._session["game_scores"].setdefault(game_type, []).append(score)

        # Map game → domain
        domain = self._game_to_domain(game_type)
        pct    = (correct / max(total, 1)) * 100

        await self._update_domain_score(child_id, domain, pct)
        self._persist_game_result(child_id, game_type, score, correct, total, difficulty)

    async def _on_emotion_detected(self, _event: str, data: Dict):
        """Track emotion identification accuracy (only from 'emotions' game)."""
        # This generic handler just counts; the emotions game
        # fires 'game.finished' which maps to the emotional domain.
        pass

    async def _on_engagement(self, _event: str, data: Dict):
        """Track attention via engagement sensor."""
        if "total_ticks" not in self._session:
            return
        self._session["total_ticks"] += 1
        if data.get("engaged"):
            self._session["engaged_ticks"] += 1

    async def _on_speech_transcribed(self, _event: str, text: str):
        """Track vocabulary and utterance length."""
        if "words_spoken" not in self._session:
            return
        words = len(text.split())
        self._session["words_spoken"] += words
        self._session["longest_utterance"] = max(
            self._session["longest_utterance"], words
        )

    async def _on_sustained_emotion(self, _event: str, data: Dict):
        """Count sustained frustration/distress events."""
        if data.get("is_negative"):
            self._session["frustration_events"] = (
                self._session.get("frustration_events", 0) + 1
            )

    async def _on_session_ended(self, _event: str, data: Dict):
        """Flush session-level aggregates to DB and update domain scores."""
        child_id   = self._session.get("child_id")
        session_id = self._session.get("session_id")
        if not child_id:
            return

        # ── Attention score ──────────────────────────────────────────────────
        total_ticks   = max(self._session.get("total_ticks", 1), 1)
        engaged_ticks = self._session.get("engaged_ticks", 0)
        attention_pct = (engaged_ticks / total_ticks) * 100
        await self._update_domain_score(child_id, "attention", attention_pct)

        # ── Speech score from utterance stats ────────────────────────────────
        words     = self._session.get("words_spoken", 0)
        longest   = self._session.get("longest_utterance", 0)
        # Heuristic: 50+ words spoken and 5+ word utterances → good speech session
        speech_pct = min(100.0, (words / 50.0) * 50 + (longest / 5.0) * 50)
        await self._update_domain_score(child_id, "speech", speech_pct)

        # ── Social score via turn-taking and conversation length ──────────────
        total_turns = len(self._session.get("game_scores", {}).get("turn_taking", []))
        social_pct  = min(100.0, total_turns * 25)
        if social_pct > 0:
            await self._update_domain_score(child_id, "social", social_pct)

        # Persist session summary fields
        self._update_session_record(
            session_id,
            attention_score=round(attention_pct / 100, 2),
            speech_score=round(speech_pct / 100, 2),
            mood_end=data.get("mood_end"),
        )

    # ── Domain score computation ──────────────────────────────────────────────

    async def _update_domain_score(
        self, child_id: int, domain: str, new_observation: float
    ):
        """
        Apply EMA update to the child's rolling domain score and persist it.
        Also checks for level-up events.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, self._ema_update_sync, child_id, domain, new_observation
        )

    def _ema_update_sync(
        self, child_id: int, domain: str, new_obs: float
    ):
        try:
            with db.get_cursor() as cursor:
                # Read current score
                cursor.execute(
                    "SELECT score, skill_level FROM skill_scores WHERE child_id=? AND domain=?",
                    (child_id, domain),
                )
                row = cursor.fetchone()

                if row:
                    old_score = row["score"]
                    old_level = row["skill_level"]
                    new_score = ALPHA * new_obs + (1 - ALPHA) * old_score
                    new_level = _score_to_level(new_score)

                    cursor.execute(
                        """
                        UPDATE skill_scores
                        SET score=?, skill_level=?, last_updated=CURRENT_TIMESTAMP
                        WHERE child_id=? AND domain=?
                        """,
                        (round(new_score, 2), new_level, child_id, domain),
                    )

                    if new_level > old_level:
                        logger.info(
                            f"[ProgressTracker] 🎉 Child {child_id} leveled up "
                            f"in {domain}: L{old_level}→L{new_level}"
                        )
                        # Fire async event from sync context — safe via call_soon_threadsafe
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            loop.call_soon_threadsafe(
                                loop.create_task,
                                event_bus.publish("skill.level_up", {
                                    "child_id":   child_id,
                                    "domain":     domain,
                                    "old_level":  old_level,
                                    "new_level":  new_level,
                                })
                            )
                        except Exception:
                            pass

                else:
                    # First time — initialise row
                    new_score = new_obs
                    new_level = _score_to_level(new_score)
                    cursor.execute(
                        """
                        INSERT INTO skill_scores (child_id, domain, score, skill_level)
                        VALUES (?, ?, ?, ?)
                        """,
                        (child_id, domain, round(new_score, 2), new_level),
                    )

        except Exception as e:
            logger.error(f"[ProgressTracker] EMA update error ({domain}): {e}")

    # ── Public reporting API ──────────────────────────────────────────────────

    def get_domain_scores(self, child_id: int) -> Dict[str, Dict]:
        """Return current domain scores and levels for all skill areas."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT domain, score, skill_level, last_updated "
                    "FROM skill_scores WHERE child_id=?",
                    (child_id,),
                )
                rows = {r["domain"]: dict(r) for r in cursor.fetchall()}

            # Fill in missing domains with defaults
            result = {}
            for domain, label in DOMAIN_LABELS.items():
                if domain in rows:
                    result[domain] = {
                        **rows[domain],
                        "label": label,
                    }
                else:
                    result[domain] = {
                        "domain":       domain,
                        "score":        0.0,
                        "skill_level":  1,
                        "last_updated": None,
                        "label":        label,
                    }
            return result
        except Exception as e:
            logger.error(f"[ProgressTracker] get_domain_scores error: {e}")
            return {}

    def get_progress_trend(
        self, child_id: int, domain: str, days: int = 30
    ) -> List[Dict]:
        """
        Return daily average game scores for a domain over the past N days.
        Used to plot a progress line chart on the dashboard.
        """
        game_types = self._domain_to_games(domain)
        if not game_types:
            return []

        placeholders = ",".join(["?"] * len(game_types))
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT DATE(played_at) as day,
                           AVG(score)       as avg_score,
                           COUNT(id)        as plays
                    FROM game_results
                    WHERE child_id=?
                      AND game_type IN ({placeholders})
                      AND played_at >= date('now', ?)
                    GROUP BY DATE(played_at)
                    ORDER BY day ASC
                    """,
                    [child_id, *game_types, f"-{days} days"],
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[ProgressTracker] trend error: {e}")
            return []

    def get_weekly_report(self, child_id: int) -> Dict[str, Any]:
        """Generate a full weekly progress report dict for the dashboard."""
        try:
            with db.get_cursor() as cursor:
                # Sessions this week
                cursor.execute(
                    """
                    SELECT COUNT(id) as sessions,
                           SUM(duration_seconds) as total_seconds,
                           AVG(attention_score)  as avg_attention,
                           AVG(speech_score)     as avg_speech
                    FROM sessions
                    WHERE child_id=?
                      AND start_time >= date('now', '-7 days')
                    """,
                    (child_id,),
                )
                session_row = dict(cursor.fetchone() or {})

                # Game stats this week
                cursor.execute(
                    """
                    SELECT game_type,
                           COUNT(id)     as plays,
                           AVG(score)    as avg_score,
                           MAX(score)    as best_score
                    FROM game_results
                    WHERE child_id=?
                      AND played_at >= date('now', '-7 days')
                    GROUP BY game_type
                    """,
                    (child_id,),
                )
                game_rows = [dict(r) for r in cursor.fetchall()]

                # Achievements this week
                cursor.execute(
                    """
                    SELECT achievement_name, earned_at
                    FROM achievements
                    WHERE child_id=?
                      AND created_at >= date('now', '-7 days')
                    ORDER BY created_at DESC
                    """,
                    (child_id,),
                )
                new_badges = [dict(r) for r in cursor.fetchall()]

            # Skill snapshot
            domain_scores = self.get_domain_scores(child_id)

            return {
                "period":         "last_7_days",
                "sessions":       session_row.get("sessions", 0),
                "total_minutes":  round((session_row.get("total_seconds") or 0) / 60),
                "avg_attention":  round((session_row.get("avg_attention") or 0) * 100, 1),
                "avg_speech":     round((session_row.get("avg_speech") or 0) * 100, 1),
                "games":          game_rows,
                "new_badges":     new_badges,
                "domain_scores":  domain_scores,
            }
        except Exception as e:
            logger.error(f"[ProgressTracker] weekly report error: {e}")
            return {}

    def get_skill_comparison(
        self, child_id: int, compare_days: int = 30
    ) -> Dict[str, Dict]:
        """
        Compare current domain scores vs. scores N days ago.
        Returns delta and trend ('improving'/'declining'/'stable').
        """
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT domain, AVG(score) as old_score
                    FROM skill_score_history
                    WHERE child_id=?
                      AND recorded_at <= date('now', ?)
                    GROUP BY domain
                    """,
                    (child_id, f"-{compare_days} days"),
                )
                old_scores = {r["domain"]: r["old_score"] for r in cursor.fetchall()}

            current = self.get_domain_scores(child_id)
            comparison = {}
            for domain, data in current.items():
                curr = data["score"]
                old  = old_scores.get(domain, curr)
                delta = round(curr - old, 1)
                if   delta >  3: trend = "improving"
                elif delta < -3: trend = "declining"
                else:            trend = "stable"
                comparison[domain] = {
                    "current_score": curr,
                    "old_score":     round(old, 1),
                    "delta":         delta,
                    "trend":         trend,
                    "skill_level":   data["skill_level"],
                }
            return comparison
        except Exception as e:
            logger.error(f"[ProgressTracker] comparison error: {e}")
            return {}

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _persist_game_result(
        self,
        child_id:   int,
        game_type:  str,
        score:      float,
        correct:    int,
        total:      int,
        difficulty: int,
    ):
        session_id = self._session.get("session_id")
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO game_results
                        (session_id, child_id, game_type, score,
                         correct_count, total_count, difficulty_level, completed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (session_id, child_id, game_type, score, correct, total, difficulty),
                )
        except Exception as e:
            logger.error(f"[ProgressTracker] persist game result error: {e}")

    def _update_session_record(
        self,
        session_id: int,
        attention_score: float,
        speech_score:    float,
        mood_end:        Optional[str],
    ):
        if not session_id:
            return
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sessions
                    SET attention_score=?, speech_score=?, mood_end=?
                    WHERE id=?
                    """,
                    (attention_score, speech_score, mood_end, session_id),
                )
        except Exception as e:
            logger.error(f"[ProgressTracker] update session error: {e}")

    # ── Mapping helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _game_to_domain(game_type: str) -> str:
        mapping = {
            "colors":       "attention",
            "emotions":     "emotional",
            "speech":       "speech",
            "turn_taking":  "social",
            "focus":        "attention",
        }
        return mapping.get(game_type, "attention")

    @staticmethod
    def _domain_to_games(domain: str) -> List[str]:
        mapping = {
            "speech":    ["speech"],
            "social":    ["turn_taking"],
            "emotional": ["emotions"],
            "attention": ["colors", "focus"],
        }
        return mapping.get(domain, [])
