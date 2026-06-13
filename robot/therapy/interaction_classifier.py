import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class InteractionClassifier:
    """Classifies incoming text into interaction categories to route to the correct logic."""

    # Keywords mapping for simple intent detection
    INTENT_KEYWORDS = {
        "emotion_coaching": [r"\b(sad|angry|mad|happy|scared|frustrated|feel)\b"],
        "game_session": [r"\b(play|game|start|level)\b"],
        "attention_training": [r"\b(look|watch|focus)\b"],
        "speech_practice": [r"\b(say|repeat|word)\b"],
    }

    def classify(self, text: str, current_activity: str) -> str:
        """
        Determine the interaction type based on text and current state.
        
        Returns:
            str: One of ['casual_conversation', 'therapy_session', 'game_session', 
                        'emotion_coaching', 'speech_practice', 'attention_training']
        """
        text_lower = text.lower()

        # If we are already in a game, default to continuing the game
        if current_activity == "game_session":
            # Check if they want to stop playing
            if re.search(r"\b(stop|quit|done|bored)\b", text_lower):
                logger.debug("Child wants to stop game. Switching to casual.")
                return "casual_conversation"
            return "game_session"

        # Check keyword patterns
        for intent, patterns in self.INTENT_KEYWORDS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    logger.debug(f"Interaction classified as {intent} due to keyword match.")
                    return intent

        # Default to casual conversation
        return "casual_conversation"
