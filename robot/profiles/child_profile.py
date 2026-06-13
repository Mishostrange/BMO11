import logging
import json
from typing import Dict, Any, List, Optional
from robot.database.connection import db

logger = logging.getLogger(__name__)

class ChildProfileManager:
    """Manages child profiles in the database."""
    
    def create_profile(self, name: str, age: int, communication_level: str, favorite_topics: List[str] = None) -> Optional[int]:
        try:
            topics_json = json.dumps(favorite_topics or [])
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO children (name, age, communication_level, favorite_topics)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, age, communication_level, topics_json)
                )
                child_id = cursor.lastrowid
                logger.info(f"Created profile for child '{name}' (ID: {child_id})")
                return child_id
        except Exception as e:
            logger.error(f"Error creating profile: {e}")
            return None

    def get_profile(self, child_id: int) -> Optional[Dict[str, Any]]:
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT * FROM children WHERE id = ?", (child_id,))
                row = cursor.fetchone()
                if row:
                    profile = dict(row)
                    # Parse JSON fields
                    try:
                        profile['favorite_topics'] = json.loads(profile['favorite_topics'])
                    except:
                        profile['favorite_topics'] = []
                    return profile
            return None
        except Exception as e:
            logger.error(f"Error fetching profile: {e}")
            return None

    def update_profile(self, child_id: int, updates: Dict[str, Any]) -> bool:
        if not updates:
            return True
            
        # Filter allowed fields
        allowed = ['name', 'age', 'communication_level', 'favorite_topics', 'difficulty_level']
        filtered = {k: v for k, v in updates.items() if k in allowed}
        
        if 'favorite_topics' in filtered:
            filtered['favorite_topics'] = json.dumps(filtered['favorite_topics'])
            
        set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
        values = list(filtered.values())
        values.append(child_id)
        
        try:
            with db.get_cursor() as cursor:
                cursor.execute(f"UPDATE children SET {set_clause} WHERE id = ?", values)
            logger.info(f"Updated profile for child {child_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating profile: {e}")
            return False

    def list_profiles(self) -> List[Dict[str, Any]]:
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT id, name, age, communication_level, difficulty_level FROM children")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error listing profiles: {e}")
            return []
