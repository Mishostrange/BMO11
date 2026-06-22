"""
robot/games/turn_taking_game.py
Collaborative story-building game to practice conversational turn-taking.
"""

import random
import time
import logging
from typing import Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

STARTERS = [
    "Once upon a time, there was a little dog who loved to...",
    "If I went to the moon, I would bring...",
    "My favorite thing to eat for breakfast is...",
    "When I go to the park, I like to play on the...",
    "One day a friendly dragon flew to school and...",
]


@GameRegistry.register("turn_taking")
class TurnTakingGame(BaseGame):
    MAX_ROUNDS = 3

    def __init__(self):
        super().__init__()
        self._starters_pool = []
        self.trial_start: float = 0.0

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self._starters_pool = random.sample(STARTERS, min(self.MAX_ROUNDS, len(STARTERS)))
        self._transition(GameState.SHOW_QUESTION)
        return "Let's build a story together! I'll start a sentence, and then it's your turn to finish it."

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS or not self._starters_pool:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        starter = self._starters_pool.pop(0)
        self._current_starter = starter
        self.trial_start = time.time()

        logger.info(f"[GameState] TurnTakingGame round {self._round_num}/{self.MAX_ROUNDS}")
        prompt = f"My turn: {starter} ... Now your turn!"
        self._mark_question_shown()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        response_time = time.time() - self.trial_start
        word_count = len(response.split())
        correct = word_count >= 1

        if correct:
            feedback = "Wonderful! You are a great storyteller!"
            await self.trigger_success_video_once()
        else:
            feedback = "I didn't quite hear you. That's okay, let's try the next one!"
            await self.trigger_failure_video_once()

        score = 1.0 if correct else 0.0
        result = GameResult(
            correct=correct, score=score, response_time=response_time,
            feedback=feedback,
            data={"word_count": word_count},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        await event_bus.publish("game.scored", {"game_type": "turn_taking", "score": score, "child_id": self.child_id})
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            return {"tokens_earned": 1, "animation": "confetti"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - (self.start_time or time.time())
        total_score = sum(t.score for t in self.trials)
        await event_bus.publish("ui.expression.change", "happy")
        self._transition(GameState.DONE)
        return GameSummary(
            total_score=total_score, correct_count=correct_count,
            total_count=total_count, time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )
