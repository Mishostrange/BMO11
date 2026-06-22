"""
robot/therapy/state_manager.py
──────────────────────────────────────────────────────────────────────────────
Single Source of Truth for Companion State.
All modules (games, therapy engine, UI) must read from this manager to 
understand what BMO is currently doing.
"""

from enum import Enum
import logging
from typing import List, Callable

logger = logging.getLogger(__name__)

class CompanionState(Enum):
    IDLE = "idle"             # Waiting for interaction
    LISTENING = "listening"   # VAD active, child is speaking
    RESPONDING = "responding" # LLM is thinking or TTS is speaking
    IN_GAME = "in_game"       # A structured game session is active


class CompanionStateManager:
    """Global manager for BMO's interaction state."""
    
    def __init__(self):
        self._current_state: CompanionState = CompanionState.IDLE
        self._listeners: List[Callable[[CompanionState, CompanionState], None]] = []

    def subscribe(self, callback: Callable[[CompanionState, CompanionState], None]):
        """Subscribe to state changes. Callback receives (old_state, new_state)."""
        self._listeners.append(callback)

    @property
    def current_state(self) -> CompanionState:
        """Read-only access to the current state."""
        return self._current_state

    def set_state(self, new_state: CompanionState, force: bool = False) -> bool:
        """
        Transition to a new state.
        Returns True if successful, False if blocked (unless force=True).
        """
        old_state = self._current_state
        
        if old_state == new_state:
            return True
            
        # Optional: Add state transition guards here
        # e.g., cannot go to LISTENING if IN_GAME without proper handling
        # For now, we trust the pipeline to manage transitions, but log them clearly.
        
        logger.info(f"[StateManager] Transition: {old_state.name} → {new_state.name}")
        self._current_state = new_state
        
        for listener in self._listeners:
            try:
                listener(old_state, new_state)
            except Exception as e:
                logger.error(f"[StateManager] Listener error: {e}")
                
        return True

    def is_idle(self) -> bool:
        return self._current_state == CompanionState.IDLE
        
    def is_listening(self) -> bool:
        return self._current_state == CompanionState.LISTENING
        
    def is_responding(self) -> bool:
        return self._current_state == CompanionState.RESPONDING
        
    def is_in_game(self) -> bool:
        return self._current_state == CompanionState.IN_GAME

# Global singleton
state_manager = CompanionStateManager()
