import logging
import time
from datetime import datetime
from robot.database.connection import db

logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self):
        self.active_session_id = None
        self.active_child_id = None
        self.start_time = None
        self.max_duration_seconds = 30 * 60  # 30 minutes limit

    def start_session(self, child_id: int, session_type: str = "casual") -> int:
        """Start a new session in the database."""
        if self.active_session_id:
            logger.warning("Attempted to start a session while one is active. Ending old session.")
            self.end_session()

        self.active_child_id = child_id
        self.start_time = time.time()

        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO sessions (child_id, session_type, start_time)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (child_id, session_type)
                )
                self.active_session_id = cursor.lastrowid
            logger.info(f"Started session {self.active_session_id} for child {child_id}")
            return self.active_session_id
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            return None

    def end_session(self):
        """End the current session."""
        if not self.active_session_id:
            return

        duration = int(time.time() - self.start_time) if self.start_time else 0
        
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sessions 
                    SET end_time = CURRENT_TIMESTAMP, duration_seconds = ?
                    WHERE id = ?
                    """,
                    (duration, self.active_session_id)
                )
            logger.info(f"Ended session {self.active_session_id}. Duration: {duration}s")
        except Exception as e:
            logger.error(f"Failed to end session: {e}")

        self.active_session_id = None
        self.active_child_id = None
        self.start_time = None

    def check_time_limit(self) -> bool:
        """Return True if session has exceeded max duration."""
        if not self.start_time:
            return False
        return (time.time() - self.start_time) > self.max_duration_seconds
