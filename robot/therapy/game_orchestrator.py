"""
robot/therapy/game_orchestrator.py
──────────────────────────────────────────────────────────────────────────────
Game Orchestrator — Bridges TherapyEngine ↔ GameRegistry.

Responsibilities:
  1. Listens for 'game.launch' events (from dashboard or voice commands).
  2. Instantiates the correct game via GameRegistry.
  3. Pumps child utterances through game.evaluate() instead of the normal
     TherapyEngine pipeline while a game is active.
  4. Saves results to game_results table when the game ends.
  5. Notifies AdaptiveDifficulty to update the child's level.
  6. Returns control to TherapyEngine when the game is finished.

Integration:
  TherapyEngine._on_speech_transcribed checks:
      orchestrator.is_game_active()  → if True, forward to orchestrator
  Otherwise normal conversation pipeline runs.
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any

from robot.games.game_registry import GameRegistry
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.database.connection import db
from robot.services.event_bus import event_bus
from robot.difficulty.adaptive import AdaptiveDifficulty

logger = logging.getLogger(__name__)

# How many turns can a game last before auto-finishing
MAX_GAME_TURNS = 20


class GameOrchestrator:
    """
    Singleton-style coordinator that sits between TherapyEngine and games.

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

        # Subscribe to launch events from dashboard & voice commands
        event_bus.subscribe("game.launch", self._on_game_launch)
        event_bus.subscribe("session.ended", self._on_session_ended)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_game_active(self) -> bool:
        return self._active_game is not None

    async def handle_speech(self, text: str) -> Optional[str]:
        """
        Called by TherapyEngine when a game is active.
        Returns the feedback string to be spoken, or None.
        """
        if self._active_game is None:
            return None

        # Check for "stop" / "finish" keywords
        if any(kw in text.lower() for kw in ["stop", "quit", "finish", "done", "bye", "exit"]):
            await self._finish_game()
            return "Okay, let's finish the game. Great job today!"

        self._turn_count += 1
        if self._turn_count > MAX_GAME_TURNS:
            await self._finish_game()
            return "We played a lot! Let's take a break. Great job!"

        try:
            result: GameResult = await self._active_game.evaluate(text)
            reward_data = await self._active_game.reward(result)

            # Publish reward if tokens earned
            if reward_data.get("tokens_earned", 0) > 0 and self._active_child_id:
                await event_bus.publish("reward.earned", {
                    "child_id": self._active_child_id,
                    "tokens": reward_data["tokens_earned"],
                    "reason": f"{self._active_game_type}_correct",
                })

            # If game signals completion (all pairs found, etc.)
            if self._is_game_complete(result):
                summary = await self._active_game.finish()
                await self._save_result(summary)
                self._active_game = None
                self._active_game_type = None
                return result.feedback + " You finished the game! Amazing work!"

            return result.feedback

        except Exception as e:
            logger.error(f"[GameOrchestrator] Game evaluation error: {e}", exc_info=True)
            return "Oops, something went wrong. Let's try again!"

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
            logger.warning("[GameOrchestrator] Launch already in progress, ignoring duplicate event.")
            return

        self._is_starting = True
        try:
            # Finish any existing game cleanly
            if self._active_game:
                await self._finish_game()

            await self._start_game(game_type, child_id, session_id)
        finally:
            self._is_starting = False

    async def _on_session_ended(self, _event: str, session_id: int):
        """If the therapy session ends abruptly, terminate the active game without saving."""
        if self._active_game and self._active_session_id == session_id:
            logger.info(f"[GameOrchestrator] Session {session_id} ended. Aborting active game '{self._active_game_type}'.")
            self._active_game = None
            self._active_game_type = None

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

        # Switch display to the game screen and activate board FIRST
        # so that when game.start() publishes its initial state, it isn't wiped out.
        await event_bus.publish("ui.screen.change", "game")
        await event_bus.publish("ui.expression.change", "happy")
        await event_bus.publish("game.board.show", {"game_type": game_type})

        try:
            opening = await game.start(child_id, difficulty)
        except Exception as e:
            logger.error(f"[GameOrchestrator] Game start error: {e}", exc_info=True)
            return

        self._active_game      = game
        self._active_game_type = game_type
        self._active_child_id  = child_id
        self._active_session_id = session_id
        self._turn_count       = 0

        await event_bus.publish("tts.synthesize", opening)

        logger.info(f"[GameOrchestrator] Started '{game_type}' for child {child_id} "
                    f"at difficulty {difficulty}")

    async def _finish_game(self):
        """Force-finish the active game and save the result."""
        if not self._active_game:
            return
        try:
            summary = await self._active_game.finish()
            await self._save_result(summary)
        except Exception as e:
            logger.error(f"[GameOrchestrator] Error finishing game: {e}")
        finally:
            # Return to face screen
            await event_bus.publish("ui.screen.change", "face")
            self._active_game      = None
            self._active_game_type = None
            self._turn_count       = 0

    def _is_game_complete(self, result: GameResult) -> bool:
        """Heuristic: game is done if result feedback contains completion phrases."""
        completion_phrases = [
            "all pairs", "finished", "you found all", "all actions", "great job today"
        ]
        return any(p in result.feedback.lower() for p in completion_phrases)

    async def _save_result(self, summary: GameSummary):
        """Persist a GameSummary to game_results and run adaptive difficulty."""
        if not self._active_child_id or not self._active_game_type:
            return
        try:
            accuracy = (summary.correct_count / max(1, summary.total_count))
            avg_rt   = summary.time_spent / max(1, summary.total_count)

            with db.get_cursor() as cursor:
                session_id = self._active_session_id
                if not session_id:
                    # Find the most recent session for this child to satisfy NOT NULL constraint
                    cursor.execute("SELECT id FROM sessions WHERE child_id = ? ORDER BY start_time DESC LIMIT 1", (self._active_child_id,))
                    row = cursor.fetchone()
                    if row:
                        session_id = row['id']
                    else:
                        # Create a dummy session if none exist
                        cursor.execute("INSERT INTO sessions (child_id, start_time) VALUES (?, CURRENT_TIMESTAMP)", (self._active_child_id,))
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
                        self._active_child_id,
                        self._active_game_type,
                        summary.difficulty_achieved,
                        accuracy,
                        summary.correct_count,
                        summary.total_count,
                        avg_rt,
                        1,
                        json.dumps({"total_score": summary.total_score}),
                    ),
                )

            # Let adaptive difficulty reassess
            self.adaptive.process_results(self._active_child_id, self._active_game_type)

            # Publish so progress tracker and badge engine also react
            await event_bus.publish("game.finished", {
                "child_id":   self._active_child_id,
                "game_type":  self._active_game_type,
                "accuracy":   accuracy,
                "difficulty": summary.difficulty_achieved,
            })

            logger.info(
                f"[GameOrchestrator] Saved '{self._active_game_type}' result: "
                f"acc={accuracy:.1%} diff={summary.difficulty_achieved}"
            )
        except Exception as e:
            logger.error(f"[GameOrchestrator] DB save error: {e}")
