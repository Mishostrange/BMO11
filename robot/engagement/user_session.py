import logging
import asyncio
import time
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class UserSessionManager:
    """
    Engagement State Engine.
    Subscribes to 'engagement.update'.
    Locks onto the track_id with the highest attention score.
    Maintains a Soft Timeout (15s) and Hard Timeout (60s).
    Publishes 'engagement.active_user_frame' containing the embedding of the locked user.
    """
    
    def __init__(self):
        self.active_track_id = None
        self.last_seen_time = 0.0
        
        self.SOFT_TIMEOUT = 15.0
        self.HARD_TIMEOUT = 60.0
        
        event_bus.subscribe('engagement.update', self._on_engagement_update)
        
    async def _on_engagement_update(self, event_type: str, payload: dict):
        faces = payload.get("faces_attention", [])
        
        if not faces:
            await self._check_timeouts()
            return
            
        # If we don't have an active track, pick the one with the highest attention > 40
        if self.active_track_id is None:
            best_face = max(faces, key=lambda f: f["score"])
            if best_face["score"] >= 40.0:
                self.active_track_id = best_face["track_id"]
                self.last_seen_time = time.time()
                logger.info(f"Locked onto new user track: {self.active_track_id} (Score: {best_face['score']:.1f})")
                await self._publish_active_user(best_face)
            return

        # If we have an active track, see if it is in the current frame
        active_face = next((f for f in faces if f["track_id"] == self.active_track_id), None)
        
        if active_face:
            self.last_seen_time = time.time()
            await self._publish_active_user(active_face)
        else:
            await self._check_timeouts()
            
    async def _check_timeouts(self):
        if self.active_track_id is None:
            return
            
        time_since_seen = time.time() - self.last_seen_time
        
        if time_since_seen > self.HARD_TIMEOUT:
            logger.info(f"Hard timeout: Releasing track {self.active_track_id}. Session ended.")
            self.active_track_id = None
            await event_bus.publish('engagement.session_ended', {})
        elif time_since_seen > self.SOFT_TIMEOUT:
            # We are in soft timeout. We keep the track locked, but we could notify the system.
            pass

    async def _publish_active_user(self, face_data: dict):
        # We pass this on to the Face Recognition service
        if face_data.get("embedding"):
            await event_bus.publish('engagement.active_user_frame', {
                "embedding": face_data["embedding"]
            })
