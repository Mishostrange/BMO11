"""
robot/games/memory_match_game.py
──────────────────────────────────────────────────────────────────────────────
Memory Card Matching Game for Autism Therapy.

The child guesses pairs by speaking two card names.
BMO announces all cards first, then prompts for guesses.
Game ends when all pairs are found (signals_complete=True).

State flow:
  start() → opening TTS
  next_question() → announce cards on first call, then ask for pair guess
  evaluate() → check guess, signals_complete when all pairs found
"""

import random
import time
import logging
from typing import List, Dict, Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

CARD_CATEGORIES: Dict[str, List[str]] = {
    "animals":  ["dog", "cat", "bird", "fish", "lion", "bear", "fox", "duck"],
    "colors":   ["red", "blue", "green", "yellow", "orange", "purple", "pink", "white"],
    "emotions": ["happy", "sad", "angry", "scared", "surprised", "calm", "excited", "tired"],
    "shapes":   ["circle", "square", "triangle", "star", "heart", "diamond", "oval", "rectangle"],
}

PAIRS_PER_LEVEL = {1: 3, 2: 4, 3: 6, 4: 8, 5: 10}


@GameRegistry.register("memory_match")
class MemoryMatchGame(BaseGame):
    """Voice-driven memory match game."""

    def __init__(self):
        super().__init__()
        self.cards: List[str] = []
        self.revealed: List[bool] = []
        self.pairs_found: int = 0
        self.total_pairs: int = 0
        self.move_count: int = 0
        self.category: str = "animals"
        self.trial_start: float = 0.0
        self._cards_announced: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self.move_count = 0
        self.pairs_found = 0
        self._cards_announced = False

        self.category = random.choice(list(CARD_CATEGORIES.keys()))
        num_pairs = PAIRS_PER_LEVEL.get(difficulty, 3)
        self.total_pairs = num_pairs
        pool = CARD_CATEGORIES[self.category][:num_pairs]
        self.cards = pool + pool
        random.shuffle(self.cards)
        self.revealed = [False] * len(self.cards)

        await event_bus.publish("ui.expression.change", "happy")
        await self._publish_state()
        self._transition(GameState.SHOW_QUESTION)

        return (
            f"Let's play the memory matching game with {self.category}! "
            f"I have {len(self.cards)} cards. "
            f"Let me tell you all of them first."
        )

    async def next_question(self) -> Optional[str]:
        """First call announces cards; subsequent calls ask for a pair guess."""
        if self.pairs_found >= self.total_pairs:
            self._transition(GameState.DONE)
            return None

        self.trial_start = time.time()

        if not self._cards_announced:
            self._cards_announced = True
            card_list = ", ".join(self.cards)
            prompt = f"The cards are: {card_list}. Now tell me two cards you think are the same!"
            logger.info(f"[GameState] MemoryMatchGame announcing {len(self.cards)} cards.")
        else:
            remaining = self.total_pairs - self.pairs_found
            prompt = f"{remaining} pair{'s' if remaining > 1 else ''} left. Tell me two cards that match!"

        await self._publish_state(prompt)
        self._mark_question_shown()
        return prompt

    # ── Evaluation ─────────────────────────────────────────────────────────────

    async def evaluate(self, response: str) -> GameResult:
        response_time = time.time() - self.trial_start
        self.trial_start = time.time()
        self.move_count += 1

        response_lower = response.lower()
        category_words = CARD_CATEGORIES[self.category]
        mentioned = [card for card in category_words if card in response_lower]

        if not mentioned:
            result = GameResult(
                correct=False, score=0.0, response_time=response_time,
                feedback=f"I didn't catch that. Tell me two {self.category} names that match!",
                signals_complete=False,
            )
            self.trials.append(result)
            self._mark_feedback_shown()
            return result

        card = mentioned[0]
        unmatched = [c for c, rev in zip(self.cards, self.revealed) if c == card and not rev]

        if len(unmatched) >= 2:
            # Found a pair!
            self.pairs_found += 1
            found = 0
            for i, (c, rev) in enumerate(zip(self.cards, self.revealed)):
                if c == card and not rev:
                    self.revealed[i] = True
                    found += 1
                    if found == 2:
                        break

            score = 1.0
            all_done = self.pairs_found >= self.total_pairs

            if all_done:
                feedback = (
                    f"Yes! {card} and {card} match! "
                    f"Amazing! You found all {self.total_pairs} pairs in {self.move_count} moves!"
                )
                await self.trigger_success_video_once()
                await event_bus.publish("ui.animation.trigger", {"type": "fireworks"})
            else:
                remaining = self.total_pairs - self.pairs_found
                feedback = (
                    f"Yes! {card} matches! Great memory! "
                    f"{remaining} pair{'s' if remaining > 1 else ''} left. Keep going!"
                )
                await event_bus.publish("ui.animation.trigger", {"type": "stars"})
                await event_bus.publish("ui.expression.change", "happy")

            result = GameResult(
                correct=True, score=score, response_time=response_time,
                feedback=feedback,
                data={"card": card, "pairs_found": self.pairs_found, "moves": self.move_count},
                signals_complete=all_done,
            )
        else:
            result = GameResult(
                correct=False, score=0.0, response_time=response_time,
                feedback=f"Hmm, that one is already found or doesn't match. Try another {self.category}!",
                data={"card": card, "pairs_found": self.pairs_found, "moves": self.move_count},
                signals_complete=False,
            )
            await self.trigger_failure_video_once()

        self.trials.append(result)
        await self._publish_state()
        await event_bus.publish("game.scored", {
            "game_type": "memory_match", "score": result.score, "child_id": self.child_id,
        })

        logger.info(
            f"[GameState] MemoryMatchGame evaluated: card='{card}' correct={result.correct} "
            f"pairs={self.pairs_found}/{self.total_pairs} signals_complete={result.signals_complete}"
        )
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = 2 if self.difficulty >= 3 else 1
            return {"tokens_earned": tokens, "animation": "stars"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        accuracy = self.pairs_found / max(1, self.total_pairs)
        time_spent = time.time() - (self.start_time or time.time())
        await event_bus.publish("ui.expression.change", "happy")
        self._transition(GameState.DONE)

        logger.info(
            f"[MemoryMatch] child={self.child_id} pairs={self.pairs_found}/{self.total_pairs} "
            f"moves={self.move_count} time={time_spent:.1f}s"
        )
        return GameSummary(
            total_score=accuracy,
            correct_count=self.pairs_found,
            total_count=self.total_pairs,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _publish_state(self, prompt: str = "Say two card names that match!"):
        await event_bus.publish("game.state_update", {
            "game_type": "memory_match",
            "state": {
                "cards": self.cards,
                "revealed": self.revealed,
                "category": self.category,
                "pairs_found": self.pairs_found,
                "total_pairs": self.total_pairs,
            },
            "prompt": prompt,
            "score_text": f"Pairs: {self.pairs_found}/{self.total_pairs}",
        })
