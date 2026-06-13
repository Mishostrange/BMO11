import logging
from robot.database.connection import db
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class RewardSystem:
    LEVEL_THRESHOLDS = [0, 50, 120, 210, 320, 450, 600, 780, 990, 1230]

    BADGES = {
        "first_session": {"name": "First Steps", "xp": 10},
        "streak_3": {"name": "3-Day Streak", "xp": 25},
        "emotion_master": {"name": "Emotion Detective", "xp": 50},
        "speech_star": {"name": "Voice Champion", "xp": 50},
    }

    def __init__(self):
        # Subscribe to reward events from games
        event_bus.subscribe('reward.earned', self._on_reward_earned)

    async def _on_reward_earned(self, event_type: str, data: dict):
        child_id = data.get("child_id")
        tokens = data.get("tokens", 0)
        reason = data.get("reason", "gameplay")
        
        if child_id and tokens > 0:
            self.grant_tokens(child_id, tokens, reason)
            # Give XP equal to tokens for simplicity
            await self.add_xp(child_id, tokens * 5)

    def grant_tokens(self, child_id: int, amount: int, reason: str):
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO rewards (child_id, reward_type, amount, reason) VALUES (?, 'token', ?, ?)",
                    (child_id, amount, reason)
                )
            logger.debug(f"Granted {amount} tokens to child {child_id} for {reason}")
        except Exception as e:
            logger.error(f"Error granting tokens: {e}")

    def get_total_tokens(self, child_id: int) -> int:
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT SUM(amount) FROM rewards WHERE child_id = ? AND reward_type = 'token'", (child_id,))
                result = cursor.fetchone()[0]
                return result if result else 0
        except Exception as e:
            logger.error(f"Error getting tokens: {e}")
            return 0

    async def add_xp(self, child_id: int, amount: int):
        """Add XP and handle level ups."""
        # For simplicity, we store total XP implicitly through total tokens * 5, 
        # or we could add an xp column to the children table.
        # Here we'll implement a simple level check based on total tokens.
        
        total_tokens = self.get_total_tokens(child_id)
        current_xp = total_tokens * 5
        
        new_level = 1
        for i, threshold in enumerate(self.LEVEL_THRESHOLDS):
            if current_xp >= threshold:
                new_level = i + 1
            else:
                break
                
        # Check current level in DB
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT difficulty_level FROM children WHERE id = ?", (child_id,))
                row = cursor.fetchone()
                db_level = row['difficulty_level'] if row else 1
                
                # We reuse difficulty_level as overall level for this simple example,
                # though they should ideally be separate.
                
                # If we really leveled up
                if new_level > db_level:
                    logger.info(f"Child {child_id} leveled up to {new_level}!")
                    await event_bus.publish('ui.animation.trigger', {"type": "fireworks"})
                    
                    # We don't overwrite difficulty_level here as adaptive difficulty manages it.
                    # In a full schema, we'd have a 'player_level' column.
        except Exception as e:
            logger.error(f"Error in XP calculation: {e}")

    def grant_badge(self, child_id: int, badge_id: str):
        if badge_id not in self.BADGES:
            return
            
        badge_info = self.BADGES[badge_id]
        
        try:
            with db.get_cursor() as cursor:
                # Check if already has badge
                cursor.execute("SELECT id FROM achievements WHERE child_id = ? AND achievement_id = ?", (child_id, badge_id))
                if cursor.fetchone():
                    return # Already has badge
                    
                cursor.execute(
                    "INSERT INTO achievements (child_id, achievement_type, achievement_id, achievement_name) VALUES (?, 'badge', ?, ?)",
                    (child_id, badge_id, badge_info["name"])
                )
            logger.info(f"Granted badge '{badge_info['name']}' to child {child_id}")
            # Could trigger an event_bus event here for UI notification
        except Exception as e:
            logger.error(f"Error granting badge: {e}")
