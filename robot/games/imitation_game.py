"""
robot/games/imitation_game.py
──────────────────────────────────────────────────────────────────────────────
Physical Imitation Game.
MediaPipe Pose has been removed to support Raspberry Pi / aarch64.
Uses a time-based auto-pass heuristic (OpenCV support layer placeholder).
"""

import asyncio
import time
import logging
import random
from typing import Optional, List

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

ACTION_HOLD_SECONDS = 1.5
AUTO_PASS_SECONDS   = 5.0   # Time after which the heuristic grants success
TIMEOUT_SECONDS     = 20.0

ACTIONS = [
    {"id": "raise_hand",  "prompt": "Raise your hand up high, like this!",                        "success_msg": "Great job raising your hand!",    "hint": "Try lifting your hand above your head!"},
    {"id": "wave_hand",   "prompt": "Wave hello to me! Move your hand side to side!",             "success_msg": "Wonderful wave! Hello to you too!", "hint": "Swing your hand left and right like you're saying hi!"},
    {"id": "clap",        "prompt": "Can you clap your hands together?",                           "success_msg": "Clap clap clap! You did it!",       "hint": "Bring both hands together and clap!"},
    {"id": "nod_head",    "prompt": "Can you nod your head up and down like you're saying yes?",  "success_msg": "Yes yes yes! Perfect nod!",         "hint": "Move your head up and then down!"},
]


@GameRegistry.register("imitation")
class ImitationGame(BaseGame):
    """Physical imitation game.
    
    MediaPipe has been removed for aarch64 compatibility.
    Uses a time-based heuristic as an OpenCV support layer placeholder.
    """

    MAX_ROUNDS = 3

    def __init__(self):
        super().__init__()
        self.current_action: Optional[dict] = None
        self._remaining_actions: List[dict] = []
        self.trial_start_time: float = 0.0
        self.pose_held_since: Optional[float] = None
        self._pose_result_future: Optional[asyncio.Future] = None
        event_bus.subscribe("perception.frame", self._on_frame)

    # ── Frame processing ───────────────────────────────────────────────────────

    async def _on_frame(self, event_type: str, payload: dict):
        """Simulate pose detection: auto-pass after AUTO_PASS_SECONDS."""
        if self.state != GameState.WAIT_ANSWER or not self.current_action:
            return
        if self._pose_result_future is None or self._pose_result_future.done():
            return

        now = time.time()
        if now - self.trial_start_time > AUTO_PASS_SECONDS:
            if not self.pose_held_since:
                self.pose_held_since = now
            elif now - self.pose_held_since >= ACTION_HOLD_SECONDS:
                logger.info(f"[ImitationGame] Auto-pass for action '{self.current_action['id']}'")
                self._pose_result_future.set_result(True)
        else:
            self.pose_held_since = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        pool = ACTIONS[:min(difficulty + 1, len(ACTIONS))]
        random.shuffle(pool)
        self._remaining_actions = list(pool)
        self.current_action = None
        self.pose_held_since = None
        self._transition(GameState.SHOW_QUESTION)
        await event_bus.publish("ui.expression.change", "happy")
        return "Let's play the copy-cat game! I will do an action, and you copy me. Ready?"

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS or not self._remaining_actions:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        self.current_action = self._remaining_actions.pop(0)
        self.pose_held_since = None
        self.trial_start_time = time.time()

        logger.info(f"[GameState] ImitationGame round {self._round_num}/{self.MAX_ROUNDS} action={self.current_action['id']}")

        loop = asyncio.get_running_loop()
        self._pose_result_future = loop.create_future()

        self._mark_question_shown()
        return self.current_action["prompt"]

    async def evaluate(self, response: str) -> GameResult:
        """Wait for pose detection to complete (or timeout)."""
        if not self.current_action or self._pose_result_future is None:
            logger.warning("[ImitationGame] evaluate() called with no active action.")
            return GameResult(correct=False, score=0.0, response_time=0.0,
                              feedback="Let me show you an action to copy!")

        trial_start = self.trial_start_time

        try:
            success = await asyncio.wait_for(
                asyncio.shield(self._pose_result_future),
                timeout=TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            success = False

        response_time = time.time() - trial_start

        if success:
            feedback = self.current_action["success_msg"]
            await self.trigger_success_video_once()
            score = max(0.3, 1.0 - (response_time / TIMEOUT_SECONDS))
        else:
            feedback = f"Good try! {self.current_action['hint']}"
            await self.trigger_failure_video_once()
            score = 0.0

        result = GameResult(
            correct=success, score=score, response_time=response_time,
            feedback=feedback,
            data={"action": self.current_action["id"]},
            signals_complete=(self._round_num >= self.MAX_ROUNDS or not self._remaining_actions),
        )
        self.trials.append(result)
        self.current_action = None

        await event_bus.publish("game.scored", {
            "game_type": "imitation", "score": score, "child_id": self.child_id,
        })
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = 3 if self.difficulty >= 3 else 2
            return {"tokens_earned": tokens}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - (self.start_time or time.time())
        total_score = sum(t.score for t in self.trials) / max(1, total_count)
        self._transition(GameState.DONE)

        return GameSummary(
            total_score=total_score, correct_count=correct_count,
            total_count=total_count, time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )
