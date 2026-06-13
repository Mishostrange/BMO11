import cv2
import logging
import numpy as np
import mediapipe as mp
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class EngagementDetector:
    """Uses MediaPipe to detect if the child is looking at the robot."""
    
    def __init__(self):
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=0, # 0 for close range (up to 2m)
            min_detection_confidence=0.5
        )
        
        # Subscribe to camera frames
        event_bus.subscribe('camera.frame', self._process_frame)

    async def _process_frame(self, event_type: str, frame: np.ndarray):
        """Process a raw camera frame to determine engagement."""
        # Convert BGR to RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # To improve performance, optionally mark the image as not writeable
        rgb_frame.flags.writeable = False
        results = self.face_detection.process(rgb_frame)
        
        engaged = False
        score = 0.0
        
        if results.detections:
            # We assume the most prominent face is the child
            detection = results.detections[0]
            
            # Simple heuristic: if face is detected with high confidence, they are engaged
            score = detection.score[0]
            if score > 0.6:
                engaged = True
                
            # A more advanced version would use FaceMesh to get head pose estimation
            # to see if they are actually looking at the camera.
                
        # Publish engagement state
        await event_bus.publish('engagement.update', {
            "engaged": engaged,
            "score": float(score)
        })
