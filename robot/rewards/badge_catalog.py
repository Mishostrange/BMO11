"""
robot/rewards/badge_catalog.py
──────────────────────────────────────────────────────────────────────────────
Centralised badge catalog and automatic badge-check logic.

All badges are defined here.  The BadgeEngine runs after every game result
and awards any badges the child just unlocked.

Badge definitions
─────────────────
Each badge has:
  id          – unique string key
  name        – display name
  description – what the child did
  icon        – single emoji shown on dashboard / in animations
  xp          – bonus XP granted when badge is first earned
  category    – grouping: first_steps / emotion / speech / memory / social / super
  check       – callable (child_id, cursor) → bool
"""

import logging
from typing import Dict, Any, Callable, List
from robot.database.connection import db
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)


# ── Helper queries ────────────────────────────────────────────────────────────

def _total_sessions(child_id: int, cursor) -> int:
    cursor.execute("SELECT COUNT(*) FROM sessions WHERE child_id=?", (child_id,))
    return cursor.fetchone()[0] or 0


def _avg_score_for_game(game_type: str, child_id: int, cursor) -> float:
    cursor.execute(
        "SELECT AVG(score) FROM game_results WHERE child_id=? AND game_type=?",
        (child_id, game_type),
    )
    v = cursor.fetchone()[0]
    return float(v) if v else 0.0


def _best_score_for_game(game_type: str, child_id: int, cursor) -> float:
    cursor.execute(
        "SELECT MAX(score) FROM game_results WHERE child_id=? AND game_type=?",
        (child_id, game_type),
    )
    v = cursor.fetchone()[0]
    return float(v) if v else 0.0


def _games_played(child_id: int, cursor) -> int:
    cursor.execute("SELECT COUNT(*) FROM game_results WHERE child_id=?", (child_id,))
    return cursor.fetchone()[0] or 0


def _total_tokens(child_id: int, cursor) -> int:
    cursor.execute(
        "SELECT SUM(amount) FROM rewards WHERE child_id=? AND reward_type='token'",
        (child_id,),
    )
    v = cursor.fetchone()[0]
    return int(v) if v else 0


def _distinct_games_played(child_id: int, cursor) -> int:
    cursor.execute(
        "SELECT COUNT(DISTINCT game_type) FROM game_results WHERE child_id=?",
        (child_id,),
    )
    return cursor.fetchone()[0] or 0


# ── Badge catalog ─────────────────────────────────────────────────────────────

BADGES: List[Dict[str, Any]] = [
    # ── First Steps ───────────────────────────────────────────────────────────
    {
        "id": "first_session",
        "name": "First Steps 👶",
        "description": "Completed your very first session with BMO!",
        "icon": "👶",
        "xp": 10,
        "category": "first_steps",
        "check": lambda cid, cur: _total_sessions(cid, cur) >= 1,
    },
    {
        "id": "first_game",
        "name": "Beginner 🌱",
        "description": "Played your first game!",
        "icon": "🌱",
        "xp": 15,
        "category": "first_steps",
        "check": lambda cid, cur: _games_played(cid, cur) >= 1,
    },
    {
        "id": "explorer",
        "name": "Explorer 🗺️",
        "description": "Tried 3 different types of games!",
        "icon": "🗺️",
        "xp": 25,
        "category": "first_steps",
        "check": lambda cid, cur: _distinct_games_played(cid, cur) >= 3,
    },
    # ── Emotion ────────────────────────────────────────────────────────────────
    {
        "id": "emotion_starter",
        "name": "Feelings Friend 🙂",
        "description": "Completed the Feelings Game for the first time!",
        "icon": "🙂",
        "xp": 15,
        "category": "emotion",
        "check": lambda cid, cur: _games_played(cid, cur) >= 1
                                   and _avg_score_for_game("emotions", cid, cur) > 0,
    },
    {
        "id": "emotion_master",
        "name": "Emotion Detective 🔍",
        "description": "Scored over 80% in the Feelings Game!",
        "icon": "🔍",
        "xp": 50,
        "category": "emotion",
        "check": lambda cid, cur: _avg_score_for_game("emotions", cid, cur) >= 0.8,
    },
    # ── Speech ─────────────────────────────────────────────────────────────────
    {
        "id": "speech_star",
        "name": "Voice Champion 🎙️",
        "description": "Scored over 80% in the Echo Game!",
        "icon": "🎙️",
        "xp": 50,
        "category": "speech",
        "check": lambda cid, cur: _avg_score_for_game("speech", cid, cur) >= 0.8,
    },
    {
        "id": "communicator",
        "name": "Communicator 📣",
        "description": "Played the Echo Game 5 times!",
        "icon": "📣",
        "xp": 30,
        "category": "speech",
        "check": lambda cid, cur: _games_played(cid, cur) >= 5
                                   and _best_score_for_game("speech", cid, cur) > 0,
    },
    # ── Memory ─────────────────────────────────────────────────────────────────
    {
        "id": "memory_starter",
        "name": "Memory Spark 💡",
        "description": "Completed the Memory Match game!",
        "icon": "💡",
        "xp": 15,
        "category": "memory",
        "check": lambda cid, cur: _best_score_for_game("memory_match", cid, cur) > 0,
    },
    {
        "id": "memory_champion",
        "name": "Memory Champion 🏆",
        "description": "Scored over 90% in Memory Match!",
        "icon": "🏆",
        "xp": 75,
        "category": "memory",
        "check": lambda cid, cur: _best_score_for_game("memory_match", cid, cur) >= 0.9,
    },
    # ── Social ─────────────────────────────────────────────────────────────────
    {
        "id": "social_buddy",
        "name": "Good Friend 🤝",
        "description": "Completed the Friend Skills game!",
        "icon": "🤝",
        "xp": 20,
        "category": "social",
        "check": lambda cid, cur: _best_score_for_game("social_skills", cid, cur) > 0,
    },
    # ── Imitation ──────────────────────────────────────────────────────────────
    {
        "id": "copy_cat",
        "name": "Copy Cat 🐱",
        "description": "Successfully imitated BMO's actions!",
        "icon": "🐱",
        "xp": 25,
        "category": "social",
        "check": lambda cid, cur: _best_score_for_game("imitation", cid, cur) > 0,
    },
    # ── Super Learner ──────────────────────────────────────────────────────────
    {
        "id": "streak_3",
        "name": "3-Day Streak 🔥",
        "description": "Had sessions on 3 different days!",
        "icon": "🔥",
        "xp": 40,
        "category": "super",
        "check": lambda cid, cur: _check_streak(cid, cur, 3),
    },
    {
        "id": "super_learner",
        "name": "Super Learner 🚀",
        "description": "Earned 100 stars total!",
        "icon": "🚀",
        "xp": 100,
        "category": "super",
        "check": lambda cid, cur: _total_tokens(cid, cur) >= 100,
    },
]

