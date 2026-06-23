import logging
import numpy as np
import asyncio
from robot.services.event_bus import event_bus
from robot.database.connection import db

import collections

logger = logging.getLogger(__name__)

class FaceRecognitionService:
    """
    Subscribes to 'engagement.active_user_frame'.
    Receives embedding of the currently locked active user.
    Compares embeddings with the SQLite database to identify children or trigger registration.
    Uses a rolling buffer to prevent false-positive switching.
    """
    def __init__(self):
        self.known_faces = [] # List of tuples: (child_id, encoding)
        self._load_known_faces()
        
        self.recognition_buffer = collections.deque(maxlen=5)
        self.unknown_buffer = collections.deque(maxlen=5) # For collecting embeddings during registration
        self.current_child_id = None
        self.is_registering = False
        
        event_bus.subscribe('engagement.active_user_frame', self._on_active_user_frame)
        event_bus.subscribe('profile.created', self._on_profile_created)
        event_bus.subscribe('engagement.session_ended', self._on_session_ended)
        
    def _load_known_faces(self):
        self.known_faces = []
        with db.get_cursor() as cursor:
            # Query the new face_embeddings table
            cursor.execute("SELECT child_id, embedding FROM face_embeddings")
            for row in cursor.fetchall():
                child_id, encoding_bytes = row
                encoding = np.frombuffer(encoding_bytes, dtype=np.float32)
                self.known_faces.append((child_id, encoding))
                
            # Also load from legacy column just in case migrations are fresh
            cursor.execute("SELECT id, face_encoding FROM children WHERE face_encoding IS NOT NULL")
            for row in cursor.fetchall():
                child_id, encoding_bytes = row
                encoding = np.frombuffer(encoding_bytes, dtype=np.float32)
                self.known_faces.append((child_id, encoding))
                
        logger.info(f"Loaded {len(self.known_faces)} known face embeddings from database.")

    async def _on_session_ended(self, event_type: str, payload: dict):
        self.current_child_id = None
        self.recognition_buffer.clear()
        
    async def _on_profile_created(self, event_type: str, child_id: int):
        """Reload faces when a new profile is registered."""
        self._load_known_faces()
        self.is_registering = False
        self.current_child_id = child_id
        self.recognition_buffer.clear()
        await event_bus.publish("face.recognized", child_id)

    def _find_match(self, embedding: np.ndarray, threshold=1.2) -> int:
        """Find the closest known face using cosine similarity (InsightFace uses L2 distance/cosine)."""
        if not self.known_faces:
            return None
            
        best_match = None
        min_dist = float('inf')
        
        for child_id, known_emb in self.known_faces:
            # L2 Distance between normalized embeddings
            dist = np.linalg.norm(embedding - known_emb)
            if dist < min_dist:
                min_dist = dist
                best_match = child_id
                
        # InsightFace typical threshold for buffalo_s is around 1.0 - 1.2
        if min_dist < threshold:
            return best_match
        return None

    async def _on_active_user_frame(self, event_type: str, payload: dict):
        if self.is_registering:
            return 
            
        embedding_bytes = payload.get("embedding")
        if not embedding_bytes:
            return
            
        try:
            embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
            embedding = embedding / np.linalg.norm(embedding)
            
            match_id = self._find_match(embedding)
            
            # Add to rolling buffer
            self.recognition_buffer.append(match_id)
            
            # Determine majority vote in buffer
            if len(self.recognition_buffer) == self.recognition_buffer.maxlen:
                counts = collections.Counter(self.recognition_buffer)
                majority_id, count = counts.most_common(1)[0]
                
                # Require 3 out of 5 frames to agree
                if count >= 3:
                    if majority_id is not None:
                        if self.current_child_id != majority_id:
                            logger.info(f"Recognized child {majority_id} (Stabilized)")
                            self.current_child_id = majority_id
                            
                            # Update last_seen
                            with db.get_cursor() as cursor:
                                cursor.execute("UPDATE children SET last_seen = CURRENT_TIMESTAMP WHERE id = ?", (majority_id,))
                                
                            await event_bus.publish("face.recognized", majority_id)
                    else:
                        self.unknown_buffer.append(embedding.tobytes())
                        if len(self.unknown_buffer) == self.unknown_buffer.maxlen:
                            logger.info("Unknown face detected consistently. Triggering registration.")
                            self.is_registering = True
                            await event_bus.publish("face.unknown", {
                                "embeddings": list(self.unknown_buffer)
                            })
                            self.unknown_buffer.clear()
                            
        except Exception as e:
            logger.error(f"Face recognition error: {e}")
