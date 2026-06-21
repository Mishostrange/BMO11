import logging
import numpy as np
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class EngagementDetector:
    """
    Subscribes to 'perception.frame' which already contains MediaPipe Face Mesh landmarks.
    Computes Head Pose (Pitch/Yaw) and Eye Aspect Ratio (EAR) to determine engagement.
    """
    
    def __init__(self):
        # We don't need to initialize MediaPipe here anymore, camera.py does it!
        event_bus.subscribe('perception.frame', self._process_perception)

    async def _process_perception(self, event_type: str, payload: dict):
        landmarks = payload.get("face_landmarks")
        
        engaged = False
        score = 0.0
        pitch = 0.0
        yaw = 0.0
        
        if landmarks:
            # Face is present, calculate head pose
            # Extract 3D coordinates for key landmarks
            # Nose tip
            nose = landmarks.landmark[1]
            # Chin
            chin = landmarks.landmark[152]
            # Left eye left corner
            left_eye = landmarks.landmark[33]
            # Right eye right corner
            right_eye = landmarks.landmark[263]
            
            # Simple 2D heuristic for Yaw (left/right turning)
            # Ratio of left eye to nose vs right eye to nose
            left_dist = abs(nose.x - left_eye.x)
            right_dist = abs(nose.x - right_eye.x)
            total_dist = left_dist + right_dist
            
            if total_dist > 0:
                # Yaw approx between -1.0 (looking left) and 1.0 (looking right)
                yaw = (right_dist - left_dist) / total_dist
            
            # Pitch approx (up/down)
            # Ratio of nose to eyes vs nose to chin
            eye_y = (left_eye.y + right_eye.y) / 2
            upper_dist = abs(nose.y - eye_y)
            lower_dist = abs(nose.y - chin.y)
            total_h = upper_dist + lower_dist
            if total_h > 0:
                pitch = (lower_dist - upper_dist) / total_h
                
            # If yaw is within bounds (-0.3 to 0.3) and pitch is reasonable, engaged!
            if abs(yaw) < 0.4 and pitch > 0:
                engaged = True
                score = 1.0 - abs(yaw) # Higher score when looking directly at camera
            else:
                score = 0.3 # Face detected but looking away
        
        # Publish engagement state
        await event_bus.publish('engagement.update', {
            "engaged": engaged,
            "score": float(score),
            "pitch": pitch,
            "yaw": yaw
        })
