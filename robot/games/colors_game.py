import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("colors")
class ColorsGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.colors = ["red", "blue", "green", "yellow", "orange", "purple"]
        self.objects = {
            "apple": "red", "sky": "blue", "grass": "green", 
            "sun": "yellow", "orange": "orange", "grape": "purple"
        }
        self.current_target = None
        self.start_time = None
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        return "Let's play the colors game! Are you ready?"

    async def _generate_trial(self) -> str:
        if self.difficulty == 1:
            # Simple 2 choices
            choices = random.sample(self.colors[:2], 2)
            self.current_target = random.choice(choices)
            prompt = f"Can you say the color {self.current_target}?"
            
        elif self.difficulty == 2:
            # 4 choices
            choices = random.sample(self.colors[:4], 4)
            self.current_target = random.choice(choices)
            prompt = f"Which color is this: {self.current_target}?"
            # In a real UI, we would emit an event to display the color
            
        elif self.difficulty >= 3:
            # Contextual
            obj, color = random.choice(list(self.objects.items()))
            self.current_target = color
            prompt = f"What color is a {obj}?"
            
        self.trial_start = time.time()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            prompt = await _generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        correct = self.current_target.lower() in response.lower()
        
        score = 1.0 if correct else 0.0
        feedback = "Great job!" if correct else f"Good try! The color was {self.current_target}."
        
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        
        # Prepare next trial
        self.current_target = None 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            await event_bus.publish("ui.animation.trigger", {"type": "star_burst"})
            return {"tokens_earned": 1, "animation": "star_burst"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = len(self.trials)
        time_spent = time.time() - self.start_time
        total_score = sum(t.score for t in self.trials)
        
        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty
        )
