import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("social_skills")
class SocialSkillsGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.scenarios = [
            {
                "category": "greeting",
                "story": "You walk into the classroom and see your teacher. What should you say?",
                "keywords": ["hi", "hello", "good morning", "morning", "greet"]
            },
            {
                "category": "sharing",
                "story": "You are playing with blocks and your friend asks if they can play too. What should you do?",
                "keywords": ["share", "yes", "give", "play together", "of course", "sure"]
            },
            {
                "category": "helping",
                "story": "If your friend drops his toy on the floor, what should you do?",
                "keywords": ["pick", "help", "give", "ask"]
            },
            {
                "category": "taking_turns",
                "story": "There is only one swing at the park, and someone else is using it. What should you do?",
                "keywords": ["wait", "turn", "ask", "patient", "line"]
            },
            {
                "category": "asking_for_help",
                "story": "You are trying to reach a book on a high shelf but you can't reach it. What should you do?",
                "keywords": ["ask", "help", "mom", "dad", "teacher", "adult", "please"]
            }
        ]
        self.current_scenario = None
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        await self._generate_trial()
        return "Let's practice being a good friend! I will tell you a story, and you tell me what you would do."

    async def _generate_trial(self) -> str:
        self.current_scenario = random.choice(self.scenarios)
        
        await event_bus.publish("ui.expression.change", "thinking")
        prompt = f"Listen carefully: {self.current_scenario['story']}"
            
        self.trial_start = time.time()
        await event_bus.publish("game.state_update", {
            "game_type": "social_skills",
            "state": {
                "question": self.current_scenario["story"],
                "options": [],
            },
            "prompt": "Say what you would do!",
        })
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_scenario:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        response_lower = response.lower()
        
        # Check if any keywords match the response
        correct = any(keyword in response_lower for keyword in self.current_scenario["keywords"])
        
        score = 1.0 if correct else 0.0
        
        if correct:
            feedback = "Great job! That is a very nice and polite thing to do."
            await event_bus.publish("ui.expression.change", "happy")
        else:
            feedback = f"That's one idea. But usually, it's very polite to {self.current_scenario['keywords'][0]}."
            await event_bus.publish("ui.expression.change", "neutral")
        
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        
        # Emit event for progress tracker
        await event_bus.publish("social_skills.update", {"score": score, "category": self.current_scenario["category"]})
        await event_bus.publish("game.scored", {"game_type": "social_skills", "score": score})
        
        self.current_scenario = None 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            await event_bus.publish("ui.animation.trigger", {"type": "stars"})
            return {"tokens_earned": 2, "animation": "stars"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - self.start_time
        total_score = sum(t.score for t in self.trials)
        
        await event_bus.publish("ui.expression.change", "happy")
        
        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty
        )
