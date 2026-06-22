import asyncio
import logging
import cv2
import numpy as np
import time
from robot.services.event_bus import event_bus
from robot.services.service_registry import service_registry

logger = logging.getLogger(__name__)

class GestureRecognizer:
    """
    Recognizes simple gestures using an OpenCV optical flow / motion support layer
    instead of heavy MediaPipe dependencies.
    """
    
    def __init__(self):
        # OpenCV background subtractor for motion detection (lightweight support layer)
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=20, varThreshold=25, detectShadows=False)
        
        # Gesture state tracking
        self.last_gesture = None
        self.gesture_cooldown = 2.0  # seconds between same gesture
        self.last_gesture_time = 0
        
        # Motion history for wave detection
        self.motion_history = []
        
        # Subscribe to camera frames
        event_bus.subscribe('perception.frame', self._on_frame)
        
    async def _on_frame(self, event_type: str, payload: dict):
        """Process camera frames for gesture/motion recognition."""
        try:
            # We use frame_bgr for OpenCV processing
            frame_bgr = payload.get("frame_bgr")
            if frame_bgr is None:
                return
            
            # 1. Downscale frame for ultra-fast OpenCV processing
            small_frame = cv2.resize(frame_bgr, (160, 120))
            gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
            
            # 2. Apply background subtraction
            fg_mask = self.bg_subtractor.apply(gray)
            
            # 3. Find contours of moving objects
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            gesture = None
            if contours:
                # Find the largest moving contour
                largest_contour = max(contours, key=cv2.contourArea)
                area = cv2.contourArea(largest_contour)
                
                # If there's a significant moving object (like a waving hand)
                if area > 300:
                    M = cv2.moments(largest_contour)
                    if M["m00"] != 0:
                        cx = int(M["m10"] / M["m00"])
                        self.motion_history.append((cx, time.time()))
                        
                        # Keep only recent motion (last 1.0 second)
                        self.motion_history = [(x, t) for (x, t) in self.motion_history if time.time() - t < 1.0]
                        
                        # If we have enough motion history, check if x-coordinate oscillates (waving)
                        if len(self.motion_history) > 5:
                            xs = [x for x, t in self.motion_history]
                            dx = max(xs) - min(xs)
                            if dx > 30:  # significant side-to-side movement
                                gesture = "wave"

            if gesture and gesture != self.last_gesture:
                current_time = time.time()
                if current_time - self.last_gesture_time > self.gesture_cooldown:
                    self.last_gesture = gesture
                    self.last_gesture_time = current_time
                    await self._trigger_gesture_response(gesture)
                            
        except Exception as e:
            logger.error(f"Gesture/Motion recognition error: {e}")
    
    async def _trigger_gesture_response(self, gesture):
        """Trigger appropriate response based on recognized gesture."""
        logger.info(f"Gesture recognized: {gesture}")
        
        engine = service_registry.get("engine")
        if not engine:
            return
            
        if gesture == "thumbs_up":
            await event_bus.publish('tts.speak', {"text": "Thumbs up to you too, Friend!"})
            await event_bus.publish('ui.expression.change', 'happy')
        elif gesture == "wave":
            await event_bus.publish('tts.speak', {"text": "Hello there!"})
            await event_bus.publish('ui.animation.trigger', {"type": "wave"})
            await event_bus.publish('ui.expression.change', 'happy')
        elif gesture == "point_left":
            # Speak response
            await event_bus.publish("tts.synthesize", "Looking left!")
