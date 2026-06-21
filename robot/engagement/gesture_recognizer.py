import asyncio
import logging
import mediapipe as mp
import numpy as np
from robot.services.event_bus import event_bus
from robot.services.service_registry import service_registry

logger = logging.getLogger(__name__)

class GestureRecognizer:
    """Recognizes hand gestures using MediaPipe Hands."""
    
    def __init__(self):
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.7,
            min_tracking_confidence=0.5
        )
        
        # Gesture state tracking
        self.last_gesture = None
        self.gesture_cooldown = 2.0  # seconds between same gesture
        self.last_gesture_time = 0
        
        # Wave detection state
        self.wave_history = []
        self.wave_threshold = 5  # number of frames to detect wave
        
        # Subscribe to camera frames
        event_bus.subscribe('perception.frame', self._on_frame)
        
    async def _on_frame(self, event_type: str, payload: dict):
        """Process camera frames for gesture recognition."""
        try:
            frame_rgb = payload.get("frame_rgb")
            if frame_rgb is None:
                return
            
            # Process hands
            results = await asyncio.get_event_loop().run_in_executor(
                None, self.hands.process, frame_rgb
            )
            
            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    gesture = self._detect_gesture(hand_landmarks, frame_rgb.shape)
                    
                    if gesture and gesture != self.last_gesture:
                        current_time = asyncio.get_event_loop().time()
                        if current_time - self.last_gesture_time > self.gesture_cooldown:
                            self.last_gesture = gesture
                            self.last_gesture_time = current_time
                            await self._trigger_gesture_response(gesture)
                            
        except Exception as e:
            logger.error(f"Gesture recognition error: {e}")
    
    def _detect_gesture(self, landmarks, frame_shape):
        """Detect which gesture is being performed."""
        # Get key landmarks
        wrist = landmarks.landmark[0]
        thumb_tip = landmarks.landmark[4]
        index_tip = landmarks.landmark[8]
        middle_tip = landmarks.landmark[12]
        ring_tip = landmarks.landmark[16]
        pinky_tip = landmarks.landmark[20]
        
        # Check for thumbs up
        if self._is_thumbs_up(landmarks):
            return "thumbs_up"
        
        # Check for pointing left
        if self._is_pointing_left(landmarks):
            return "point_left"
        
        # Check for waving (requires temporal analysis)
        if self._is_waving(wrist, frame_shape):
            return "wave"
        
        return None
    
    def _is_thumbs_up(self, landmarks):
        """Check if hand is in thumbs up position."""
        thumb_tip = landmarks.landmark[4]
        thumb_ip = landmarks.landmark[3]
        index_tip = landmarks.landmark[8]
        middle_tip = landmarks.landmark[12]
        ring_tip = landmarks.landmark[16]
        pinky_tip = landmarks.landmark[20]
        
        # Thumb should be extended upward (tip above IP joint)
        thumb_extended = thumb_tip.y < thumb_ip.y
        
        # Other fingers should be curled down
        fingers_curled = (
            index_tip.y > landmarks.landmark[6].y and
            middle_tip.y > landmarks.landmark[10].y and
            ring_tip.y > landmarks.landmark[14].y and
            pinky_tip.y > landmarks.landmark[18].y
        )
        
        return thumb_extended and fingers_curled
    
    def _is_pointing_left(self, landmarks):
        """Check if hand is pointing left."""
        index_tip = landmarks.landmark[8]
        index_mcp = landmarks.landmark[5]
        middle_tip = landmarks.landmark[12]
        ring_tip = landmarks.landmark[16]
        pinky_tip = landmarks.landmark[20]
        
        # Index finger should be extended
        index_extended = abs(index_tip.x - index_mcp.x) > 0.1
        
        # Index finger should point left (smaller x value than MCP)
        pointing_left = index_tip.x < index_mcp.x - 0.05
        
        # Other fingers should be curled
        fingers_curled = (
            middle_tip.y > landmarks.landmark[10].y and
            ring_tip.y > landmarks.landmark[14].y and
            pinky_tip.y > landmarks.landmark[18].y
        )
        
        return index_extended and pointing_left and fingers_curled
    
    def _is_waving(self, wrist, frame_shape):
        """Check if hand is waving (requires temporal analysis)."""
        current_time = asyncio.get_event_loop().time()
        
        # Track wrist x position over time
        self.wave_history.append((current_time, wrist.x))
        
        # Keep only recent history (last 2 seconds)
        self.wave_history = [(t, x) for t, x in self.wave_history if current_time - t < 2.0]
        
        # Need enough history
        if len(self.wave_history) < self.wave_threshold:
            return False
        
        # Check for oscillating motion
        x_positions = [x for _, x in self.wave_history]
        
        # Calculate direction changes
        direction_changes = 0
        for i in range(2, len(x_positions)):
            if (x_positions[i] - x_positions[i-1]) * (x_positions[i-1] - x_positions[i-2]) < 0:
                direction_changes += 1
        
        # Wave if enough direction changes
        if direction_changes >= 3:
            self.wave_history = []  # Reset after detecting wave
            return True
        
        return False
    
    async def _trigger_gesture_response(self, gesture: str):
        """Trigger appropriate response for detected gesture."""
        logger.info(f"Detected gesture: {gesture}")
        
        if gesture == "wave":
            # Trigger wave animation
            await event_bus.publish('ui.animation.trigger', {"type": "wave"})
            # Also speak
            # Note: We publish to event bus instead of calling tts.speak()
            await event_bus.publish("tts.synthesize", "Hi there!")
                
        elif gesture == "thumbs_up":
            # Trigger celebration animation
            await event_bus.publish('ui.animation.trigger', {"type": "confetti"})
            # Speak response
            await event_bus.publish("tts.synthesize", "Great job!")
                
        elif gesture == "point_left":
            # Trigger look left animation
            await event_bus.publish('ui.animation.trigger', {"type": "look_left"})
            # Speak response
            await event_bus.publish("tts.synthesize", "Looking left!")
