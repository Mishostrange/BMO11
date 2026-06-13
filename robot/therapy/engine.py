"""
therapy/engine.py
─────────────────────────────────────────────────────────────────────────────
Central orchestrator: STT → intent → memory → LLM → TTS.

Upgrades over v1
────────────────
• Uses MemoryManager as single memory entry point (STM+LTM+Vector).
• Reads dominant emotion from STM rolling window instead of latest event only.
• Checks for sustained frustration (3+ consecutive negative readings).
• Records successful response strategies into vector memory.
• Fires 'session.started' / 'session.ended' events with proper payloads.
"""

import logging
from typing import Dict, Any

from robot.therapy.interaction_classifier import InteractionClassifier
from robot.therapy.frustration_detector   import FrustrationDetector
from robot.therapy.rules                  import TherapyRules
from robot.llm.prompt_templates           import get_prompt
from robot.services.event_bus             import event_bus

logger = logging.getLogger(__name__)


class TherapyEngine:
    """Central orchestrator for AI logic. Sits between STT and LLM."""

    def __init__(self, provider_manager, memory_manager, session_manager):
        self.provider_manager = provider_manager
        self.mem              = memory_manager        # MemoryManager facade
        self.stm              = memory_manager.stm    # direct STM reference
        self.ltm              = memory_manager.ltm    # direct LTM reference
        self.session_manager  = session_manager

        self.classifier         = InteractionClassifier()
        self.frustration_detector = FrustrationDetector()
        self.rules              = TherapyRules()

        # Subscribe to STT events
        event_bus.subscribe("speech.transcribed", self._on_speech_transcribed)

        # Sustained negative emotion from EmotionTracker
        event_bus.subscribe("emotion.sustained",  self._on_emotion_sustained)

        # Live emotion snapshot
        self._latest_emotion: Dict[str, Any] = {}

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_emotion_sustained(self, _event: str, data: Dict[str, Any]):
        """Forced comfort-mode entry when emotion tracker fires sustained event."""
        if data.get("is_negative"):
            child_id = data.get("child_id") or self.session_manager.active_child_id
            logger.warning(
                f"[TherapyEngine] Sustained negative emotion '{data.get('emotion')}'. "
                "Entering comfort mode."
            )
            self.stm.set_activity("comfort_mode")
            await event_bus.publish(
                "safety.alert",
                {
                    "type":     "sustained_emotion",
                    "emotion":  data.get("emotion"),
                    "level":    5,
                    "child_id": child_id,
                },
            )

    async def _on_speech_transcribed(self, _event: str, text: str):
        """Main pipeline entry point from STT."""
        if not self.session_manager.active_child_id:
            logger.warning("[TherapyEngine] Speech received but no active session.")
            return

        child_id = self.session_manager.active_child_id

        # ── 1. Route user turn through MemoryManager ──────────────────────────
        self.mem.add_user_turn(text, child_id)

        # ── 2. Profile ────────────────────────────────────────────────────────
        profile = self.ltm.get_child_profile(child_id) or {"name": "Friend"}

        # ── 3. Classify interaction intent ────────────────────────────────────
        interaction_type = self.classifier.classify(text, self.stm.current_activity)
        self.mem.set_activity(interaction_type)

        # ── 4. Emotion-aware frustration detection ────────────────────────────
        # Use STM dominant emotion (rolling window) for more stability
        dom_emotion, dom_conf = self.stm.get_dominant_emotion()
        emotion_payload = {"emotion": dom_emotion, "confidence": dom_conf}

        frustration = self.frustration_detector.check(text, child_id, emotion_payload)

        # Sustained frustration check (3 consecutive negative)
        if self.stm.is_sustained_frustration(threshold=3) or frustration >= 4:
            interaction_type = "comfort_mode"
            self.mem.set_activity("comfort_mode")
            logger.info("[TherapyEngine] Entering comfort mode (frustration).")
            await event_bus.publish(
                "safety.alert",
                {
                    "type":     "high_frustration",
                    "level":    frustration,
                    "child_id": child_id,
                },
            )

        # ── 5. Apply therapy rules ────────────────────────────────────────────
        active_rules = self.rules.get_active_rules(profile, interaction_type, frustration)

        # ── 6. Build context for LLM ──────────────────────────────────────────
        full_context = self.mem.build_llm_context(child_id, situation=text)

        system_prompt = get_prompt(
            interaction_type,
            child_name=profile.get("name", "Friend"),
            context=full_context,
        )
        if active_rules:
            system_prompt += f"\n\n{active_rules}"

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.mem.get_recent_messages())

        logger.debug(f"[TherapyEngine] Intent: {interaction_type} | Frustration: {frustration}")

        # ── 7. Query LLM ──────────────────────────────────────────────────────
        try:
            await event_bus.publish("ui.expression.change", "thinking")

            response_text = await self.provider_manager.chat(messages)
            logger.info(f"BMO: {response_text}")

            # ── 8. Route response through MemoryManager ───────────────────────
            self.mem.add_assistant_turn(response_text)

            # Record strategy if the interaction was therapy/game (score unknown here;
            # ProgressTracker handles detailed scoring via game.finished events)
            if interaction_type not in ("casual_conversation", "comfort_mode"):
                self.mem.record_successful_strategy(
                    child_id,
                    f"{interaction_type}: {text[:80]} → {response_text[:80]}",
                    score=0.7,
                )

            # ── 9. TTS ───────────────────────────────────────────────────────
            await event_bus.publish("tts.synthesize", response_text)

        except Exception as e:
            logger.error(f"[TherapyEngine] LLM pipeline error: {e}")
            await event_bus.publish("ui.expression.change", "sad")

    # ── Session helpers ───────────────────────────────────────────────────────

    async def start_session(self, child_id: int, session_type: str = "casual"):
        """Called externally (e.g. wake-word handler) to begin a session."""
        session_id = self.session_manager.start_session(child_id, session_type)
        self.mem.set_active_child(child_id)

        await event_bus.publish("session.started", {
            "child_id":   child_id,
            "session_id": session_id,
        })
        logger.info(f"[TherapyEngine] Session {session_id} started for child {child_id}.")
        return session_id

    async def end_session(self):
        """Called to gracefully terminate the current session."""
        child_id   = self.session_manager.active_child_id
        session_id = self.session_manager.active_session_id

        # Build a session summary for memory consolidation
        messages  = self.mem.get_recent_messages()
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        summary   = "Session: " + " ".join(user_msgs[-5:])[:400]

        self.session_manager.end_session()

        await event_bus.publish("session.ended", {
            "child_id":   child_id,
            "session_id": session_id,
            "summary":    summary,
        })

        self.mem.clear_session()
        logger.info(f"[TherapyEngine] Session {session_id} ended for child {child_id}.")
