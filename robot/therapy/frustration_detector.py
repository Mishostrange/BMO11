import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class FrustrationDetector:
    """Detects frustration levels from text and external signals."""

    FRUSTRATION_KEYWORDS = [
        r"\b(can't|cannot|too hard|don't want to|no|stop|quit|tired)\b",
        r"\b(stupid|dumb|hate|angry)\b"
    ]

    def __init__(self):
        # We will keep track of recent frustration events to detect sustained frustration
        self.frustration_history = []

    def check(self, text: str, child_id: int, emotion_state: Dict[str, Any] = None) -> int:
        """
        Check for frustration.
        Returns level 0-5 (0 = none, 5 = severe).
        """
        level = 0
        text_lower = text.lower()

        # Check verbal cues
        for pattern in self.FRUSTRATION_KEYWORDS:
            if re.search(pattern, text_lower):
                level += 2
                break # Don't double count keywords

        # Combine with emotion state if available
        if emotion_state:
            emotion = emotion_state.get('emotion', 'neutral')
            if emotion in ['angry', 'frustrated']:
                level += 3
            elif emotion == 'sad':
                level += 2

        # Cap at 5
        level = min(level, 5)
        
        if level > 0:
            logger.info(f"Detected frustration level {level} for child {child_id}")
            
        return level
