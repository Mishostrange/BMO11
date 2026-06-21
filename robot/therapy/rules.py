import logging

logger = logging.getLogger(__name__)

class TherapyRules:
    """Applies therapy rules to generate constraints for the LLM."""

    def get_active_rules(self, profile: dict, interaction_type: str, frustration_level: int, attention_score: float = 0.5, current_emotion: str = "neutral") -> str:
        """
        Generate a string of rules to append to the LLM system prompt.
        """
        rules = []

        # Communication Level Rules
        comm_level = profile.get('communication_level', 'verbal')
        if comm_level == 'nonverbal':
            rules.append("Use extremely simple, 1-3 word sentences.")
            rules.append("Focus on yes/no questions.")
        elif comm_level == 'limited':
            rules.append("Use short, simple sentences. Max 5 words per sentence.")
            rules.append("Give only one instruction at a time.")

        # Emotion & Attention Rules
        if frustration_level >= 3 or current_emotion in ["angry", "frustrated"]:
            rules.append("The child is frustrated. Be extremely gentle. Validate their feelings.")
            rules.append("Do NOT ask them to do a task right now.")
            if interaction_type == "game_session":
                rules.append("Suggest taking a break from the game.")
        
        if attention_score < 0.4:
            rules.append("The child is DISTRACTED. You MUST say their name to get their attention.")
            rules.append("Do something surprising or ask a very silly question to regain their focus.")
        elif attention_score > 0.8:
            rules.append("The child is highly engaged! Give them enthusiastic praise.")

        # Interaction specific rules
        if interaction_type == "game_session":
            rules.append("Wait for the child to answer before moving to the next question.")

        if not rules:
            return ""
            
        return "CRITICAL RULES FOR THIS RESPONSE:\n- " + "\n- ".join(rules)
