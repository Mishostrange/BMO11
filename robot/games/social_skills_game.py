"""
robot/games/social_skills_game.py
"""

import random
import time
import logging
from typing import Optional

from robot.games.base_game import BaseGame, GameResult, GameSummary, GameState
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

SCENARIOS = [
    {
        "category": "greeting",
        "story": "You walk into the classroom and see your teacher. What should you say?",
        "keywords": ["hi", "hello", "good morning", "morning", "greet"],
        "hint": "say hello",
    },
    {
        "category": "sharing",
        "story": "You are playing with blocks and your friend asks if they can play too. What should you do?",
        "keywords": ["share", "yes", "give", "play together", "of course", "sure"],
        "hint": "share your blocks",
    },
    {
        "category": "helping",
        "story": "Your friend drops his toy on the floor. What should you do?",
        "keywords": ["pick", "help", "give", "ask"],
        "hint": "help them pick it up",
    },
    {
        "category": "taking_turns",
        "story": "There is only one swing at the park, and someone else is using it. What should you do?",
        "keywords": ["wait", "turn", "ask", "patient", "line"],
        "hint": "wait for your turn",
    },
    {
        "category": "asking_for_help",
        "story": "You can't reach a book on a high shelf. What should you do?",
        "keywords": ["ask", "help", "mom", "dad", "teacher", "adult", "please"],
        "hint": "ask an adult for help",
    },
]


@GameRegistry.register("social_skills")
class SocialSkillsGame(BaseGame):
    MAX_ROUNDS = 3

    def __init__(self):
        super().__init__()
        self.current_scenario = None
        self.trial_start: float = 0.0
        self._scenario_pool = []

    async def start(self, child_id: int, difficulty: int) -> str:
        await super().start(child_id, difficulty)
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self._round_num = 0
        self.current_scenario = None
        self._scenario_pool = random.sample(SCENARIOS, min(self.MAX_ROUNDS, len(SCENARIOS)))
        self._transition(GameState.SHOW_QUESTION)
        return "Let's practice being a good friend! I will tell you a story, and you tell me what you would do."

    async def next_question(self) -> Optional[str]:
        if self._round_num >= self.MAX_ROUNDS or not self._scenario_pool:
            self._transition(GameState.DONE)
            return None

        self._round_num += 1
        self.current_scenario = self._scenario_pool.pop(0)
        logger.info(f"[GameState] SocialSkillsGame round {self._round_num}/{self.MAX_ROUNDS} category={self.current_scenario['category']}")

        await event_bus.publish("ui.expression.change", "thinking")
        prompt = f"Listen carefully: {self.current_scenario['story']}"
        self.trial_start = time.time()

        await event_bus.publish("game.state_update", {
            "game_type": "social_skills",
            "round": self._round_num,
            "max_rounds": self.MAX_ROUNDS,
            "state": {"question": self.current_scenario["story"]},
            "prompt": "Say what you would do!",
        })
        self._mark_question_shown()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_scenario:
            logger.warning("[SocialSkillsGame] evaluate() called with no active scenario.")
            return GameResult(correct=False, score=0.0, response_time=0.0,
                              feedback="Let me think of a new story for you!")

        response_time = time.time() - self.trial_start
        response_lower = response.lower()
        correct = any(kw in response_lower for kw in self.current_scenario["keywords"])
        score = 1.0 if correct else 0.0

        if correct:
            feedback = "Great job! That is a very nice and polite thing to do."
            await self.trigger_success_video_once()
        else:
            hint = self.current_scenario["hint"]
            feedback = f"That's one idea! A good choice would be to {hint}."
            await self.trigger_failure_video_once()

        result = GameResult(
            correct=correct, score=score, response_time=response_time,
            feedback=feedback,
            data={"category": self.current_scenario["category"]},
            signals_complete=(self._round_num >= self.MAX_ROUNDS),
        )
        self.trials.append(result)
        self.current_scenario = None

        await event_bus.publish("social_skills.update", {"score": score})
        await event_bus.publish("game.scored", {"game_type": "social_skills", "score": score, "child_id": self.child_id})
        self._mark_feedback_shown()
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            return {"tokens_earned": 2, "animation": "stars"}
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
