import logging
import asyncio
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class EmotionFusion:
    """Fuses multi-modal emotion signals (face + voice) into a single confident prediction."""
    
    def __init__(self):
        self.latest_face = {"emotion": "neutral", "confidence": 0.0, "timestamp": 0}
        self.latest_voice = {"emotion": "neutral", "confidence": 0.0, "timestamp": 0}
        
        event_bus.subscribe('emotion.face', self._on_face_emotion)
        event_bus.subscribe('emotion.voice', self._on_voice_emotion)

    async def _on_face_emotion(self, event_type: str, data: dict):
        self.latest_face = data
        self.latest_face["timestamp"] = asyncio.get_event_loop().time()
        await self._fuse_and_publish()

    async def _on_voice_emotion(self, event_type: str, data: dict):
        self.latest_voice = data
        self.latest_voice["timestamp"] = asyncio.get_event_loop().time()
        await self._fuse_and_publish()

    async def _fuse_and_publish(self):
        current_time = asyncio.get_event_loop().time()
        
        # Discard old signals (e.g., older than 3 seconds)
        face_valid = (current_time - self.latest_face["timestamp"]) < 3.0
        voice_valid = (current_time - self.latest_voice["timestamp"]) < 3.0
        
        fused_emotion = "neutral"
        fused_confidence = 0.0
        
        if face_valid and voice_valid:
            # Both signals available: weight by confidence
            if self.latest_face["emotion"] == self.latest_voice["emotion"]:
                fused_emotion = self.latest_face["emotion"]
                # Boost confidence if they agree
                fused_confidence = min(1.0, self.latest_face["confidence"] + self.latest_voice["confidence"] * 0.5)
            else:
                # Disagree: pick the one with higher confidence
                if self.latest_face["confidence"] > self.latest_voice["confidence"]:
                    fused_emotion = self.latest_face["emotion"]
                    fused_confidence = self.latest_face["confidence"]
                else:
                    fused_emotion = self.latest_voice["emotion"]
                    fused_confidence = self.latest_voice["confidence"]
        elif face_valid:
            fused_emotion = self.latest_face["emotion"]
            fused_confidence = self.latest_face["confidence"]
        elif voice_valid:
            fused_emotion = self.latest_voice["emotion"]
            fused_confidence = self.latest_voice["confidence"]
            
        # Only publish if confidence is high enough
        if fused_confidence > 0.4:
            logger.debug(f"Fused Emotion: {fused_emotion} (Conf: {fused_confidence:.2f})")
            await event_bus.publish('emotion.detected', {
                "emotion": fused_emotion,
                "confidence": fused_confidence
            })
