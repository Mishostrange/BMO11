import logging
from robot.database.connection import db

logger = logging.getLogger(__name__)

class AdaptiveDifficulty:
    """Adjusts game difficulty dynamically to maintain a ~75% success rate (Flow State)."""

    def __init__(self, window_size=10, step_up_threshold=0.85, step_down_threshold=0.60, max_level=5):
        self.window_size = window_size
        self.step_up_threshold = step_up_threshold
        self.step_down_threshold = step_down_threshold
        self.max_level = max_level

    def get_difficulty(self, child_id: int) -> int:
        """Get the current difficulty level for a child from the database."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT difficulty_level FROM children WHERE id = ?", (child_id,))
                row = cursor.fetchone()
                return row['difficulty_level'] if row else 1
        except Exception as e:
            logger.error(f"Error fetching difficulty: {e}")
            return 1

    def set_difficulty(self, child_id: int, level: int):
        """Update the child's difficulty level in the database."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("UPDATE children SET difficulty_level = ? WHERE id = ?", (level, child_id))
            logger.info(f"Updated difficulty for child {child_id} to level {level}")
        except Exception as e:
            logger.error(f"Error setting difficulty: {e}")

    def process_results(self, child_id: int, game_type: str):
        """
        Analyze recent game results for this child and adjust global difficulty if needed.
        (In a more advanced version, difficulty would be tracked per-game rather than globally).
        """
        try:
            with db.get_cursor() as cursor:
                # Fetch last N game results
                cursor.execute(
                    """
                    SELECT score 
                    FROM game_results 
                    WHERE child_id = ? 
                    ORDER BY played_at DESC LIMIT ?
                    """,
                    (child_id, self.window_size)
                )
                rows = cursor.fetchall()

            if len(rows) < self.window_size:
                return # Not enough data yet

            accuracy = sum(1 for row in rows if row['score'] >= 0.8) / len(rows)
            current_level = self.get_difficulty(child_id)

            if accuracy >= self.step_up_threshold and current_level < self.max_level:
                self.set_difficulty(child_id, current_level + 1)
                logger.info(f"Child {child_id} mastering level {current_level}. Stepping up difficulty.")
            elif accuracy <= self.step_down_threshold and current_level > 1:
                self.set_difficulty(child_id, current_level - 1)
                logger.info(f"Child {child_id} struggling with level {current_level}. Stepping down difficulty.")

        except Exception as e:
            logger.error(f"Error processing adaptive difficulty: {e}")
