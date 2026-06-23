import logging
import numpy as np
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class EngagementDetector:
    """
    Subscribes to 'perception.frame'. 
    Computes Head Pose (Pitch/Yaw) using InsightFace 5-point keypoints (kps).
    """
    
    def __init__(self):
        event_bus.subscribe('perception.frame', self._process_perception)

    async def _process_perception(self, event_type: str, payload: dict):
        face = payload.get("insight_face")
        
        engaged = False
        score = 0.0
        pitch = 0.0
        yaw = 0.0
        
        if face is not None and hasattr(face, 'kps'):
            # kps is shape (5, 2)
            left_eye = face.kps[0]
            right_eye = face.kps[1]
            nose = face.kps[2]
            
            # Simple 2D heuristic for Yaw (left/right turning)
            # Ratio of left eye to nose vs right eye to nose
            left_dist = abs(nose[0] - left_eye[0])
            right_dist = abs(nose[0] - right_eye[0])
            total_dist = left_dist + right_dist
            
            if total_dist > 0:
                # Yaw approx between -1.0 (looking left) and 1.0 (looking right)
                yaw = (right_dist - left_dist) / total_dist
            
            # Pitch approx (up/down)
            # We don't have a specific chin point in 5-point kps, but we have bbox bottom
            eye_y = (left_eye[1] + right_eye[1]) / 2
            chin_y = face.bbox[3]
            upper_dist = abs(nose[1] - eye_y)
            lower_dist = abs(nose[1] - chin_y)
            total_h = upper_dist + lower_dist
            if total_h > 0:
                pitch = (lower_dist - upper_dist) / total_h
                
            # If yaw is within bounds (-0.4 to 0.4) and pitch is reasonable, engaged!
            if abs(yaw) < 0.4 and pitch > 0:
                engaged = True
                score = 1.0 - abs(yaw) # Higher score when looking directly at camera
            else:
                score = 0.3 # Face detected but looking away
        
        # Publish engagement state
        await event_bus.publish('engagement.update', {
            "engaged": engaged,
            "score": float(score),
            "pitch": float(pitch),
            "yaw": float(yaw)
        })
