import cv2
import asyncio
import logging
import numpy as np
import time
from insightface.app import FaceAnalysis
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
        self.picam2 = None
        self.use_picamera2 = False
        self._is_running = False

        # Initialize InsightFace centrally (replaces MediaPipe)
        # 'buffalo_s' is lightweight and perfectly compatible with aarch64
        self.app = FaceAnalysis(name='buffalo_s', allowed_modules=['recognition', 'detection'])
        self.app.prepare(ctx_id=0, det_size=(640, 640)) # 0 means CPU/auto

    def start(self):
        if self._is_running:
            return
            
        try:
            import sys
            # Allow importing system-installed picamera2 from within a virtual environment
            if '/usr/lib/python3/dist-packages' not in sys.path:
                sys.path.append('/usr/lib/python3/dist-packages')
                
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(main={"format": 'RGB888', "size": (640, 480)})
            self.picam2.configure(config)
            self.picam2.start()
            self.use_picamera2 = True
            self._is_running = True
            logger.info("Started camera using Picamera2")
            return
        except ImportError:
            logger.info("Picamera2 not found, falling back to cv2.VideoCapture")
        except Exception as e:
            logger.warning(f"Picamera2 init failed: {e}. Falling back to cv2.VideoCapture")

        # Optimize for lowest latency: small buffer, MJPG format if possible
        # Use V4L2 backend for Raspberry Pi
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_V4L2)
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
        if self.use_picamera2 and self.picam2:
            self.picam2.stop()
            self.picam2 = None
        elif self.cap:
            self.cap.release()
            self.cap = None
        logger.info("Stopped camera")

    async def run_loop(self):
        loop = asyncio.get_running_loop()
        
        while self._is_running:
            start_t = time.time()
            try:
                frame = None
                rgb_frame = None
                ret = False

                if self.use_picamera2:
                    # Picamera2 returns RGB by default with RGB888 config
                    rgb_frame = await loop.run_in_executor(None, self.picam2.capture_array)
                    if rgb_frame is not None:
                        ret = True
                        frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
                else:
                    # Read frame in executor
                    ret, frame = await loop.run_in_executor(None, self.cap.read)
                    if ret:
                        # 1. Convert to RGB ONE time
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                if ret:
                    rgb_frame.flags.writeable = False
                    
                    # Quick check if frame is pitch black (could be virtual camera / blocked)
                    if getattr(self, '_frame_count', 0) % 30 == 0:
                        brightness = np.mean(frame)
                        if brightness < 5.0:
                            logger.warning(f"Camera frame is very dark! (brightness: {brightness:.2f}). Is it covered?")
                        elif brightness > 250.0:
                            logger.warning(f"Camera frame is completely white! (brightness: {brightness:.2f})")
                    self._frame_count = getattr(self, '_frame_count', 0) + 1

                    # 2. Run InsightFace ONE time centrally
                    def _detect():
                        return self.app.get(frame)  # InsightFace uses BGR
                        
                    faces = await loop.run_in_executor(None, _detect)
                    
                    target_face = None
                    if faces:
                        # Assume largest face is target
                        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
                        target_face = faces[0]

                    # 3. Publish rich payload
                    payload = {
                        "frame_bgr": frame,
                        "frame_rgb": rgb_frame,
                        "insight_face": target_face,
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