BADGE_MAP: Dict[str, Dict] = {b["id"]: b for b in BADGES}


def _check_streak(child_id: int, cursor, days: int) -> bool:
    cursor.execute(
        "SELECT DISTINCT DATE(start_time) FROM sessions WHERE child_id=? ORDER BY 1 DESC LIMIT ?",
        (child_id, days),
    )
    rows = cursor.fetchall()
    return len(rows) >= days


# ── BadgeEngine ────────────────────────────────────────────────────────────────

class BadgeEngine:
    """Check and award badges after game results or session ends."""

    def __init__(self):
        event_bus.subscribe("game.scored",    self._on_game_scored)
        event_bus.subscribe("session.ended",  self._on_session_ended)

    async def _on_game_scored(self, _event: str, data: dict):
        child_id = data.get("child_id")
        if child_id:
            await self.check_and_award(child_id)

    async def _on_session_ended(self, _event: str, data: dict):
        child_id = data.get("child_id")
        if child_id:
            await self.check_and_award(child_id)

    async def check_and_award(self, child_id: int):
        """Run all badge checks and award any newly-unlocked badges."""
        try:
            with db.get_cursor() as cursor:
                # Get already-earned badge IDs
                cursor.execute(
                    "SELECT achievement_id FROM achievements WHERE child_id=?",
                    (child_id,),
                )
                earned_ids = {row[0] for row in cursor.fetchall()}

                newly_earned = []
                for badge in BADGES:
                    if badge["id"] in earned_ids:
                        continue  # already has it
                    try:
                        if badge["check"](child_id, cursor):
                            newly_earned.append(badge)
                    except Exception as e:
                        logger.debug(f"[BadgeEngine] Check error for {badge['id']}: {e}")

                for badge in newly_earned:
                    self._grant_badge(child_id, badge, cursor)

            for badge in newly_earned:
                logger.info(f"[BadgeEngine] Awarded '{badge['name']}' to child {child_id}")
                # Fire badge.earned event — the orchestrator will announce it after game ends
                await event_bus.publish("badge.earned", {
                    "child_id": child_id,
                    "badge": badge,
                })
                # Only fire UI animation (no TTS here — orchestrator handles that)
                await event_bus.publish("ui.animation.trigger", {"type": "fireworks"})

        except Exception as e:
            logger.error(f"[BadgeEngine] check_and_award error: {e}")

    def _grant_badge(self, child_id: int, badge: dict, cursor):
        try:
            cursor.execute(
                """
                INSERT INTO achievements
                    (child_id, achievement_type, achievement_id, achievement_name, description)
                VALUES (?, 'badge', ?, ?, ?)
                """,
                (child_id, badge["id"], badge["name"], badge["description"]),
            )
        except Exception as e:
            logger.warning(f"[BadgeEngine] Could not insert badge {badge['id']}: {e}")

    def get_child_badges(self, child_id: int) -> List[Dict]:
        """Return all earned badges for a child (for dashboard display)."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT achievement_id, earned_at FROM achievements WHERE child_id=?",
                    (child_id,),
                )
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    badge_id, earned_at = row[0], row[1]
                    if badge_id in BADGE_MAP:
                        b = dict(BADGE_MAP[badge_id])
                        b["earned_at"] = earned_at
                        result.append(b)
                return result
        except Exception as e:
            logger.error(f"[BadgeEngine] get_child_badges error: {e}")
            return []
