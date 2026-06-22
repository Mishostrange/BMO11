"""
robot/games/emotions_game.py
──────────────────────────────────────────────────────────────────────────────
Emotion Recognition Game.

State flow (managed by BaseGame + Orchestrator):
  start() → opening prompt
  next_question() → generate + publish trial, return spoken question
  evaluate() → score answer, set signals_complete on last round
  finish() → summary

Difficulty levels:
  1 (Easy):   2-option face display
  2 (Medium): 4-option face display
  3 (Hard):   Situational / scenario description
"""

import random
import time
import logging
from typing import Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

EMOTIONS = ["happy", "sad", "angry", "scared"]

SITUATIONS = {
    "You got a brand new toy for your birthday!": "happy",
    "You dropped your favorite ice cream on the floor.": "sad",
    "Someone pushed you and took your turn on the swing.": "angry",
    "A loud thunder noise woke you up in the dark.": "scared",
    "Your best friend came over to play!": "happy",
    "You lost your favorite teddy bear.": "sad",
}


@GameRegistry.register("emotions")
class EmotionsGame(BaseGame):
    MAX_ROUNDS = 4

    def __init__(self):
        super().__init__()
        self.current_target: Optional[str] = None
        self._current_options = []
        self.trial_start: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = min(3, max(1, difficulty))
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self.current_target = None
        self._transition(GameState.SHOW_QUESTION)
        return "Let's play the feelings game! I will show you a face, and you tell me how I feel."

    async def next_question(self) -> Optional[str]:
        """Generate and publish the next trial. Returns the spoken question."""
        if self._round_num >= self.MAX_ROUNDS:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        logger.info(f"[GameState] EmotionsGame round {self._round_num}/{self.MAX_ROUNDS}")

        if self.difficulty == 1:
            pool = random.sample(EMOTIONS, 2)
            self.current_target = random.choice(pool)
            self._current_options = pool
            await event_bus.publish("ui.expression.change", self.current_target)
            options_text = " or ".join(pool)
            prompt = f"Look at my face. Do you think I feel {options_text}?"

        elif self.difficulty == 2:
            pool = list(EMOTIONS)
            random.shuffle(pool)
            self.current_target = random.choice(pool)
            self._current_options = pool
            await event_bus.publish("ui.expression.change", self.current_target)
            options_text = ", ".join(pool[:-1]) + f", or {pool[-1]}"
            prompt = f"Look at my face. Which feeling is this? Is it {options_text}?"

        else:
            situation, emotion = random.choice(list(SITUATIONS.items()))
            self.current_target = emotion
            self._current_options = list(EMOTIONS)
            await event_bus.publish("ui.expression.change", "thinking")
            prompt = f"Listen to this story: {situation} How do you think that makes me feel?"

        self.trial_start = time.time()

        await event_bus.publish("game.state_update", {
            "game_type": "emotions",
            "round": self._round_num,
            "max_rounds": self.MAX_ROUNDS,
            "state": {
                "target": self.current_target,
                "options": self._current_options,
            },
            "prompt": prompt,
            "score_text": f"Score: {sum(1 for t in self.trials if t.correct)}/{len(self.trials)}",
        })

        self._mark_question_shown()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            # No active question — skip gracefully
            logger.warning("[EmotionsGame] evaluate() called with no active target.")
            return GameResult(
                correct=False, score=0.0, response_time=0.0,
                feedback="Hmm, let me think of a new question for you!"
            )

        response_time = time.time() - self.trial_start
        correct = self.current_target.lower() in response.lower()
        score = 1.0 if correct else 0.0

        if correct:
            feedback = "Yes! That is exactly right! You are great at reading feelings."
            await self.trigger_success_video_once()
        else:
            feedback = f"Good try! Actually, I was feeling {self.current_target}."
            await self.trigger_failure_video_once()

        result = GameResult(
            correct=correct,
            score=score,
            response_time=response_time,
            feedback=feedback,
            data={"target": self.current_target, "response": response},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        self.current_target = None  # Clear so we don't score twice

        await event_bus.publish("game.scored", {
            "game_type": "emotions",
            "score": score,
            "child_id": self.child_id,
        })

        self._mark_feedback_shown()
        logger.info(
            f"[GameState] EmotionsGame evaluated round {self._round_num}: "
            f"correct={correct} signals_complete={result.signals_complete}"
        )
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = self.difficulty
            return {"tokens_earned": tokens, "animation": "confetti"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - (self.start_time or time.time())
        total_score = sum(t.score for t in self.trials)

        await event_bus.publish("ui.expression.change", "happy")
        self._transition(GameState.DONE)

        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )
