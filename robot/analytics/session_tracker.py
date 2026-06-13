import logging
from robot.database.connection import db
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class SessionTracker:
    """Tracks metrics during an active session."""
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
        
        # Subscribe to relevant events
        event_bus.subscribe('game.finished', self._on_game_finished)
        event_bus.subscribe('emotion.detected', self._on_emotion_detected)

    async def _on_game_finished(self, event_type: str, data: dict):
        child_id = data.get("child_id")
        game_type = data.get("game_type")
        score = data.get("score", 0.0)
        session_id = self.session_manager.active_session_id
        
        if not session_id or not child_id:
            return
            
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO game_results (child_id, session_id, game_type, score)
                    VALUES (?, ?, ?, ?)
                    """,
                    (child_id, session_id, game_type, score)
                )
        except Exception as e:
            logger.error(f"Error tracking game result: {e}")

    async def _on_emotion_detected(self, event_type: str, data: dict):
        emotion = data.get("emotion")
        session_id = self.session_manager.active_session_id
        
        # In a real app, we would aggregate these rather than inserting every single emotion event
        # For MVP, we might just store a summary at the end of the session, or log extreme emotions
        pass
