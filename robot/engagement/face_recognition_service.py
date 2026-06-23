import logging
import numpy as np
import asyncio
from robot.services.event_bus import event_bus
from robot.database.connection import db

logger = logging.getLogger(__name__)

class FaceRecognitionService:
    """
    Subscribes to 'perception.frame'.
    Receives InsightFace target_face directly from the CameraManager.
    Compares embeddings with the SQLite database to identify children or trigger registration.
    """
    def __init__(self):
        
        self.known_faces = [] # List of tuples: (child_id, encoding)
        self._load_known_faces()
        
        self.unknown_face_frames = 0
        self.current_child_id = None
        self.is_registering = False
        
        event_bus.subscribe('perception.frame', self._on_frame)
        event_bus.subscribe('profile.created', self._on_profile_created)
        
    def _load_known_faces(self):
        self.known_faces = []
        with db.get_cursor() as cursor:
            cursor.execute("SELECT id, face_encoding FROM children WHERE face_encoding IS NOT NULL")
            for row in cursor.fetchall():
                child_id, encoding_bytes = row
                # Convert bytes back to numpy array
                encoding = np.frombuffer(encoding_bytes, dtype=np.float32)
                self.known_faces.append((child_id, encoding))
        logger.info(f"Loaded {len(self.known_faces)} known faces from database.")

    async def _on_profile_created(self, event_type: str, child_id: int):
        """Reload faces when a new profile is registered."""
        self._load_known_faces()
        self.is_registering = False
        self.current_child_id = child_id
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

    async def _on_frame(self, event_type: str, payload: dict):
        if self.is_registering:
            return # Don't process while we are mid-registration conversation
            
        # Only process 1 frame per second to save CPU
        if getattr(self, '_last_process_time', 0) > payload['timestamp'] - 1.0:
            return
        self._last_process_time = payload['timestamp']
        
        target_face = payload.get("insight_face")
        
        try:
            if target_face:
                embedding = target_face.embedding
                
                # Normalize embedding
                embedding = embedding / np.linalg.norm(embedding)
                
                match_id = self._find_match(embedding)
                
                if match_id:
                    self.unknown_face_frames = 0
                    if self.current_child_id != match_id:
                        logger.info(f"Recognized child {match_id}")
                        self.current_child_id = match_id
                        await event_bus.publish("face.recognized", match_id)
                else:
                    self.unknown_face_frames += 1
                    # If we see the unknown face for 10 consecutive seconds
                    if self.unknown_face_frames >= 10:
                        logger.info("Unknown face detected consistently. Triggering registration.")
                        self.is_registering = True
                        self.unknown_face_frames = 0
                        await event_bus.publish("face.unknown", embedding.tobytes())
            else:
                if self.unknown_face_frames > 0:
                    logger.debug("InsightFace returned 0 faces in the current frame.")
                self.unknown_face_frames = 0
                
        except Exception as e:
            logger.error(f"Face recognition error: {e}")
