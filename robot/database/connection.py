import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator
import logging
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class DatabaseConnectionManager:
    def __init__(self, db_path: str = settings.db.PATH):
        self.db_path = db_path
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'connection'):
            # Check same thread is false because we manage it per thread in ThreadLocal
            # but sometimes passing between async tasks in same thread needs it
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            
            # Enable foreign keys and WAL mode for better concurrency
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            
            self._local.connection = conn
            logger.debug(f"Created new database connection to {self.db_path}")
        return self._local.connection

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Get a database cursor, commits on success, rolls back on error."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise
        finally:
            cursor.close()
            
    def close_all(self):
        """Close thread-local connection if it exists."""
        if hasattr(self._local, 'connection'):
            try:
                self._local.connection.close()
                logger.debug("Closed database connection")
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            finally:
                del self._local.connection

# Global singleton
db = DatabaseConnectionManager()
