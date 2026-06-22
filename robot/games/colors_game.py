"""
robot/games/colors_game.py
"""

import random
import time
import logging
from typing import Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

COLORS = ["red", "blue", "green", "yellow", "orange", "purple"]
OBJECTS = {
    "apple": "red", "sky": "blue", "grass": "green",
    "sun": "yellow", "orange": "orange", "grape": "purple",
}


@GameRegistry.register("colors")
class ColorsGame(BaseGame):
    MAX_ROUNDS = 4

    def __init__(self):
        super().__init__()
        self.current_target: Optional[str] = None
        self.trial_start: float = 0.0

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self.current_target = None
        self._transition(GameState.SHOW_QUESTION)
        return "Let's play the colors game! Are you ready?"

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        logger.info(f"[GameState] ColorsGame round {self._round_num}/{self.MAX_ROUNDS}")

        if self.difficulty <= 1:
            choices = random.sample(COLORS[:3], 2)
            self.current_target = random.choice(choices)
            prompt = f"Can you say the color: {self.current_target}?"
        elif self.difficulty == 2:
            choices = random.sample(COLORS[:4], 4)
            self.current_target = random.choice(choices)
            options_text = ", ".join(choices[:-1]) + f", or {choices[-1]}"
            prompt = f"Which of these colors is {self.current_target}? Say: {options_text}"
        else:
            obj, color = random.choice(list(OBJECTS.items()))
            self.current_target = color
            prompt = f"What color is a {obj}?"

        self.trial_start = time.time()
        await event_bus.publish("game.state_update", {
            "game_type": "colors",
            "round": self._round_num,
            "max_rounds": self.MAX_ROUNDS,
            "state": {"target": self.current_target},
            "prompt": prompt,
        })
        self._mark_question_shown()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            logger.warning("[ColorsGame] evaluate() called with no active target.")
            return GameResult(correct=False, score=0.0, response_time=0.0,
                              feedback="Let me ask you a question!")

        response_time = time.time() - self.trial_start
        correct = self.current_target.lower() in response.lower()
        score = 1.0 if correct else 0.0
        feedback = "Great job! That is right!" if correct else f"Good try! The answer was {self.current_target}."

        if correct:
            await self.trigger_success_video_once()
        else:
            await self.trigger_failure_video_once()

        result = GameResult(
            correct=correct, score=score, response_time=response_time,
            feedback=feedback,
            data={"target": self.current_target},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        self.current_target = None
        await event_bus.publish("game.scored", {"game_type": "colors", "score": score, "child_id": self.child_id})
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            return {"tokens_earned": 1, "animation": "star_burst"}
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
