"""
analytics/reporter.py
─────────────────────────────────────────────────────────────────────────────
Unified analytics reporter. Aggregates data from:
• sessions / game_results / achievements  (relational DB)
• skill_scores / skill_score_history      (relational DB)
• emotion_log                             (relational DB)
• ProgressTracker (rich domain metrics)
"""

import logging
from typing import Dict, Any, List, Optional
from robot.database.connection import db

logger = logging.getLogger(__name__)


class AnalyticsReporter:
    """Generates reports and summaries for the parent dashboard."""

    # ── Overall summary ───────────────────────────────────────────────────────

    def get_child_summary(self, child_id: int) -> Dict[str, Any]:
        summary = {
            "total_sessions":       0,
            "total_duration_minutes": 0,
            "favorite_game":        None,
            "recent_achievements":  [],
            "avg_attention":        0.0,
            "avg_speech":           0.0,
        }

        try:
            with db.get_cursor() as cursor:
                # Session totals
                cursor.execute(
                    """
                    SELECT COUNT(id)             AS cnt,
                           SUM(duration_seconds) AS dur,
                           AVG(attention_score)  AS attn,
                           AVG(speech_score)     AS spch
                    FROM sessions WHERE child_id=?
                    """,
                    (child_id,),
                )
                row = cursor.fetchone()
                if row and row["cnt"]:
                    summary["total_sessions"]         = row["cnt"]
                    summary["total_duration_minutes"] = round((row["dur"] or 0) / 60)
                    summary["avg_attention"]          = round((row["attn"] or 0) * 100, 1)
                    summary["avg_speech"]             = round((row["spch"] or 0) * 100, 1)

                # Favourite game
                cursor.execute(
                    """
                    SELECT game_type, COUNT(*) AS plays
                    FROM game_results WHERE child_id=?
                    GROUP BY game_type ORDER BY plays DESC LIMIT 1
                    """,
                    (child_id,),
                )
                g = cursor.fetchone()
                if g:
                    summary["favorite_game"] = g["game_type"]

                # Recent achievements
                cursor.execute(
                    """
                    SELECT achievement_name, created_at
                    FROM achievements WHERE child_id=?
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (child_id,),
                )
                summary["recent_achievements"] = [dict(r) for r in cursor.fetchall()]

        except Exception as e:
            logger.error(f"[Reporter] get_child_summary error: {e}")

        return summary

    # ── Game performance ──────────────────────────────────────────────────────

    def get_game_performance(self, child_id: int) -> List[Dict[str, Any]]:
        """Average and best scores per game type."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT game_type,
                           AVG(score)   AS avg_score,
                           MAX(score)   AS best_score,
                           COUNT(id)    AS plays,
                           AVG(correct_count * 1.0 / MAX(total_count, 1)) AS accuracy
                    FROM game_results WHERE child_id=?
                    GROUP BY game_type
                    """,
                    (child_id,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[Reporter] get_game_performance error: {e}")
            return []

    # ── Skill domain scores ───────────────────────────────────────────────────

    def get_domain_scores(self, child_id: int) -> Dict[str, Any]:
        """Return current skill scores per domain."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT domain, score, skill_level, last_updated "
                    "FROM skill_scores WHERE child_id=?",
                    (child_id,),
                )
                return {r["domain"]: dict(r) for r in cursor.fetchall()}
        except Exception as e:
            logger.error(f"[Reporter] get_domain_scores error: {e}")
            return {}

    # ── Progress trend (for line charts) ─────────────────────────────────────

    def get_score_trend(
        self, child_id: int, domain: str = None, days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Daily average game scores over the past N days.
        If domain is None, returns across all games.
        """
        try:
            with db.get_cursor() as cursor:
                query = """
                    SELECT DATE(played_at) AS day,
                           AVG(score)      AS avg_score,
                           COUNT(id)       AS plays
                    FROM game_results
                    WHERE child_id=?
                      AND played_at >= date('now', ?)
                """
                params: list = [child_id, f"-{days} days"]
                query += " GROUP BY DATE(played_at) ORDER BY day ASC"

                cursor.execute(query, params)
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[Reporter] get_score_trend error: {e}")
            return []

    # ── Emotion distribution ──────────────────────────────────────────────────

    def get_emotion_distribution(
        self, child_id: int, days: int = 7
    ) -> Dict[str, int]:
        """Emotion counts over the past N days (excludes snapshots)."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT emotion, COUNT(*) AS cnt
                    FROM emotion_log
                    WHERE child_id=?
                      AND timestamp >= datetime('now', ?)
                      AND source    != 'snapshot'
                    GROUP BY emotion
                    """,
                    (child_id, f"-{days} days"),
                )
                return {r["emotion"]: r["cnt"] for r in cursor.fetchall()}
        except Exception as e:
            logger.error(f"[Reporter] emotion distribution error: {e}")
            return {}

    def get_emotion_timeline(
        self, child_id: int, session_id: int
    ) -> List[Dict[str, Any]]:
        """Emotion readings for a specific session (for chart playback)."""
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
            logger.error(f"[Reporter] emotion timeline error: {e}")
            return []

    # ── Session history ───────────────────────────────────────────────────────

    def get_session_history(
        self, child_id: int, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return recent sessions with key metrics."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, session_type, start_time, duration_seconds,
                           attention_score, speech_score, mood_start, mood_end,
                           difficulty_level
                    FROM sessions
                    WHERE child_id=?
                    ORDER BY start_time DESC LIMIT ?
                    """,
                    (child_id, limit),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[Reporter] session history error: {e}")
            return []

    # ── Interests (from memory) ───────────────────────────────────────────────

    def get_interest_distribution(self, child_id: int) -> Dict[str, int]:
        """Return memory-extracted interest categories and counts."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT category, COUNT(*) AS cnt
                    FROM memories
                    WHERE child_id=? AND memory_type='interest'
                    GROUP BY category
                    """,
                    (child_id,),
                )
                return {r["category"]: r["cnt"] for r in cursor.fetchall()}
        except Exception as e:
            logger.error(f"[Reporter] interest distribution error: {e}")
            return {}
