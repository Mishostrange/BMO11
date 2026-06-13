import cv2
import asyncio
import logging
from robot.config.settings import settings
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class CameraManager:
    """Manages the USB camera capture in a background task."""
    
    def __init__(self):
        self.camera_index = settings.perception.CAMERA_INDEX
        self.fps = settings.perception.FPS
        self.cap = None
        self._is_running = False

    def start(self):
        if self._is_running:
            return
            
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            logger.error(f"Failed to open camera at index {self.camera_index}")
            return
            
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Set a lower resolution for better performance on RPi5
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        self._is_running = True
        logger.info(f"Started camera {self.camera_index}")

    def stop(self):
        self._is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        logger.info("Stopped camera")

    async def run_loop(self):
        """Async loop to read frames and publish them."""
        loop = asyncio.get_running_loop()
        
        while self._is_running:
            try:
                # Read frame in executor to not block async loop
                ret, frame = await loop.run_in_executor(None, self.cap.read)
                
                if ret:
                    # Publish the raw frame for downstream processors (engagement, emotion)
                    # We copy it to ensure thread safety if multiple subscribers modify it
                    await event_bus.publish('camera.frame', frame.copy())
                else:
                    logger.warning("Failed to read from camera.")
                    await asyncio.sleep(1) # Prevent tight loop on failure
                    
            except Exception as e:
                logger.error(f"Camera loop error: {e}")
                
            # Yield to maintain target FPS roughly
            await asyncio.sleep(1.0 / self.fps)
