"""
robot/games/focus_game.py
Sequential memory / focus game. Child repeats a sequence of words in order.
"""

import random
import time
import logging
from typing import Optional, List

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

ITEMS = ["apple", "ball", "cat", "dog", "elephant", "fish", "grape", "hat", "ice", "jar"]


@GameRegistry.register("focus")
class FocusGame(BaseGame):
    MAX_ROUNDS = 4

    def __init__(self):
        super().__init__()
        self.current_sequence: List[str] = []
        self.trial_start: float = 0.0

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self.current_sequence = []
        self._transition(GameState.SHOW_QUESTION)
        return "Let's play the memory game! I will say some words, and you repeat them back in order."

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        seq_len = min(self.difficulty + 1, 5)
        self.current_sequence = random.sample(ITEMS, seq_len)
        items_str = ", ".join(self.current_sequence)
        self.trial_start = time.time()

        logger.info(f"[GameState] FocusGame round {self._round_num}/{self.MAX_ROUNDS} sequence={self.current_sequence}")
        prompt = f"Listen carefully: {items_str}. Now you say them!"
        self._mark_question_shown()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_sequence:
            logger.warning("[FocusGame] evaluate() called with no active sequence.")
            return GameResult(correct=False, score=0.0, response_time=0.0,
                              feedback="Let me give you a sequence to remember!")

        response_time = time.time() - self.trial_start
        response_lower = response.lower()

        correct_items = sum(1 for item in self.current_sequence if item in response_lower)
        score = correct_items / len(self.current_sequence)
        correct = score >= 1.0

        if correct:
            feedback = "Amazing memory! You got them all!"
            await self.trigger_success_video_once()
        elif score >= 0.5:
            feedback = f"Good job! You remembered most of them. The words were: {', '.join(self.current_sequence)}."
            await self.trigger_failure_video_once()
        else:
            feedback = f"Nice try! The words were: {', '.join(self.current_sequence)}."
            await self.trigger_failure_video_once()

        result = GameResult(
            correct=correct, score=score, response_time=response_time,
            feedback=feedback,
            data={"sequence": self.current_sequence},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        self.current_sequence = []
        await event_bus.publish("game.scored", {"game_type": "focus", "score": score, "child_id": self.child_id})
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            return {"tokens_earned": self.difficulty, "animation": "star_burst"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - (self.start_time or time.time())
        total_score = sum(t.score for t in self.trials)
        self._transition(GameState.DONE)
        return GameSummary(
            total_score=total_score, correct_count=correct_count,
            total_count=total_count, time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )
