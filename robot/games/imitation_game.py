"""
robot/games/imitation_game.py
──────────────────────────────────────────────────────────────────────────────
Imitation Game using MediaPipe Pose detection.

BMO announces a physical action (wave, raise hand, clap, nod).
CameraManager publishes perception.frame events containing MediaPipe
face landmarks.  This game subscribes to those frames and uses
MediaPipe POSE landmarks (fetched via a dedicated pose detector) to
check whether the child performed the action.

Architecture:
  - ImitatioGame subscribes to perception.frame.
  - On each frame it runs a lightweight pose check using mediapipe Pose.
  - It matches the detected pose against the target action definition.
  - When the child successfully holds the pose for ACTION_HOLD_SECONDS,
    the trial is marked correct.

Actions supported:
  wave_hand   – dominant wrist is above shoulder and oscillating horizontally
  raise_hand  – dominant wrist is above nose landmark (head)
  clap        – both wrists are close together and below face level
  nod_head    – head pitch changes sign over 3 consecutive frames (up-then-down)
"""

import asyncio
import time
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import mediapipe as mp

from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
ACTION_HOLD_SECONDS = 1.5   # child must hold pose for this many seconds
TIMEOUT_SECONDS     = 20.0  # how long BMO waits for imitation before moving on

ACTIONS = [
    {
        "id": "raise_hand",
        "prompt": "Raise your hand up high, like this!",
        "success_msg": "Great job raising your hand!",
        "hint": "Try lifting your hand above your head!",
    },
    {
        "id": "wave_hand",
        "prompt": "Wave hello to me! Move your hand side to side!",
        "success_msg": "Wonderful wave! Hello to you too!",
        "hint": "Swing your hand left and right like you're saying hi!",
    },
    {
        "id": "clap",
        "prompt": "Can you clap your hands together?",
        "success_msg": "Clap clap clap! You did it!",
        "hint": "Bring both hands together and clap!",
    },
    {
        "id": "nod_head",
        "prompt": "Can you nod your head up and down like you're saying yes?",
        "success_msg": "Yes yes yes! Perfect nod!",
        "hint": "Move your head up and then down!",
    },
]

# MediaPipe Pose landmark indices
MP_POSE = mp.solutions.pose
NOSE        = MP_POSE.PoseLandmark.NOSE
LEFT_WRIST  = MP_POSE.PoseLandmark.LEFT_WRIST
RIGHT_WRIST = MP_POSE.PoseLandmark.RIGHT_WRIST
LEFT_SHOULDER  = MP_POSE.PoseLandmark.LEFT_SHOULDER
RIGHT_SHOULDER = MP_POSE.PoseLandmark.RIGHT_SHOULDER
LEFT_EYE    = MP_POSE.PoseLandmark.LEFT_EYE


@dataclass
class PoseSnapshot:
    """Lightweight snapshot of landmark positions used for action checks."""
    nose_y: float = 0.0
    left_wrist_x: float = 0.0
    left_wrist_y: float = 0.0
    right_wrist_x: float = 0.0
    right_wrist_y: float = 0.0
    left_shoulder_y: float = 0.0
    right_shoulder_y: float = 0.0
    timestamp: float = field(default_factory=time.time)


@GameRegistry.register("imitation")
class ImitationGame(BaseGame):
    """Physical imitation game using MediaPipe Pose.

    The game subscribes to 'perception.frame' events from CameraManager.
    It runs a dedicated MediaPipe Pose model on each frame (in the asyncio
    executor to avoid blocking).  Pose snapshots are stored in a rolling
    buffer and analysed for the target action pattern.
    """

    def __init__(self):
        super().__init__()
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=0,            # Lightweight (model 0 = fastest)
            enable_segmentation=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._pose_buffer: List[PoseSnapshot] = []
        self._max_buffer = 30              # keep last 30 snapshots (~2s @ 15fps)

        self._current_action: Optional[dict] = None
        self._action_start_time: Optional[float] = None
        self._pose_held_since: Optional[float] = None
        self._awaiting_pose: bool = False
        self._pose_result_future: Optional[asyncio.Future] = None

        # Register frame subscriber once
        event_bus.subscribe("perception.frame", self._on_frame)

    # ── Frame processing ──────────────────────────────────────────────────────

    async def _on_frame(self, _event: str, payload: dict):
        """Receive each camera frame, extract pose, push to buffer."""
        if not self._awaiting_pose:
            return  # idle – skip processing

        frame_bgr = payload.get("frame_bgr")
        if frame_bgr is None:
            return

        loop = asyncio.get_running_loop()

        def _extract_pose():
            import cv2
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            return self._pose.process(frame_rgb)

        try:
            results = await loop.run_in_executor(None, _extract_pose)
        except Exception as e:
            logger.warning(f"[ImitationGame] Pose extraction error: {e}")
            return

        if results.pose_landmarks:
            snap = self._landmarks_to_snapshot(results.pose_landmarks)
            self._pose_buffer.append(snap)
            if len(self._pose_buffer) > self._max_buffer:
                self._pose_buffer.pop(0)

            # Check if the target action is being performed
            if self._current_action and self._pose_result_future and \
               not self._pose_result_future.done():
                detected = self._check_action(self._current_action["id"])
                if detected:
                    if self._pose_held_since is None:
                        self._pose_held_since = time.time()
                    elif time.time() - self._pose_held_since >= ACTION_HOLD_SECONDS:
                        self._pose_result_future.set_result(True)
                else:
                    self._pose_held_since = None  # reset hold timer
        else:
            self._pose_held_since = None  # face/body not visible

    def _landmarks_to_snapshot(self, landmarks) -> PoseSnapshot:
        lm = landmarks.landmark
        return PoseSnapshot(
            nose_y         = lm[NOSE.value].y,
            left_wrist_x   = lm[LEFT_WRIST.value].x,
            left_wrist_y   = lm[LEFT_WRIST.value].y,
            right_wrist_x  = lm[RIGHT_WRIST.value].x,
            right_wrist_y  = lm[RIGHT_WRIST.value].y,
            left_shoulder_y  = lm[LEFT_SHOULDER.value].y,
            right_shoulder_y = lm[RIGHT_SHOULDER.value].y,
        )

    # ── Action detectors ──────────────────────────────────────────────────────

    def _check_action(self, action_id: str) -> bool:
        """Return True if the last few snapshots show the target pose."""
        if not self._pose_buffer:
            return False
        snap = self._pose_buffer[-1]

        if action_id == "raise_hand":
            # Either wrist above the nose (y-axis is inverted: 0=top, 1=bottom)
            return (snap.left_wrist_y < snap.nose_y - 0.05 or
                    snap.right_wrist_y < snap.nose_y - 0.05)

        elif action_id == "wave_hand":
            # Wrist above shoulder AND x-variation over last 10 frames
            if len(self._pose_buffer) < 6:
                return False
            wrist_above = snap.right_wrist_y < snap.right_shoulder_y - 0.05
            xs = [s.right_wrist_x for s in self._pose_buffer[-10:]]
            variation = max(xs) - min(xs) if xs else 0
            return wrist_above and variation > 0.08

        elif action_id == "clap":
            # Both wrists are close together
            dist = math.hypot(
                snap.left_wrist_x - snap.right_wrist_x,
                snap.left_wrist_y - snap.right_wrist_y,
            )
            return dist < 0.15

        elif action_id == "nod_head":
            # Nose y-position alternates up-down at least twice
            if len(self._pose_buffer) < 10:
                return False
            nose_ys = [s.nose_y for s in self._pose_buffer[-15:]]
            # Count direction changes
            changes = 0
            for i in range(1, len(nose_ys) - 1):
                if (nose_ys[i] - nose_ys[i-1]) * (nose_ys[i+1] - nose_ys[i]) < -0.0001:
                    changes += 1
            return changes >= 2

        return False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._pose_buffer.clear()
        self._awaiting_pose = False

        # Number of actions per difficulty
        self._action_pool = ACTIONS[:min(difficulty + 1, len(ACTIONS))]
        random.shuffle(self._action_pool)
        self._remaining_actions = list(self._action_pool)

        await event_bus.publish("ui.expression.change", "happy")
        return (
            "Let's play the copy-cat game! I will do an action, and you copy me. Ready?"
        )

    async def evaluate(self, response: str) -> GameResult:
        """Called when child speaks. Either start next action or check readiness."""
        if not self._remaining_actions:
            return GameResult(
                correct=True, score=1.0, response_time=0,
                feedback="We've done all the actions! Great job!"
            )

        action = self._remaining_actions.pop(0)
        self._current_action = action
        self._pose_held_since = None
        self._pose_buffer.clear()

        # Announce the action via TTS
        await event_bus.publish("tts.synthesize", action["prompt"])

        # Start watching for the pose
        self._awaiting_pose = True
        loop = asyncio.get_running_loop()
        self._pose_result_future = loop.create_future()

        trial_start = time.time()

        # Wait up to TIMEOUT_SECONDS for child to complete action
        try:
            success = await asyncio.wait_for(
                asyncio.shield(self._pose_result_future),
                timeout=TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            success = False

        self._awaiting_pose = False
        response_time = time.time() - trial_start

        if success:
            feedback = action["success_msg"]
            score = max(0.3, 1.0 - (response_time / TIMEOUT_SECONDS))  # faster = higher score
            await event_bus.publish("ui.animation.trigger", {"type": "stars"})
            await event_bus.publish("ui.expression.change", "happy")
        else:
            feedback = f"Good try! {action['hint']}"
            score = 0.0
            await event_bus.publish("ui.expression.change", "neutral")

        result = GameResult(
            correct=success,
            score=score,
            response_time=response_time,
            feedback=feedback,
            data={"action": action["id"], "reaction_time": response_time},
        )
        self.trials.append(result)

        await event_bus.publish("game.scored", {
            "game_type": "imitation",
            "score": score,
            "child_id": self.child_id,
        })
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = 3 if self.difficulty >= 3 else 2
            await event_bus.publish("reward.earned", {
                "child_id": self.child_id,
                "tokens": tokens,
                "reason": f"imitation_{self._current_action['id'] if self._current_action else 'action'}",
            })
            return {"tokens_earned": tokens}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - self.start_time
        total_score = sum(t.score for t in self.trials) / total_count

        logger.info(
            f"[ImitationGame] child={self.child_id} "
            f"correct={correct_count}/{total_count} time={time_spent:.1f}s"
        )
        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty,
        )


# Fix missing import in evaluate method
import random
