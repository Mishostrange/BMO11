"""
robot/therapy/game_orchestrator.py
──────────────────────────────────────────────────────────────────────────────
Game Orchestrator — Bridges TherapyEngine ↔ GameRegistry.

Responsibilities:
  1. Listens for 'game.launch' events (from dashboard or voice commands).
  2. Instantiates the correct game via GameRegistry.
  3. Pumps child utterances through game.evaluate() instead of the normal
     TherapyEngine pipeline while a game is active.
  4. After each evaluate(), calls game.next_question() to advance state.
  5. Reads GameResult.signals_complete to determine when game is done.
  6. Saves results to game_results table when the game ends.
  7. Notifies AdaptiveDifficulty to update the child's level.
  8. Returns control to TherapyEngine when the game is finished.

Event Flow:
  game.launch → _start_game → opening TTS → WAIT_ANSWER
  speech → handle_speech → evaluate() → reward() → feedback TTS
    → if not done: next_question() → WAIT_ANSWER
    → if done: finish() → _save_result → ui.screen.change(face)
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any

from robot.games.game_registry import GameRegistry
from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.database.connection import db
from robot.services.event_bus import event_bus
from robot.difficulty.adaptive import AdaptiveDifficulty
from robot.therapy.state_manager import state_manager, CompanionState

logger = logging.getLogger(__name__)

# Hard cap: regardless of game signals, force-end after this many turns
MAX_GAME_TURNS = 30
# Minimum delay (seconds) between answer feedback and next question
NEXT_QUESTION_DELAY = 1.5


class GameOrchestrator:
    """
    Coordinator that sits between TherapyEngine and games.

    Usage in TherapyEngine:
        if self.game_orchestrator.is_game_active():
            await self.game_orchestrator.handle_speech(text)
        else:
            # normal pipeline
    """

    def __init__(self, adaptive_difficulty: AdaptiveDifficulty):
        self.adaptive = adaptive_difficulty

        self._active_game: Optional[BaseGame] = None
        self._active_game_type: Optional[str] = None
        self._active_child_id: Optional[int] = None
        self._active_session_id: Optional[int] = None
        self._turn_count: int = 0
        self._is_starting: bool = False  # guard against double-launch
        self._processing_answer: bool = False  # guard against re-entrant calls
        self._pending_badge_names = []  # badges earned during game, announced at end

        # Subscribe to launch events from dashboard & voice commands
        event_bus.subscribe("game.launch", self._on_game_launch)
        event_bus.subscribe("session.ended", self._on_session_ended)
        event_bus.subscribe("badge.earned", self._on_badge_earned)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_game_active(self) -> bool:
        return self._active_game is not None

    async def handle_speech(self, text: str) -> Optional[str]:
        """
        Called by TherapyEngine when a game is active.
        Returns the feedback string to be spoken via TTS, or None.
        """
        if self._active_game is None:
            return None

        # Guard: don't process a second answer while already processing one
        if self._processing_answer:
            logger.debug("[GameOrchestrator] Ignoring speech — already processing an answer.")
            return None

        # Check for stop / go back commands
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["go back", "back to games", "back to the game room"]):
            await self._finish_game(early=True, return_screen="games_dashboard")
            return "Okay, let's go back to the game room."

        if any(kw in text_lower for kw in ["stop game", "quit game", "finish game", "end game", "stop playing", "don't want to play", "dont want to play"]):
            await self._finish_game(early=True, return_screen="face")
            return "Okay, let's stop playing. You did a great job!"

        # Hard turn cap
        self._turn_count += 1
        if self._turn_count > MAX_GAME_TURNS:
            logger.warning("[GameOrchestrator] Max turns reached, force-finishing game.")
            await self._finish_game(early=True)
            return "We've been playing for a while! Let's take a break. Amazing work!"

        self._processing_answer = True
        try:
            game = self._active_game
            game_type = self._active_game_type
            child_id = self._active_child_id

            logger.info(
                f"[GameState] Processing answer turn={self._turn_count} "
                f"state={game.state.name} game={game_type} text='{text[:40]}'"
            )

            # ── Evaluate ──
            result: GameResult = await game.evaluate(text)

            logger.info(
                f"[GameState] evaluate() done: correct={result.correct} "
                f"score={result.score:.2f} signals_complete={result.signals_complete}"
            )

            # ── Reward (animations only, no TTS) ──
            reward_data = await game.reward(result)

            if reward_data.get("tokens_earned", 0) > 0 and child_id:
                await event_bus.publish("reward.earned", {
                    "child_id": child_id,
                    "tokens": reward_data["tokens_earned"],
                    "reason": f"{game_type}_correct",
                })

            # ── Speak feedback ──
            feedback_text = result.feedback
            await event_bus.publish("tts.synthesize", feedback_text)

            # ── Transition ──
            if result.signals_complete or game.state == GameState.DONE:
                # Game complete
                logger.info(f"[GameState] Game '{game_type}' signalled complete. Finishing.")
                await asyncio.sleep(NEXT_QUESTION_DELAY)
                await self._finish_game(early=False)
                return None

            # Ask next question after a short pause
            await asyncio.sleep(NEXT_QUESTION_DELAY)
            next_q = await game.next_question()
            if next_q:
                logger.info(f"[GameState] Asking next question: '{next_q[:60]}'")
                await event_bus.publish("tts.synthesize", next_q)
            else:
                # Game has no more questions — finish
                logger.info(f"[GameState] No next question returned — finishing '{game_type}'.")
                await self._finish_game(early=False)

            return None  # feedback already published via event bus

        except Exception as e:
            logger.error(f"[GameOrchestrator] Game evaluation error: {e}", exc_info=True)
            return "Oops, something went wrong. Let's try again!"
        finally:
            self._processing_answer = False

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_game_launch(self, _event: str, data: Dict[str, Any]):
        """Launch a game from a dashboard click or voice command."""
        game_type = data.get("game_type")
        child_id  = data.get("child_id")
        session_id = data.get("session_id")

        if not game_type or not child_id:
            logger.warning("[GameOrchestrator] game.launch missing game_type or child_id")
            return

        if self._is_starting:
            logger.warning("[GameOrchestrator] Launch already in progress, ignoring duplicate.")
            return

        self._is_starting = True
        try:
            if self._active_game:
                await self._finish_game(early=True)
            await self._start_game(game_type, child_id, session_id)
        finally:
            self._is_starting = False

    async def _on_session_ended(self, _event: str, session_id: int):
        """If the therapy session ends abruptly, terminate active game."""
        if self._active_game and self._active_session_id == session_id:
            logger.info(f"[GameOrchestrator] Session {session_id} ended. Aborting game.")
            self._active_game = None
            self._active_game_type = None

    async def _on_badge_earned(self, _event: str, data: Dict[str, Any]):
        """Collect badge announcements during game — deliver after game ends."""
        badge = data.get("badge", {})
        name = badge.get("name", "")
        if name:
            self._pending_badge_names.append(name)
            logger.info(f"[GameOrchestrator] Badge queued for post-game announcement: '{name}'")

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _start_game(self, game_type: str, child_id: int,
                          session_id: Optional[int] = None):
        """Instantiate game, get opening prompt, and begin."""
        try:
            game = GameRegistry.get_game(game_type)
        except ValueError as e:
            logger.error(f"[GameOrchestrator] {e}")
            await event_bus.publish("tts.synthesize", "Sorry, I don't know that game yet.")
            return

        difficulty = self.adaptive.get_difficulty(child_id)

        # Switch UI to game screen and update state
        state_manager.set_state(CompanionState.IN_GAME)
        await event_bus.publish("ui.screen.change", "game")
        await event_bus.publish("ui.expression.change", "happy")
        await event_bus.publish("game.board.show", {"game_type": game_type})

        try:
            opening = await game.start(child_id, difficulty)
        except Exception as e:
            logger.error(f"[GameOrchestrator] Game start error: {e}", exc_info=True)
            return

        self._active_game       = game
        self._active_game_type  = game_type
        self._active_child_id   = child_id
        self._active_session_id = session_id
        self._turn_count        = 0
        self._pending_badge_names = []

        await event_bus.publish("tts.synthesize", opening)
        logger.info(
            f"[GameOrchestrator] Started '{game_type}' for child {child_id} "
            f"at difficulty {difficulty}"
        )

        # Ask first question after a short pause
        await asyncio.sleep(NEXT_QUESTION_DELAY)
        first_q = await game.next_question()
        if first_q:
            logger.info(f"[GameState] First question: '{first_q[:60]}'")
            await event_bus.publish("tts.synthesize", first_q)

    async def _finish_game(self, early: bool = False, return_screen: str = "face"):
        """Force-finish the active game and save the result."""
        if not self._active_game:
            return

        game = self._active_game
        game_type = self._active_game_type

        # Defensive: clear active game FIRST to prevent re-entry
        self._active_game      = None
        self._active_game_type = None
        self._turn_count       = 0

        try:
            summary = await game.finish()
            await self._save_result(summary, game_type)
        except Exception as e:
            logger.error(f"[GameOrchestrator] Error finishing game: {e}")

        # Return to requested screen (face or games_dashboard)
        await event_bus.publish("ui.screen.change", return_screen)
        if return_screen == "face":
            await event_bus.publish("ui.expression.change", "happy")

        # BADGE SYSTEM: Badges are INTERNAL ONLY.
        # Do NOT announce badge names, streak messages, or reward points via TTS.
        # Clear the queue silently.
        if self._pending_badge_names:
            logger.info(
                f"[GameOrchestrator] Badges earned (internal only, not spoken): "
                f"{', '.join(self._pending_badge_names)}"
            )
            self._pending_badge_names = []

        # Restore state to IDLE — game is fully over
        state_manager.set_state(CompanionState.IDLE)
        logger.info(f"[GameOrchestrator] Game '{game_type}' finished (early={early}).")

    async def _save_result(self, summary: GameSummary, game_type: str):
        """Persist a GameSummary to game_results and run adaptive difficulty."""
        child_id = self._active_child_id
        if not child_id or not game_type:
            return
        try:
            accuracy = summary.correct_count / max(1, summary.total_count)
            avg_rt   = summary.time_spent / max(1, summary.total_count)

            with db.get_cursor() as cursor:
                session_id = self._active_session_id
                if not session_id:
                    cursor.execute(
                        "SELECT id FROM sessions WHERE child_id = ? ORDER BY start_time DESC LIMIT 1",
                        (child_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        session_id = row["id"]
                    else:
                        cursor.execute(
                            "INSERT INTO sessions (child_id, start_time) VALUES (?, CURRENT_TIMESTAMP)",
                            (child_id,)
                        )
                        session_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO game_results
                        (session_id, child_id, game_type, difficulty_level,
                         score, correct_count, total_count, response_time_avg,
                         completed, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        child_id,
                        game_type,
                        summary.difficulty_achieved,
                        accuracy,
                        summary.correct_count,
                        summary.total_count,
                        avg_rt,
                        1,
                        json.dumps({"total_score": summary.total_score}),
                    ),
                )

            self.adaptive.process_results(child_id, game_type)

            await event_bus.publish("game.finished", {
                "child_id":   child_id,
                "game_type":  game_type,
                "accuracy":   accuracy,
                "difficulty": summary.difficulty_achieved,
            })

            logger.info(
                f"[GameOrchestrator] Saved '{game_type}' result: "
                f"acc={accuracy:.1%} diff={summary.difficulty_achieved}"
            )
        except Exception as e:
            logger.error(f"[GameOrchestrator] DB save error: {e}")
