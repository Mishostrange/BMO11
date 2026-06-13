"""
memory/memory_manager.py
─────────────────────────────────────────────────────────────────────────────
Central coordinator for all memory subsystems.
Provides a single entry point so the TherapyEngine only needs one import.
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
    • Assemble a unified context block for the LLM.
    • Persist session summaries and therapy observations at session end.
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

        # Subscribe to events that auto-trigger memory updates
        event_bus.subscribe("speech.transcribed",  self._on_user_speech)
        event_bus.subscribe("session.ended",        self._on_session_ended)
        event_bus.subscribe("milestone.achieved",   self._on_milestone)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_user_speech(self, _event: str, text: str):
        """Called every time the child finishes an utterance."""
        child_id = self.stm.active_child_id
        if child_id is None:
            return
        # Auto-extract interests in the background (non-blocking)
        asyncio.get_event_loop().run_in_executor(
            None, self.ltm.extract_memories_from_text, child_id, text
        )

    async def _on_session_ended(self, _event: str, data: Dict[str, Any]):
        """Consolidate memories when a session wraps up."""
        child_id   = data.get("child_id")
        summary    = data.get("summary", "")
        session_id = data.get("session_id")

        if not child_id or not summary:
            return

        # Save to relational DB
        self.ltm.consolidate_session(child_id, summary)

        # Save to vector DB for semantic search
        if session_id:
            self.vmem.store_session_note(child_id, session_id, summary)

        logger.info(f"[MemoryManager] Consolidated session for child {child_id}")

    async def _on_milestone(self, _event: str, data: Dict[str, Any]):
        child_id  = data.get("child_id")
        milestone = data.get("milestone", "")
        if child_id and milestone:
            self.ltm.add_milestone(child_id, milestone)

    # ── Core API ──────────────────────────────────────────────────────────────

    def set_active_child(self, child_id: int):
        """Called by SessionManager when a session starts."""
        self.stm.active_child_id = child_id

    def add_user_turn(self, text: str, child_id: int):
        self.stm.add_user_message(text)
        # Non-blocking: extract interests inline (fast regex, no I/O)
        self.ltm.extract_memories_from_text(child_id, text)

    def add_assistant_turn(self, text: str):
        self.stm.add_assistant_message(text)

    def build_llm_context(self, child_id: int, situation: str = "") -> str:
        """
        Assemble the full context block injected into the LLM system prompt.
        Combines:  LTM profile + STM activity state + relevant vector memories.
        """
        parts: List[str] = []

        # 1. Long-term profile + important memories
        ltm_ctx = self.ltm.get_context_for_child(child_id)
        if ltm_ctx:
            parts.append(ltm_ctx)

        # 2. Short-term activity state
        stm_ctx = self.stm.get_context_string()
        if stm_ctx:
            parts.append(f"Current state: {stm_ctx}")

        # 3. Semantic recall: similar past sessions (if enough data)
        if situation:
            similar = self.vmem.find_similar_situations(situation, child_id, n=2)
            if similar:
                parts.append("Past similar situations: " + " | ".join(similar))

            strategies = self.vmem.get_relevant_strategies(situation, child_id, n=2)
            if strategies:
                parts.append("Strategies that worked before: " + " | ".join(strategies))

        return "\n".join(parts)

    def record_successful_strategy(self, child_id: int, strategy: str, score: float):
        """Persist a therapy strategy that produced a high-score result."""
        self.vmem.store_strategy(child_id, strategy, score)

    def get_recent_messages(self):
        """Expose conversation history for the LLM messages list."""
        return self.stm.get_messages()

    def set_activity(self, activity_type: str, state: dict = None):
        self.stm.set_activity(activity_type, state)

    def clear_session(self):
        self.stm.clear()
        self.stm.active_child_id = None
