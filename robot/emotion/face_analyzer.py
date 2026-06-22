import logging
import numpy as np
import asyncio
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class FaceAnalyzer:
    """
    Analyzes InsightFace facial keypoints (kps) and bounding box to detect expressions.
    Uses geometric heuristics for ultra-lightweight emotion detection.
    """
    
    def __init__(self):
        self._last_process_time = 0
        self.polling_interval = 5.0 # Run emotion detection every 5 seconds
        
        event_bus.subscribe('perception.frame', self._process_frame)

    def _dist(self, p1, p2):
        """Calculate Euclidean distance between two points (x, y)."""
        return np.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

    async def _process_frame(self, event_type: str, payload: dict):
        """Process a frame to detect facial emotion."""
        if payload['timestamp'] - self._last_process_time < self.polling_interval:
            return
            
        face = payload.get("insight_face")
        if face is None or not hasattr(face, 'kps'):
            return
            
        self._last_process_time = payload['timestamp']
            
        try:
            left_eye = face.kps[0]
            right_eye = face.kps[1]
            nose = face.kps[2]
            mouth_left = face.kps[3]
            mouth_right = face.kps[4]
            
            bbox_width = face.bbox[2] - face.bbox[0]
            bbox_height = face.bbox[3] - face.bbox[1]
            
            # Heuristics using 5 points and bbox
            mouth_width = self._dist(mouth_left, mouth_right)
            eye_dist = self._dist(left_eye, right_eye)
            
            mouth_ratio = mouth_width / bbox_width
            eye_ratio = eye_dist / bbox_width
            
            # Default to neutral
            emotion = "neutral"
            confidence = 0.5
            
            # Simple threshold rules based on 5 points
            # Wide mouth relative to face width = smile
            if mouth_ratio > 0.45:
                emotion = "happy"
                confidence = 0.8
            # Eyes very wide apart relative to face (surprise/fear eyes)
            elif eye_ratio > 0.5:
                emotion = "surprised"
                confidence = 0.6
                
            logger.debug(f"Detected face emotion: {emotion} (mouth_ratio: {mouth_ratio:.2f})")
            
            # Publish face emotion result
            await event_bus.publish('emotion.face', {
                "emotion": emotion,
                "confidence": confidence
            })
            
        except Exception as e:
            logger.error(f"Face emotion analysis error: {e}")
