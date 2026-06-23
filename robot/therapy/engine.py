"""
therapy/engine.py
─────────────────────────────────────────────────────────────────────────────
Central orchestrator: STT → StateManager → Emotional Engine → Memory → LLM → TTS.

Architecture (v3 — Companion Edition)
──────────────────────────────────────
• CompanionStateManager is the single source of truth for system state.
• EmotionalContinuityEngine computes emotion in Python (NOT delegated to LLM).
• MemoryManager persists last_emotion, last_game, last_topic across sessions.
• Prompt builder uses strict priority: Emotion → Context → Memory → Task → Output.
• Decision engine injects soft, optional hints — never forces tasks.
• Games are delegated entirely to GameOrchestrator (state = IN_GAME).
"""

import logging
import time
from typing import Dict, Any

from robot.therapy.interaction_classifier import InteractionClassifier
from robot.therapy.frustration_detector   import FrustrationDetector
from robot.therapy.rules                  import TherapyRules
from robot.therapy.state_manager          import state_manager, CompanionState
from robot.therapy.emotional_continuity   import emotion_engine
from robot.llm.prompt_templates           import get_prompt
from robot.services.event_bus             import event_bus

logger = logging.getLogger(__name__)


class TherapyEngine:
    """Central orchestrator for AI logic. Sits between STT and LLM."""

    def __init__(self, provider_manager, memory_manager, session_manager):
        self.provider_manager = provider_manager
        self.mem              = memory_manager
        self.stm              = memory_manager.stm
        self.ltm              = memory_manager.ltm
        self.session_manager  = session_manager

        self.classifier           = InteractionClassifier()
        self.frustration_detector = FrustrationDetector()
        self.rules                = TherapyRules()

        # Game orchestrator — set externally via set_game_orchestrator()
        self.game_orchestrator = None

        # ── Subscribe to events ───────────────────────────────────────────────
        event_bus.subscribe("speech.transcribed", self._on_speech_transcribed)
        event_bus.subscribe("emotion.sustained",  self._on_emotion_sustained)
        event_bus.subscribe("face.recognized",    self._on_face_recognized)
        event_bus.subscribe("face.unknown",        self._on_face_unknown)
        event_bus.subscribe("speech.started",      self._on_speech_started)
        event_bus.subscribe("speech.ended",        self._on_speech_ended)
        event_bus.subscribe("profile.created",     self._on_profile_created)

        # Attention tracking (engagement system)
        self._latest_attention  = 0.5
        self._last_speech_time  = 0.0
        self._last_rule2_time   = 0.0
        event_bus.subscribe("engagement.update", self._on_engagement)

        # Registration state
        self.pending_registration_encoding = None
        self.is_registering = False

        # Sleep state
        self.is_asleep = False

        # Decision engine thresholds
        self._ENGAGE_LOW_THRESHOLD  = 0.3
        self._EMOTION_ACC_THRESHOLD = 0.5
        self._SOCIAL_ACC_THRESHOLD  = 0.5

        event_bus.subscribe("profile.created", self._on_profile_created)

    def set_game_orchestrator(self, orchestrator):
        self.game_orchestrator = orchestrator

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _on_engagement(self, _event: str, data: Dict[str, Any]):
        self._latest_attention = data.get("score", 0.5)

    async def _on_speech_started(self, *_):
        if state_manager.current_state != CompanionState.IN_GAME:
            state_manager.set_state(CompanionState.LISTENING)

    async def _on_speech_ended(self, *_):
        # Will transition to RESPONDING when we begin the LLM call
        if state_manager.current_state == CompanionState.LISTENING:
            state_manager.set_state(CompanionState.IDLE)

    async def _on_emotion_sustained(self, _event: str, data: Dict[str, Any]):
        """Forced comfort-mode entry when emotion tracker fires sustained event."""
        if data.get("is_negative"):
            child_id = data.get("child_id") or self.session_manager.active_child_id
            logger.warning(
                f"[TherapyEngine] Sustained negative emotion '{data.get('emotion')}'. "
                "Entering comfort mode."
            )
            self.stm.set_activity("comfort_mode")
            await event_bus.publish("safety.alert", {
                "type":     "sustained_emotion",
                "emotion":  data.get("emotion"),
                "level":    5,
                "child_id": child_id,
            })

    async def _on_profile_created(self, _event: str, new_child_id: int):
        """Auto-start a session once a new child profile has been saved."""
        if self.session_manager.active_child_id != new_child_id:
            logger.info(f"[TherapyEngine] Profile created. Starting session for child {new_child_id}")
            await self.start_session(new_child_id)

    async def _on_face_recognized(self, _event: str, child_id: int):
        """Automatically start a session if a known child is seen."""
        if self.is_asleep:
            return
        if self.session_manager.active_child_id is not None:
            if self.session_manager.active_child_id != child_id:
                logger.debug(f"[TherapyEngine] Session locked to child {self.session_manager.active_child_id}.")
            return

        logger.info(f"[TherapyEngine] Face recognized. Auto-starting session for child {child_id}")
        await self.start_session(child_id)

        profile = self.ltm.get_child_profile(child_id) or {}
        name = profile.get("name", "friend")

        # Build a personalised greeting using persistent memory
        last_emotion = self.mem._last_emotion
        last_game    = self.mem._last_game
        last_topic   = self.mem._last_topic

        if last_emotion and last_emotion in ("sad", "angry", "scared", "frustrated"):
            greeting = f"Hello {name}! I'm really glad to see you. Last time you seemed a bit {last_emotion}. How are you feeling today?"
        elif last_game:
            game_friendly = last_game.replace("_", " ").title()
            greeting = f"Welcome back, {name}! Last time we played {game_friendly}. Do you want to do something fun again?"
        elif last_topic:
            greeting = f"Hi {name}! I remember we were talking about {last_topic} last time. What would you like to talk about today?"
        else:
            topics = profile.get("favorite_topics", [])
            animals = profile.get("favorite_animals", [])
            if topics:
                greeting = f"Hello {name}! I remember you love {topics[0]}. What do you want to do today?"
            elif animals:
                greeting = f"Hello {name}! Did you know {animals[0]}s are amazing? What's on your mind today?"
            else:
                greeting = f"Hello {name}! I'm so happy to see you. What do you want to do today?"

        await event_bus.publish("tts.synthesize", greeting)

    async def _on_face_unknown(self, _event: str, encoding_bytes: bytes):
        """Triggered when an unknown face is seen for several seconds."""
        if not self.is_registering:
            if self.session_manager.active_session_id:
                return
            logger.info("[TherapyEngine] Unknown face detected. Starting registration.")
            self.is_registering = True
            self.pending_registration_encoding = encoding_bytes
            await event_bus.publish("tts.synthesize",
                "Hello! I don't think we've met before. I'm BMO. What's your name?")

    async def _on_speech_transcribed(self, _event: str, text: str):
        """Main pipeline entry point — routes through the unified event flow."""
        self._last_speech_time = time.time()

        # ── Registration intercept ─────────────────────────────────────────────
        if self.is_registering and self.pending_registration_encoding:
            await self._handle_registration(text)
            return

        text_lower = text.lower()

        # ── Goodbye ──────────────────────────────────────────────────────────
        if any(w in text_lower for w in ["goodbye", "good bye", "bye bmo"]):
            await event_bus.publish("tts.synthesize",
                "Goodbye! Say 'Hi BMO' when you want to play again.")
            if self.session_manager.active_session_id:
                await self.end_session()
            self.is_asleep = True
            state_manager.set_state(CompanionState.IDLE)
            return

        # ── Wake word ────────────────────────────────────────────────────────
        if self.is_asleep:
            if any(w in text_lower for w in ["hi bmo", "hello bmo", "wake up"]):
                self.is_asleep = False
                await event_bus.publish("tts.synthesize", "Hello! I'm here. Let me take a look at you.")
            return

        # ── No active session ────────────────────────────────────────────────
        if not self.session_manager.active_child_id:
            if "start session" in text_lower:
                await self.start_session(1)
                await event_bus.publish("tts.synthesize", "Starting default session.")
            else:
                logger.warning("[TherapyEngine] Speech received but no active session.")
            return

        child_id = self.session_manager.active_child_id

        # ── STEP 1: IN_GAME delegation — checked before ALL shortcuts ─────────
        if self.game_orchestrator and self.game_orchestrator.is_game_active():
            feedback = await self.game_orchestrator.handle_speech(text)
            if feedback:
                await event_bus.publish("tts.synthesize", feedback)
            return

        # ── STEP 2: Navigation voice shortcuts ────────────────────────────────
        if any(kw in text_lower for kw in ["show games", "game room", "open games", "let's play", "lets play", "play a game"]):
            await event_bus.publish("ui.screen.change", "games_dashboard")
            await event_bus.publish("tts.synthesize",
                "Here's the game room! Would you like to pick a game?")
            return

        if any(kw in text_lower for kw in
               ["go back", "stop playing", "don't want to play", "dont want to play",
                "exit games", "close games"]):
            await event_bus.publish("ui.screen.change", "face")
            await event_bus.publish("tts.synthesize", "Okay, let's do something else.")
            return

        # ── STEP 3: Main companion pipeline ───────────────────────────────────
        await self._run_companion_pipeline(text, child_id, text_lower)

    # ── Companion pipeline ─────────────────────────────────────────────────────

    async def _run_companion_pipeline(self, text: str, child_id: int, text_lower: str):
        """
        The unified companion response pipeline.

        Order:
          A. Classify intent
          B. Read emotional state (Python — no LLM)
          C. Check for sustained frustration → comfort mode
          D. Compute engagement hint (soft, optional)
          E. Build emotional + memory context
          F. Build prompt (emotion priority first)
          G. Call LLM
          H. Speak + persist
        """
        state_manager.set_state(CompanionState.RESPONDING)

        # A. Route turn through memory
        self.mem.add_user_turn(text, child_id)

        # B. Profile
        profile = self.ltm.get_child_profile(child_id) or {"name": "Friend"}

        # C. Classify intent
        interaction_type = self.classifier.classify(text, self.stm.current_activity)
        self.mem.set_activity(interaction_type)

        # D. Read emotion from EmotionalContinuityEngine (pure Python, not LLM)
        emotion_snap = emotion_engine.snapshot()
        current_emotion = emotion_snap["current"]
        self.mem.update_emotion(current_emotion)

        # E. Frustration override
        frustration = self.frustration_detector.check(text, child_id, {
            "emotion": current_emotion, "confidence": 0.7
        })

        if self.stm.is_sustained_frustration(threshold=3) or frustration >= 4:
            interaction_type = "comfort_mode"
            self.mem.set_activity("comfort_mode")
            logger.info("[TherapyEngine] Entering comfort mode (frustration).")
            await event_bus.publish("safety.alert", {
                "type": "high_frustration", "level": frustration, "child_id": child_id,
            })
            if current_emotion in ("angry", "frustrated", "anxious", "sad") and frustration >= 3:
                await event_bus.publish("tts.synthesize",
                    "I can see you're feeling upset. Let's take a slow breath together. Breathe in... and breathe out.")
                await event_bus.publish("ui.animation.trigger", {"type": "breathe"})
                await event_bus.publish("ui.expression.change", "calm")
                state_manager.set_state(CompanionState.IDLE)
                return

        # F. Soft engagement hint (only if not in comfort mode)
        soft_hint = ""
        if interaction_type != "comfort_mode":
            now = time.time()
            speech_bonus = 0.5 if (now - self._last_speech_time < 5.0) else 0.0
            composite_engagement = min(1.0, self._latest_attention + speech_bonus)

            logger.info(
                f"[Engagement] attention={self._latest_attention:.2f} "
                f"speech_bonus={speech_bonus:.2f} composite={composite_engagement:.2f}"
            )

            if composite_engagement < self._ENGAGE_LOW_THRESHOLD:
                if now - self._last_rule2_time > 60.0:
                    self._last_rule2_time = now
                    profile_data = self.ltm.get_child_profile(child_id) or {}
                    fav_topics  = profile_data.get("favorite_topics", [])
                    fav_animals = profile_data.get("favorite_animals", [])
                    if fav_topics:
                        soft_hint = f"The child seems a little distracted. Gently bring up their love of {fav_topics[0]} in a warm, friendly way."
                    elif fav_animals:
                        soft_hint = f"The child seems a little distracted. Mention something fun about {fav_animals[0]}s to reconnect."
                    else:
                        soft_hint = "The child seems a little distracted. Say their name warmly and ask a gentle question."
            else:
                # Engagement is high, child is not frustrated. Perfect time for a soft educational question!
                from robot.therapy.education_bank import get_educational_question
                profile_data = self.ltm.get_child_profile(child_id) or {}
                age = profile_data.get("age", 5)
                # Pick a topic based on current activity if possible
                topic_pref = None
                if "color" in text_lower: topic_pref = "colours"
                elif "animal" in text_lower or "dog" in text_lower or "cat" in text_lower: topic_pref = "animals"
                
                edu_question = get_educational_question(child_age=age, topic_preference=topic_pref)
                if edu_question:
                    soft_hint = f"The child is engaged and calm. Gently weave this educational question into your response: '{edu_question}'"
                    logger.info(f"[TherapyEngine] Educational question queued: {edu_question}")

        # G. Build context strings (emotion + memory separated)
        emotional_context = emotion_engine.build_context_line()
        memory_context    = self.mem.build_memory_context(child_id, situation=text)

        # H. Therapy rules
        dom_emotion, dom_conf = self.stm.get_dominant_emotion()
        active_rules = self.rules.get_active_rules(
            profile, interaction_type, frustration, self._latest_attention, dom_emotion
        )

        # I. Build system prompt (emotion injected FIRST — highest priority)
        system_prompt = get_prompt(
            interaction_type,
            child_name=profile.get("name", "Friend"),
            emotional_context=emotional_context,
            memory_context=memory_context,
        )
        if active_rules:
            system_prompt += f"\n\n{active_rules}"
        if soft_hint:
            system_prompt += f"\n\n[ENGAGEMENT HINT — optional]: {soft_hint}"

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.mem.get_recent_messages())

        logger.debug(f"[TherapyEngine] Intent={interaction_type} Emotion={current_emotion} Frustration={frustration}")

        # J. LLM call
        try:
            await event_bus.publish("ui.expression.change", "thinking")
            response_text = await self.provider_manager.chat(messages)
            logger.info(f"BMO: {response_text}")

            self.mem.add_assistant_turn(response_text)

            if interaction_type not in ("casual_conversation", "comfort_mode"):
                self.mem.record_successful_strategy(
                    child_id,
                    f"{interaction_type}: {text[:80]} → {response_text[:80]}",
                    score=0.7,
                )

            await event_bus.publish("tts.synthesize", response_text)

        except Exception as e:
            logger.error(f"[TherapyEngine] LLM pipeline error: {e}", exc_info=True)
            await event_bus.publish("ui.expression.change", "sad")
        finally:
            state_manager.set_state(CompanionState.IDLE)

    # ── Registration ───────────────────────────────────────────────────────────

    async def _handle_registration(self, text: str):
        try:
            extraction_prompt = (
                f"Extract the first name from this text. "
                f"Output ONLY the name, nothing else. If no name, output 'Friend'. Text: '{text}'"
            )
            name = await self.provider_manager.chat([{"role": "user", "content": extraction_prompt}])
            name = name.strip(' .,"\'')

            from robot.database.connection import db
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO children (caregiver_id, name, face_encoding) VALUES (1, ?, ?)",
                    (name, self.pending_registration_encoding)
                )
                new_child_id = cursor.lastrowid

            self.is_registering = False
            self.pending_registration_encoding = None

            await event_bus.publish("profile.created", new_child_id)
            await event_bus.publish("tts.synthesize",
                f"Nice to meet you, {name}! I'll remember you. What would you like to do?")
        except Exception as e:
            logger.error(f"Registration failed: {e}", exc_info=True)
            self.is_registering = False

    # ── Session helpers ────────────────────────────────────────────────────────

    async def start_session(self, child_id: int, session_type: str = "casual"):
        session_id = self.session_manager.start_session(child_id, session_type)
        self.mem.set_active_child(child_id)
        state_manager.set_state(CompanionState.IDLE)

        await event_bus.publish("session.started", {
            "child_id": child_id, "session_id": session_id,
        })
        logger.info(f"[TherapyEngine] Session {session_id} started for child {child_id}.")
        return session_id

    async def end_session(self):
        child_id   = self.session_manager.active_child_id
        session_id = self.session_manager.active_session_id

        messages  = self.mem.get_recent_messages()
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        summary   = "Session: " + " ".join(user_msgs[-5:])[:400]

        self.session_manager.end_session()

        await event_bus.publish("session.ended", {
            "child_id": child_id, "session_id": session_id, "summary": summary,
        })

        self.mem.clear_session()
        state_manager.set_state(CompanionState.IDLE)
        logger.info(f"[TherapyEngine] Session {session_id} ended for child {child_id}.")
