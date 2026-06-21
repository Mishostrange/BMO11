"""
robot/games/memory_match_game.py
──────────────────────────────────────────────────────────────────────────────
Memory Card Matching Game for Autism Therapy.

The child flips cards to find matching pairs.
Categories: animals, colors, emotions, shapes.
All interaction is voice-driven - BMO announces cards and tracks progress.
Difficulty controls the grid size: Easy=4 pairs, Medium=6, Hard=8.
"""

import random
import time
import logging
from typing import List, Dict, Optional, Tuple

from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

# ── Card sets per category ───────────────────────────────────────────────────
CARD_CATEGORIES: Dict[str, List[str]] = {
    "animals":  ["dog", "cat", "bird", "fish", "lion", "bear", "fox", "duck"],
    "colors":   ["red", "blue", "green", "yellow", "orange", "purple", "pink", "white"],
    "emotions": ["happy", "sad", "angry", "scared", "surprised", "calm", "excited", "tired"],
    "shapes":   ["circle", "square", "triangle", "star", "heart", "diamond", "oval", "rectangle"],
}

# Number of pairs per difficulty level
PAIRS_PER_LEVEL = {1: 3, 2: 4, 3: 6, 4: 8, 5: 10}


@GameRegistry.register("memory_match")
class MemoryMatchGame(BaseGame):
    """Voice-driven memory match game.
    
    Game flow:
      1. BMO announces all cards in sequence (so child knows what's there).
      2. BMO asks: "Tell me two cards you think match!"
      3. Child responds with two card names (e.g. "dog and dog").
      4. BMO checks if they match and gives feedback.
      5. Continue until all pairs are found.
    """

    def __init__(self):
        super().__init__()
        self.cards: List[str] = []           # all cards (contains duplicates for pairs)
        self.revealed: List[bool] = []       # which cards have been matched
        self.pairs_found: int = 0
        self.total_pairs: int = 0
        self.move_count: int = 0
        self.category: str = "animals"
        self.trial_start: Optional[float] = None
        self._announced = False              # whether we've announced the cards yet

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self.move_count = 0
        self.pairs_found = 0
        self._announced = False

        # Pick a random category
        self.category = random.choice(list(CARD_CATEGORIES.keys()))
        num_pairs = PAIRS_PER_LEVEL.get(difficulty, 4)
        self.total_pairs = num_pairs

        # Build deck (each card appears exactly twice)
        pool = CARD_CATEGORIES[self.category][:num_pairs]
        self.cards = pool + pool  # duplicate for pairs
        random.shuffle(self.cards)
        self.revealed = [False] * len(self.cards)

        await event_bus.publish("ui.expression.change", "happy")
        await self._publish_state()
        return (
            f"Let's play the memory matching game with {self.category}! "
            f"I have {len(self.cards)} cards face down. "
            f"Let me tell you all the {self.category} on them first. "
        )

    async def _publish_state(self, prompt: str = "Say two card names that match!"):
        """Push current board state to the visual game screen."""
        await event_bus.publish("game.state_update", {
            "game_type": "memory_match",
            "state": {
                "cards":       self.cards,
                "revealed":    self.revealed,
                "category":    self.category,
                "pairs_found": self.pairs_found,
                "total_pairs": self.total_pairs,
            },
            "prompt":     prompt,
            "score_text": f"Pairs: {self.pairs_found}/{self.total_pairs}",
        })

    async def _announce_cards(self) -> str:
        """Return a string listing all cards (child hears them to memorize)."""
        self._announced = True
        card_list = ", ".join(self.cards)
        return f"The cards are: {card_list}. Now tell me two cards you think are the same!"

    async def evaluate(self, response: str) -> GameResult:
        """Parse the child's response for two card names and check if they match."""
        # First turn: announce the cards
        if not self._announced:
            announcement = await self._announce_cards()
            self.trial_start = time.time()
            return GameResult(correct=False, score=0, response_time=0, feedback=announcement)

        response_time = time.time() - (self.trial_start or time.time())
        self.trial_start = time.time()
        self.move_count += 1

        # ── Find two card names mentioned in the child's response ─────────────
        response_lower = response.lower()
        mentioned = [card for card in CARD_CATEGORIES[self.category]
                     if card in response_lower]

        if len(mentioned) < 1:
            return GameResult(
                correct=False, score=0, response_time=response_time,
                feedback=f"I didn't understand. Tell me two {self.category} that match!"
            )

        # If the child says the same word twice (or says one word twice), treat as a pair guess
        if len(mentioned) == 1:
            # Check if this card actually has an unmatched pair
            card = mentioned[0]
        else:
            card = mentioned[0]  # first mentioned

        # Count unmatched occurrences of this card
        unmatched = [c for c, revealed in zip(self.cards, self.revealed)
                     if c == card and not revealed]

        if len(unmatched) >= 2:
            # Found a matching pair!
            self.pairs_found += 1
            # Mark both as revealed
            found = 0
            for i, (c, rev) in enumerate(zip(self.cards, self.revealed)):
                if c == card and not rev:
                    self.revealed[i] = True
                    found += 1
                    if found == 2:
                        break

            score = 1.0
            correct = True

            if self.pairs_found == self.total_pairs:
                feedback = (
                    f"That's right, {card} and {card} match! "
                    f"Amazing! You found all {self.total_pairs} pairs in {self.move_count} moves!"
                )
                await event_bus.publish("ui.animation.trigger", {"type": "fireworks"})
                await event_bus.publish("ui.expression.change", "happy")
            else:
                remaining = self.total_pairs - self.pairs_found
                feedback = (
                    f"Yes! {card} and {card} match! Great memory! "
                    f"{remaining} pair{'s' if remaining > 1 else ''} left. Keep going!"
                )
                await event_bus.publish("ui.animation.trigger", {"type": "stars"})
        else:
            score = 0.0
            correct = False
            feedback = (
                f"Hmm, {card} and {card} don't make a pair, or they are already found. "
                f"Try another card!"
            )
            await event_bus.publish("ui.expression.change", "neutral")

        # Update the visual board
        await self._publish_state()

        result = GameResult(
            correct=correct,
            score=score,
            response_time=response_time,
            feedback=feedback,
            data={"card_guessed": card, "pairs_found": self.pairs_found, "moves": self.move_count},
        )
        self.trials.append(result)

        # Emit progress event
        await event_bus.publish("game.scored", {
            "game_type": "memory_match",
            "score": score,
            "child_id": self.child_id,
        })
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = 2 if self.difficulty >= 3 else 1
            await event_bus.publish("reward.earned", {
                "child_id": self.child_id,
                "tokens": tokens,
                "reason": "memory_match_pair",
            })
            return {"tokens_earned": tokens, "animation": "stars"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - self.start_time

        # Accuracy: pairs found vs total pairs
        accuracy = self.pairs_found / max(1, self.total_pairs)
        await event_bus.publish("ui.expression.change", "happy")

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
