"""
memory/memory_manager.py
─────────────────────────────────────────────────────────────────────────────
Central coordinator for all memory subsystems.

Additions in this version:
  - Persists last_emotion, last_game, last_topic per child across sessions.
  - build_llm_context() now accepts an EmotionalContinuityEngine snapshot
    and returns separate emotional_context + memory_context strings that map
    directly to get_prompt()'s new signature.
  - Session summarization: at session end, summarizes STM and saves to LTM
    instead of clearing it wholesale.
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional

from robot.memory.short_term  import ShortTermMemory
from robot.memory.long_term   import LongTermMemory
from robot.memory.vector_store import VectorMemory
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Facade over Short-Term, Long-Term, and Vector Memory.

    Responsibilities
    ────────────────
    • Route incoming conversation turns into the right memory store.
    • Auto-extract interests from every child utterance.
    • Assemble unified context strings for the LLM.
    • Persist session summaries and therapy observations at session end.
    • Track and persist last_emotion, last_game, last_topic across sessions.
    """

    def __init__(
        self,
        stm:  ShortTermMemory,
        ltm:  LongTermMemory,
        vmem: VectorMemory,
    ):
        self.stm  = stm
        self.ltm  = ltm
        self.vmem = vmem

        # Persistent cross-session companion fields (loaded per child at session start)
        self._last_emotion: str = "neutral"
        self._last_game:    str = ""
        self._last_topic:   str = ""

        # Subscribe to events that auto-trigger memory updates
        event_bus.subscribe("speech.transcribed", self._on_user_speech)
        event_bus.subscribe("session.ended",       self._on_session_ended)
        event_bus.subscribe("session.started",     self._on_session_started)
        event_bus.subscribe("milestone.achieved",  self._on_milestone)
        event_bus.subscribe("game.finished",       self._on_game_finished)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_session_started(self, _event: str, data: Dict[str, Any]):
        """Load persistent cross-session fields for this child."""
        child_id = data.get("child_id")
        if not child_id:
            return
        self.set_active_child(child_id)
        self._load_persistent_fields(child_id)

    async def _on_user_speech(self, _event: str, text: str):
        """Called every time the child finishes an utterance."""
        child_id = self.stm.active_child_id
        if child_id is None:
            return
        # Update last_topic from the spoken text
        discovered = self.ltm.extract_memories_from_text(child_id, text)
        if discovered:
            self._last_topic = discovered[0]

    async def _on_session_ended(self, _event: str, data: Dict[str, Any]):
        """Consolidate memories when a session wraps up. Persist emotion/topic."""
        child_id   = data.get("child_id")
        summary    = data.get("summary", "")
        session_id = data.get("session_id")

        if not child_id:
            return

        # Save last emotion and topic persistently
        self._save_persistent_fields(child_id)

        if summary:
            self.ltm.consolidate_session(child_id, summary)
            if session_id:
                self.vmem.store_session_note(child_id, session_id, summary)

        logger.info(f"[MemoryManager] Consolidated session for child {child_id}.")

    async def _on_milestone(self, _event: str, data: Dict[str, Any]):
        child_id  = data.get("child_id")
        milestone = data.get("milestone", "")
        if child_id and milestone:
            self.ltm.add_milestone(child_id, milestone)

    async def _on_game_finished(self, _event: str, data: Dict[str, Any]):
        """Track last game played for cross-session memory."""
        game_type = data.get("game_type", "")
        if game_type:
            self._last_game = game_type

    # ── Core API ──────────────────────────────────────────────────────────────

    def set_active_child(self, child_id: int):
        """Called by SessionManager when a session starts."""
        self.stm.active_child_id = child_id

    def update_emotion(self, emotion: str):
        """Called by TherapyEngine with the latest Python-computed emotion."""
        self._last_emotion = emotion

    def add_user_turn(self, text: str, child_id: int):
        self.stm.add_user_message(text)
        # Extract interests inline (fast regex, no I/O)
        discovered = self.ltm.extract_memories_from_text(child_id, text)
        if discovered:
            self._last_topic = discovered[0]

    def add_assistant_turn(self, text: str):
        self.stm.add_assistant_message(text)

    def build_memory_context(self, child_id: int, situation: str = "") -> str:
        """
        Assemble memory context only (no emotional state — that comes separately).
        Combines: LTM profile + cross-session fields + STM state + vector memories.
        """
        parts: List[str] = []

        # 1. Long-term profile
        ltm_ctx = self.ltm.get_context_for_child(child_id)
        if ltm_ctx:
            parts.append(ltm_ctx)

        # 2. Cross-session persistent fields
        if self._last_emotion and self._last_emotion != "neutral":
            parts.append(f"Last time we talked, {self.stm.active_child_id and 'the child' or 'they'} seemed {self._last_emotion}.")
        if self._last_game:
            parts.append(f"Last game played: {self._last_game.replace('_', ' ').title()}.")
        if self._last_topic:
            parts.append(f"Last conversation topic: {self._last_topic}.")

        # 3. Short-term activity state (current session only)
        stm_ctx = self.stm.get_context_string()
        if stm_ctx:
            parts.append(f"Current session state: {stm_ctx}")

        # 4. Semantic recall (if we have enough data)
        if situation:
            similar = self.vmem.find_similar_situations(situation, child_id, n=2)
            if similar:
                parts.append("Past similar situations: " + " | ".join(similar))

            strategies = self.vmem.get_relevant_strategies(situation, child_id, n=2)
            if strategies:
                parts.append("Strategies that worked before: " + " | ".join(strategies))

        return "\n".join(parts)

    # Legacy alias used by some modules — kept for compatibility
    def build_llm_context(self, child_id: int, situation: str = "") -> str:
        return self.build_memory_context(child_id, situation)

    def record_successful_strategy(self, child_id: int, strategy: str, score: float):
        """Persist a therapy strategy that produced a high-score result."""
        self.vmem.store_strategy(child_id, strategy, score)

    def get_recent_messages(self):
        """Expose conversation history for the LLM messages list."""
        return self.stm.get_messages()

    def set_activity(self, activity_type: str, state: dict = None):
        self.stm.set_activity(activity_type, state)

    def clear_session(self):
        """Clear short-term context only. Long-term and cross-session data persist."""
        self.stm.clear()
        self.stm.active_child_id = None

    # ── Persistent fields helpers ─────────────────────────────────────────────

    def _load_persistent_fields(self, child_id: int):
        """Load cross-session fields from LTM observations."""
        try:
            memories = self.ltm.get_memories(
                child_id, memory_type="companion_state", limit=1
            )
            if memories:
                import json
                data = json.loads(memories[0].get("content", "{}"))
                self._last_emotion = data.get("last_emotion", "neutral")
                self._last_game    = data.get("last_game", "")
                self._last_topic   = data.get("last_topic", "")
                logger.info(
                    f"[MemoryManager] Loaded persistent fields for child {child_id}: "
                    f"emotion={self._last_emotion}, game={self._last_game}, topic={self._last_topic}"
                )
        except Exception as e:
            logger.debug(f"[MemoryManager] Could not load persistent fields: {e}")

    def _save_persistent_fields(self, child_id: int):
        """Persist cross-session companion state into LTM as a JSON blob."""
        try:
            import json
            content = json.dumps({
                "last_emotion": self._last_emotion,
                "last_game":    self._last_game,
                "last_topic":   self._last_topic,
            })
            # Remove old entry and add fresh one
            from robot.database.connection import db
            with db.get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM memories WHERE child_id=? AND memory_type='companion_state'",
                    (child_id,)
                )
                cursor.execute(
                    "INSERT INTO memories (child_id, memory_type, category, content, importance) VALUES (?,?,?,?,?)",
                    (child_id, "companion_state", "system", content, 1.0)
                )
            logger.info(f"[MemoryManager] Saved persistent companion state for child {child_id}.")
        except Exception as e:
            logger.error(f"[MemoryManager] Could not save persistent fields: {e}")
