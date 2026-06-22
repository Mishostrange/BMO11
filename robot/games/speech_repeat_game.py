"""
robot/games/speech_repeat_game.py
"""

import random
import time
import difflib
import logging
from typing import Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

LEVELS = {
    1: ["cat", "dog", "ball", "sun", "car", "hat"],
    2: ["red apple", "big dog", "fast car", "blue sky"],
    3: ["I like to play", "The sun is hot", "I want some water"],
    4: ["She sells seashells by the seashore"],
}


@GameRegistry.register("speech")
class SpeechRepeatGame(BaseGame):
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
        return "Let's play the parrot game! I will say something, and you say it back to me."

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        level_key = min(self.difficulty, max(LEVELS.keys()))
        self.current_target = random.choice(LEVELS[level_key])
        self.trial_start = time.time()

        logger.info(f"[GameState] SpeechRepeatGame round {self._round_num}/{self.MAX_ROUNDS} target='{self.current_target}'")
        prompt = f"Can you say: {self.current_target}?"
        self._mark_question_shown()
        return prompt

    def _similarity(self, s1: str, s2: str) -> float:
        return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            logger.warning("[SpeechRepeatGame] evaluate() called with no active target.")
            return GameResult(correct=False, score=0.0, response_time=0.0,
                              feedback="Let me give you something to say!")

        response_time = time.time() - self.trial_start
        similarity = self._similarity(self.current_target, response)
        threshold = 0.6 if self.difficulty <= 1 else 0.75
        correct = similarity >= threshold

        if correct:
            feedback = "Excellent speaking! You said it perfectly!"
            await self.trigger_success_video_once()
        else:
            feedback = f"You tried really hard! Listen again: {self.current_target}"
            await self.trigger_failure_video_once()

        result = GameResult(
            correct=correct, score=similarity, response_time=response_time,
            feedback=feedback,
            data={"target": self.current_target, "recognized": response, "similarity": similarity},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        self.current_target = None
        await event_bus.publish("game.scored", {"game_type": "speech", "score": similarity, "child_id": self.child_id})
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = 2 if self.difficulty > 2 else 1
            return {"tokens_earned": tokens, "animation": "star_burst"}
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
