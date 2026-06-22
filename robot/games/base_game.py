"""
robot/games/base_game.py
──────────────────────────────────────────────────────────────────────────────
Base class for all therapy games. Defines the state machine contract.

State Machine:
  IDLE → SHOW_QUESTION → WAIT_ANSWER → SHOW_FEEDBACK → (loop or END_ROUND)

Games signal completion by setting `signals_complete=True` on the returned
GameResult. The orchestrator reads this flag to end the session cleanly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


class GameState(Enum):
    IDLE          = auto()  # Not yet started
    SHOW_QUESTION = auto()  # Question was just presented
    WAIT_ANSWER   = auto()  # Waiting for child's speech
    SHOW_FEEDBACK = auto()  # Showing correct/wrong feedback
    END_ROUND     = auto()  # Round finished, preparing next or ending game
    DONE          = auto()  # Game fully complete, ready to exit


@dataclass
class GameResult:
    correct: bool
    score: float
    response_time: float
    feedback: str
    data: Dict[str, Any] = None
    signals_complete: bool = False   # Set True on the LAST round to end the game


@dataclass
class GameSummary:
    total_score: float
    correct_count: int
    total_count: int
    time_spent: float
    difficulty_achieved: int


class BaseGame(ABC):
    """Abstract base class for all therapy games.

    Subclasses must implement:
      - start()        → returns opening speech prompt
      - evaluate()     → processes child answer, returns GameResult
      - next_question() → returns next question speech prompt (or None if done)
      - reward()       → fires reward animations/tokens
      - finish()       → returns GameSummary
    """

    MAX_ROUNDS = 5  # Default max rounds before auto-completing

    def __init__(self):
        self.child_id: Optional[int] = None
        self.difficulty: int = 1
        self.start_time: Optional[float] = None
        self.trials = []
        self._state = GameState.IDLE
        self._round_num: int = 0
        self._video_played: bool = False  # Guard against duplicate video triggers

    # ── State Machine ─────────────────────────────────────────────────────────

    @property
    def state(self) -> GameState:
        return self._state

    def _transition(self, new_state: GameState):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"[GameState] {self.__class__.__name__} "
            f"{self._state.name} → {new_state.name} "
            f"(round {self._round_num})"
        )
        self._state = new_state

    def _mark_question_shown(self):
        """Call this after publishing the question prompt."""
        self._video_played = False
        self._transition(GameState.WAIT_ANSWER)

    def _mark_feedback_shown(self):
        self._transition(GameState.SHOW_FEEDBACK)

    # ── Video helpers ──────────────────────────────────────────────────────────

    async def trigger_success_video_once(self):
        """Fire success feedback exactly once per round."""
        from robot.services.event_bus import event_bus
        if not self._video_played:
            self._video_played = True
            await event_bus.publish("ui.animation.trigger", {"type": "video_dance"})

    async def trigger_failure_video_once(self):
        """Fire failure feedback exactly once per round."""
        from robot.services.event_bus import event_bus
        if not self._video_played:
            self._video_played = True
            await event_bus.publish("ui.animation.trigger", {"type": "shake"})
            await event_bus.publish("ui.expression.change", "neutral")

    # ── Abstract Interface ────────────────────────────────────────────────────

    @abstractmethod
    async def start(self, child_id: int, difficulty: int) -> str:
        """Initialize and return the opening speech prompt."""
        self.child_id = child_id
        self.difficulty = difficulty

    @abstractmethod
    async def evaluate(self, response: str) -> GameResult:
        """Evaluate child's answer. Returns GameResult with signals_complete=True on last round."""
        pass

    async def next_question(self) -> Optional[str]:
        """Return the next question speech prompt, or None if the game is done.
        Default: returns None (subclasses with multi-round flow should override).
        """
        return None

    @abstractmethod
    async def reward(self, result: GameResult) -> Dict[str, Any]:
        """Trigger reward animations/tokens. Do NOT publish TTS here."""
        pass

    @abstractmethod
    async def finish(self) -> GameSummary:
        """Wrap up and return session summary."""
        pass

    # ── Convenience ───────────────────────────────────────────────────────────

    def reset(self):
        """Reset internal state for a fresh start."""
        self.trials = []
        self._state = GameState.IDLE
        self._round_num = 0
        self._video_played = False
        self.start_time = None

    def get_hint(self) -> str:
        return "Let's try again!"

    def get_encouragement(self) -> str:
        return "You are doing great!"
