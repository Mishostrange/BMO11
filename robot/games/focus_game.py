import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("focus")
class FocusGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.items = ["apple", "ball", "cat", "dog", "elephant", "fish", "grape", "hat"]
        self.current_sequence = []
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        return "Let's play the memory game! Put your thinking cap on. I will say some words, and you repeat them back to me in order."

    async def _generate_trial(self) -> str:
        # Difficulty determines sequence length
        seq_length = min(self.difficulty + 1, 5) # level 1 = 2 items, level 4 = 5 items
        self.current_sequence = random.sample(self.items, seq_length)
        
        items_str = ", ".join(self.current_sequence)
        prompt = f"Listen carefully: {items_str}. Now you say them!"
        
        self.trial_start = time.time()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_sequence:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        response_lower = response.lower()
        
        # Check if all items are present and in the correct order
        # For a simple implementation, we just check if the words exist in the response
        # A more strict implementation would enforce exact ordering
        
        correct_items = 0
        for item in self.current_sequence:
            if item in response_lower:
                correct_items += 1
                
        score = correct_items / len(self.current_sequence)
        correct = score == 1.0
        
        if correct:
            feedback = "Amazing memory! You got them all."
        elif score >= 0.5:
            feedback = f"Good job! You remembered most of them. The words were: {', '.join(self.current_sequence)}."
        else:
            feedback = f"Nice try! It's tricky. The words were: {', '.join(self.current_sequence)}."
            
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        self.current_sequence = [] 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            await event_bus.publish("ui.animation.trigger", {"type": "star_burst"})
            return {"tokens_earned": self.difficulty, "animation": "star_burst"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - self.start_time
        total_score = sum(t.score for t in self.trials)
        
        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty
        )
