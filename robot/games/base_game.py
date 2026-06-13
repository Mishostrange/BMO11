from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class GameResult:
    correct: bool
    score: float
    response_time: float
    feedback: str
    data: Dict[str, Any] = None

@dataclass
class GameSummary:
    total_score: float
    correct_count: int
    total_count: int
    time_spent: float
    difficulty_achieved: int

class BaseGame(ABC):
    """Abstract base class for all therapy games."""
    
    def __init__(self):
        self.child_id = None
        self.difficulty = 1
        self.start_time = None
        self.trials = []
    
    @abstractmethod
    async def start(self, child_id: int, difficulty: int) -> str:
        """Initialize the game and return the starting prompt."""
        self.child_id = child_id
        self.difficulty = difficulty
        pass

    @abstractmethod
    async def evaluate(self, response: str) -> GameResult:
        """Evaluate the child's response and return a GameResult."""
        pass

    @abstractmethod
    async def reward(self, result: GameResult) -> Dict[str, Any]:
        """Trigger rewards based on the result."""
        pass

    @abstractmethod
    async def finish(self) -> GameSummary:
        """Wrap up the game and return a summary of performance."""
        pass

    def get_hint(self) -> str:
        """Provide a hint for the current trial."""
        return "Let's try again!"

    def get_encouragement(self) -> str:
        """Provide general encouragement."""
        return "You are doing great!"
