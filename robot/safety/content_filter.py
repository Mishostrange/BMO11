import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

class ContentFilter:
    """Ensures all inputs and outputs are safe and age-appropriate."""
    
    # In a real application, these lists would be much more comprehensive
    # and potentially use a lightweight ML classification model.
    UNSAFE_WORDS = [
        r"\b(kill|die|dead|blood|hurt|stupid|idiot|hate|ugly)\b",
        r"\b(scary|monster|ghost)\b"
    ]
    
    def __init__(self):
        self.unsafe_patterns = [re.compile(p, re.IGNORECASE) for p in self.UNSAFE_WORDS]

    def check_input(self, text: str) -> Tuple[bool, str]:
        """
        Check if the child's input contains unsafe topics.
        Returns: (is_safe, category_or_none)
        """
        for pattern in self.unsafe_patterns:
            if pattern.search(text):
                logger.warning(f"Unsafe input detected: {text}")
                return False, "unsafe_topic"
        return True, None

    def filter_output(self, text: str) -> str:
        """
        Sanitize the LLM output before speaking.
        If it's too bad, return a fallback safe phrase.
        """
        for pattern in self.unsafe_patterns:
            if pattern.search(text):
                logger.error(f"LLM generated unsafe output! Blocked. Output: {text}")
                return "I don't think we should talk about that. Let's play a game instead!"
                
        return text
