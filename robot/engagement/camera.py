import cv2
import asyncio
import logging
import numpy as np
import mediapipe as mp
import time
from robot.config.settings import settings
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class CameraManager:
    """
    Centralized camera and perception pipeline.
    Captures frames, runs MediaPipe Face Mesh ONCE per frame, 
    and publishes a rich 'perception.frame' event to prevent duplicate processing.
    """
    
    def __init__(self):
        self.camera_index = settings.perception.CAMERA_INDEX
        self.fps = settings.perception.FPS
        self.cap = None
        self._is_running = False

        # Initialize MediaPipe Face Mesh centrally
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,  # Need this for Iris (EAR)
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )

    def start(self):
        if self._is_running:
            return
            
        # Optimize for lowest latency: small buffer, MJPG format if possible
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW) if cv2.CAP_DSHOW else cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            logger.error(f"Failed to open camera at index {self.camera_index}")
            return
            
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Prevent frame queueing/latency
        
        self._is_running = True
        logger.info(f"Started camera {self.camera_index} with central Face Mesh")

    def stop(self):
        self._is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.face_mesh.close()
        logger.info("Stopped camera")

    async def run_loop(self):
        loop = asyncio.get_running_loop()
        
        while self._is_running:
            start_t = time.time()
            try:
                # Read frame in executor
                ret, frame = await loop.run_in_executor(None, self.cap.read)
                
                if ret:
                    # 1. Convert to RGB ONE time
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    rgb_frame.flags.writeable = False
                    
                    # Quick check if frame is pitch black (could be virtual camera / blocked)
                    if getattr(self, '_frame_count', 0) % 30 == 0:
                        brightness = np.mean(frame)
                        if brightness < 5.0:
                            logger.warning(f"Camera frame is very dark! (brightness: {brightness:.2f}). Is it covered?")
                        elif brightness > 250.0:
                            logger.warning(f"Camera frame is completely white! (brightness: {brightness:.2f})")
                    self._frame_count = getattr(self, '_frame_count', 0) + 1

                    # 2. Run MediaPipe Face Mesh ONE time
                    # This replaces the need for separate face detection everywhere
                    results = await loop.run_in_executor(None, self.face_mesh.process, rgb_frame)

                    # 3. Publish rich payload
                    payload = {
                        "frame_bgr": frame,
                        "frame_rgb": rgb_frame,
                        "face_landmarks": results.multi_face_landmarks[0] if results.multi_face_landmarks else None,
                        "timestamp": start_t
                    }
                    
                    await event_bus.publish('perception.frame', payload)
                else:
                    logger.warning("Failed to read from camera.")
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Camera loop error: {e}")
                
            # Maintain target FPS
            elapsed = time.time() - start_t
            sleep_time = max(0, (1.0 / self.fps) - elapsed)
            await asyncio.sleep(sleep_time)
