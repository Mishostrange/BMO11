import logging
import numpy as np
import asyncio
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class FaceAnalyzer:
    """
    Analyzes MediaPipe Face Mesh landmarks to detect facial expressions.
    Uses geometric heuristics (distances between landmarks) for ultra-lightweight
    emotion detection without needing heavy ML models like DeepFace.
    """
    
    def __init__(self):
        self._last_process_time = 0
        self.polling_interval = 5.0 # Run emotion detection every 5 seconds
        
        event_bus.subscribe('perception.frame', self._process_frame)

    def _dist(self, p1, p2):
        """Calculate Euclidean distance between two landmarks."""
        return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)

    async def _process_frame(self, event_type: str, payload: dict):
        """Process a frame to detect facial emotion."""
        if payload['timestamp'] - self._last_process_time < self.polling_interval:
            return
            
        landmarks = payload.get("face_landmarks")
        if not landmarks:
            return
            
        self._last_process_time = payload['timestamp']
            
        try:
            # MediaPipe Face Mesh Landmark indices
            # Mouth
            mouth_left = landmarks.landmark[61]
            mouth_right = landmarks.landmark[291]
            mouth_top = landmarks.landmark[13]
            mouth_bottom = landmarks.landmark[14]
            # Eyebrows
            left_eyebrow_inner = landmarks.landmark[55]
            right_eyebrow_inner = landmarks.landmark[285]
            left_eyebrow_outer = landmarks.landmark[105]
            right_eyebrow_outer = landmarks.landmark[334]
            # Eyes
            left_eye_top = landmarks.landmark[159]
            left_eye_bottom = landmarks.landmark[145]
            
            # Nose for normalization
            nose_top = landmarks.landmark[8]
            nose_bottom = landmarks.landmark[2]
            face_height = self._dist(nose_top, nose_bottom)
            
            # Heuristics
            mouth_width = self._dist(mouth_left, mouth_right) / face_height
            mouth_openness = self._dist(mouth_top, mouth_bottom) / face_height
            
            # Eyebrow shape
            left_eyebrow_slope = left_eyebrow_inner.y - left_eyebrow_outer.y
            right_eyebrow_slope = right_eyebrow_inner.y - right_eyebrow_outer.y
            eyebrow_furrow = self._dist(left_eyebrow_inner, right_eyebrow_inner) / face_height
            
            eye_openness = self._dist(left_eye_top, left_eye_bottom) / face_height
            
            # Default to neutral
            emotion = "neutral"
            confidence = 0.5
            
            # Simple threshold rules
            if mouth_openness > 0.4 and eye_openness > 0.3:
                emotion = "surprised"
                confidence = 0.8
            elif mouth_width > 0.9 and left_eyebrow_slope > -0.05:
                # Wide mouth, eyebrows not pulled down
                emotion = "happy"
                confidence = 0.8
            elif eyebrow_furrow < 0.6 and left_eyebrow_slope < -0.05:
                # Eyebrows pulled together and down
                emotion = "angry"
                confidence = 0.7
            elif left_eyebrow_slope > 0.1 and right_eyebrow_slope > 0.1 and mouth_width < 0.8:
                # Inner eyebrows pulled up
                emotion = "sad"
                confidence = 0.6
                
            logger.debug(f"Detected face emotion: {emotion}")
            
            # Publish face emotion result
            await event_bus.publish('emotion.face', {
                "emotion": emotion,
                "confidence": confidence
            })
            
        except Exception as e:
            logger.error(f"Face emotion analysis error: {e}")
