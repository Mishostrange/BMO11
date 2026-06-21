import time
import asyncio
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("sensory_regulation")
class SensoryRegulationGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.completed = False

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self.completed = False
        
        # Trigger the breathing animation on the UI
        await event_bus.publish("ui.animation.trigger", {"type": "breathe"})
        await event_bus.publish("ui.expression.change", "calm")
        
        return "I can see you might be feeling overwhelmed. Let's take a deep breath together. Breathe in... and breathe out... How are you feeling now?"

    async def evaluate(self, response: str) -> GameResult:
        response_time = time.time() - self.start_time
        response_lower = response.lower()
        
        # If they say they are feeling better, good, okay, calm
        better = any(word in response_lower for word in ["better", "good", "okay", "ok", "calm", "happy"])
        
        if better:
            feedback = "I'm so glad you're feeling better. Remember, you can always take deep breaths when things get too big."
            self.completed = True
        else:
            feedback = "It's okay to feel that way. I am right here with you. Let's try taking one more big breath."
            # They need another round, so we don't set completed
            self.completed = False
            
        result = GameResult(correct=better, score=1.0 if better else 0.5, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        
        if better:
            await event_bus.publish("ui.expression.change", "happy")
            
        return result

    async def reward(self, result: GameResult) -> dict:
        return {"tokens_earned": 0} # No tokens for feeling overwhelmed, just comfort

    async def finish(self) -> GameSummary:
        time_spent = time.time() - self.start_time
        
        return GameSummary(
            total_score=sum(t.score for t in self.trials),
            correct_count=1 if self.completed else 0,
            total_count=len(self.trials),
            time_spent=time_spent,
            difficulty_achieved=self.difficulty
        )
