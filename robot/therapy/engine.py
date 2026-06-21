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

        self.classifier           = InteractionClassifier()
        self.frustration_detector = FrustrationDetector()
        self.rules                = TherapyRules()
        
        # Game orchestrator (set externally via set_game_orchestrator)
        self.game_orchestrator = None

        # Subscribe to STT events
        event_bus.subscribe("speech.transcribed", self._on_speech_transcribed)

        # Sustained negative emotion from EmotionTracker
        event_bus.subscribe("emotion.sustained",  self._on_emotion_sustained)

        # Face recognition events
        event_bus.subscribe("face.recognized", self._on_face_recognized)
        event_bus.subscribe("face.unknown", self._on_face_unknown)
        
        # Attention state
        self._latest_attention = 0.5
        event_bus.subscribe("engagement.update", self._on_engagement)

        # Live emotion snapshot
        self._latest_emotion: Dict[str, Any] = {}
        
        # Pending registration state
        self.pending_registration_encoding = None
        self.is_registering = False
        
        # System sleep state
        self.is_asleep = False
        
        # Decision engine: low-score thresholds
        self._ENGAGE_LOW_THRESHOLD = 0.3   # engagement below this → switch to fav topic
        self._EMOTION_ACC_THRESHOLD = 0.5  # emotion accuracy below this → suggest emotions game
        self._SOCIAL_ACC_THRESHOLD  = 0.5  # social skill score below this → suggest social skills
        
        # Subscribe to profile.created to auto-start session after registration
        event_bus.subscribe("profile.created", self._on_profile_created)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_engagement(self, _event: str, data: Dict[str, Any]):
        self._latest_attention = data.get("score", 0.5)

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

    async def _on_profile_created(self, _event: str, new_child_id: int):
        """Auto-start a session once a new child profile has been saved."""
        if self.session_manager.active_child_id != new_child_id:
            logger.info(f"[TherapyEngine] Profile created. Starting session for child {new_child_id}")
            await self.start_session(new_child_id)

    async def _on_face_recognized(self, _event: str, child_id: int):
        """Automatically start a session if a known child is seen."""
        if self.is_asleep:
            return  # Ignore faces while asleep

        if self.session_manager.active_child_id is not None:
            if self.session_manager.active_child_id != child_id:
                logger.debug(f"[TherapyEngine] Session locked to child {self.session_manager.active_child_id}. Ignoring child {child_id}.")
            return  # Session is locked! Do not switch!

        if self.session_manager.active_child_id != child_id:
            logger.info(f"[TherapyEngine] Face recognized. Auto-starting session for child {child_id}")
            await self.start_session(child_id)
            
            # Greet the child — personalize with their interests
            profile = self.ltm.get_child_profile(child_id) or {}
            name = profile.get("name", "friend")
            topics = profile.get("favorite_topics", [])
            animals = profile.get("favorite_animals", [])
            
            if topics:
                greeting = f"Hello {name}! I remember you love {topics[0]}. Want to talk about it?"
            elif animals:
                greeting = f"Hello {name}! Do you want to hear a fun fact about {animals[0]}s today?"
            else:
                greeting = f"Hello {name}! I'm so happy to see you again. What do you want to do today?"
                
            await event_bus.publish("tts.synthesize", greeting)

    async def _on_face_unknown(self, _event: str, encoding_bytes: bytes):
        """Triggered when an unknown face is seen for several seconds."""
        if not self.is_registering:
            if self.session_manager.active_session_id:
                logger.debug("[TherapyEngine] Unknown face detected, but session is active. Ignoring to prevent interruption.")
                return
                
            logger.info("[TherapyEngine] Unknown face detected. Starting registration.")
            self.is_registering = True
            self.pending_registration_encoding = encoding_bytes
                
            await event_bus.publish("tts.synthesize", "Hello! I don't think we've met before. I am BMO. What is your name?")

    async def _on_speech_transcribed(self, _event: str, text: str):
        """Main pipeline entry point from STT."""
        
        # ── 0. Registration Intercept ──────────────────────────────────────────
        if self.is_registering and self.pending_registration_encoding:
            logger.info(f"[TherapyEngine] Received name during registration: {text}")
            
            # Simple heuristic: assume the transcribed text is their name or contains it
            # A more robust system would use the LLM to extract the name:
            # For now, we'll just use Groq to extract the name quickly.
            try:
                extraction_prompt = f"Extract the first name from this text. Output ONLY the name, nothing else. If no name, output 'Friend'. Text: '{text}'"
                name = await self.provider_manager.chat([{"role": "user", "content": extraction_prompt}])
                name = name.strip(' .,"\'')
                
                # Save to database
                from robot.database.connection import db
                with db.get_cursor() as cursor:
                    # Default caregiver_id to 1 (admin)
                    cursor.execute(
                        "INSERT INTO children (caregiver_id, name, face_encoding) VALUES (1, ?, ?)",
                        (name, self.pending_registration_encoding)
                    )
                    new_child_id = cursor.lastrowid
                    
                self.is_registering = False
                self.pending_registration_encoding = None
                
                await event_bus.publish("profile.created", new_child_id)
                await event_bus.publish("tts.synthesize", f"Nice to meet you, {name}! I have saved your profile. Let's play!")
                return
            except Exception as e:
                logger.error(f"Registration failed: {e}")
                self.is_registering = False
                return

        text_lower = text.lower()

        # 1. Goodbye check
        if any(w in text_lower for w in ["goodbye", "good bye", "bye bmo"]):
            logger.info("[TherapyEngine] Goodbye command heard. Locking system.")
            await event_bus.publish("tts.synthesize", "Goodbye! Say 'Hi BMO' when you want to play again.")
            if self.session_manager.active_session_id:
                await self.end_session()
            self.is_asleep = True
            return

        # 2. Wake word check (works even if asleep)
        if not self.session_manager.active_child_id and self.is_asleep:
            if any(w in text_lower for w in ["hi bmo", "hello bmo", "wake up"]):
                logger.info("[TherapyEngine] Wake word detected. Waking up.")
                self.is_asleep = False
                await event_bus.publish("tts.synthesize", "Hello! I am awake. Let me take a look at you.")
                return
            else:
                return  # Ignore other speech while asleep

        if not self.session_manager.active_child_id:
            # Fallback for manual start without camera
            if "start session" in text_lower:
                logger.info("[TherapyEngine] Manual start requested without active session.")
                await self.start_session(1)
                await event_bus.publish("tts.synthesize", "Starting default session.")
                return
            logger.warning("[TherapyEngine] Speech received but no active session.")
            return

        child_id = self.session_manager.active_child_id
        
        # ── 0b. Voice shortcuts for Games Dashboard ──────────────────────────
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["show games", "game room", "let's play", "lets play", "open games"]):
            await event_bus.publish("ui.screen.change", "games_dashboard")
            await event_bus.publish("tts.synthesize", "Here is the game room! Which game do you want to play?")
            return
        
        # ── 0c. Delegate to Game Orchestrator when a game is active ──────────
        if self.game_orchestrator and self.game_orchestrator.is_game_active():
            feedback = await self.game_orchestrator.handle_speech(text)
            if feedback:
                await event_bus.publish("tts.synthesize", feedback)
            return

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
            # Decision Engine Rule 1:
            # IF emotion == anxious/frustrated → auto-start sensory regulation
            if dom_emotion in ("angry", "frustrated", "anxious", "sad") and frustration >= 3:
                logger.info("[TherapyEngine] Decision Engine: auto-triggering sensory regulation.")
                await event_bus.publish("tts.synthesize", "I can see you're feeling upset. Let's take a deep breath together. Breathe in... and breathe out...")
                await event_bus.publish("ui.animation.trigger", {"type": "breathe"})
                await event_bus.publish("ui.expression.change", "calm")
                return

        # ── Decision Engine Rule 2: Low engagement → switch to favorite topic ──
        engagement_hint = ""
        if self._latest_attention < self._ENGAGE_LOW_THRESHOLD:
            profile_data = self.ltm.get_child_profile(child_id) or {}
            fav_topics = profile_data.get("favorite_topics", [])
            fav_animals = profile_data.get("favorite_animals", [])
            if fav_topics:
                engagement_hint = f"IMPORTANT: The child seems distracted. Bring up their love of '{fav_topics[0]}' to regain their attention."
            elif fav_animals:
                engagement_hint = f"IMPORTANT: The child seems distracted. Say a fun fact about {fav_animals[0]}s to re-engage them."
            else:
                engagement_hint = "IMPORTANT: The child seems distracted. Say their name and ask them a very silly, funny question to get their attention back."
            logger.info(f"[TherapyEngine] Decision Engine Rule 2 triggered (low attention). Hint: {engagement_hint[:60]}")

        # ── Decision Engine Rule 3: Low emotion score → suggest emotions game ──
        suggest_game_hint = ""
        try:
            from robot.database.connection import db
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT AVG(correct_count * 1.0 / MAX(total_count, 1)) AS acc FROM game_results WHERE child_id=? AND game_type='emotions' LIMIT 10",
                    (child_id,)
                )
                row = cursor.fetchone()
                if row and row["acc"] is not None and row["acc"] < self._EMOTION_ACC_THRESHOLD:
                    suggest_game_hint += " You should gently suggest playing the Feelings Game to help the child practice identifying emotions."
                    logger.info("[TherapyEngine] Decision Engine Rule 3: Low emotion accuracy → suggesting emotions game.")
                
                cursor.execute(
                    "SELECT AVG(correct_count * 1.0 / MAX(total_count, 1)) AS acc FROM game_results WHERE child_id=? AND game_type='social_skills' LIMIT 10",
                    (child_id,)
                )
                row = cursor.fetchone()
                if row and row["acc"] is not None and row["acc"] < self._SOCIAL_ACC_THRESHOLD:
                    suggest_game_hint += " You should suggest playing the Friends Game to help the child practice social skills."
                    logger.info("[TherapyEngine] Decision Engine Rule 4: Low social skills score → suggesting social skills game.")
        except Exception as e:
            logger.debug(f"[TherapyEngine] Could not check game scores for decision engine: {e}")

        # ── 5. Apply therapy rules ────────────────────────────────────────────
        active_rules = self.rules.get_active_rules(profile, interaction_type, frustration, self._latest_attention, dom_emotion)

        # ── 6. Build context for LLM ──────────────────────────────────────────
        full_context = self.mem.build_llm_context(child_id, situation=text)

        system_prompt = get_prompt(
            interaction_type,
            child_name=profile.get("name", "Friend"),
            context=full_context,
        )
        if active_rules:
            system_prompt += f"\n\n{active_rules}"
        # Inject decision-engine hints into the system prompt
        if engagement_hint:
            system_prompt += f"\n\n{engagement_hint}"
        if suggest_game_hint:
            system_prompt += f"\n\n{suggest_game_hint}"

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
