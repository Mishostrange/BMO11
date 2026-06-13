import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("emotions")
class EmotionsGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.emotions = ["happy", "sad", "angry", "surprised", "scared"]
        self.situations = {
            "You got a new toy!": "happy",
            "You dropped your ice cream.": "sad",
            "Someone took your turn.": "angry",
            "A loud noise scared you.": "scared"
        }
        self.current_target = None
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        return "Let's play the feelings game! I will show you a face, and you tell me how I feel."

    async def _generate_trial(self) -> str:
        if self.difficulty <= 2:
            # Show expression and ask
            pool = self.emotions[:2] if self.difficulty == 1 else self.emotions[:4]
            self.current_target = random.choice(pool)
            
            # Emit event to change robot face
            await event_bus.publish("ui.expression.change", self.current_target)
            
            prompt = "Look at my face. How am I feeling?"
            
        else:
            # Contextual situations
            situation, emotion = random.choice(list(self.situations.items()))
            self.current_target = emotion
            await event_bus.publish("ui.expression.change", "thinking")
            prompt = f"Listen to this story: {situation} How would that make you feel?"
            
        self.trial_start = time.time()
        return prompt

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        correct = self.current_target in response.lower()
        
        score = 1.0 if correct else 0.0
        feedback = "Yes! That is exactly right!" if correct else f"Actually, I was feeling {self.current_target}."
        
        # Reset face to happy after evaluation
        await event_bus.publish("ui.expression.change", "happy")
        
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        self.current_target = None 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            await event_bus.publish("ui.animation.trigger", {"type": "confetti"})
            return {"tokens_earned": 2, "animation": "confetti"}
        return {"tokens_earned": 0}

    async def finish(self) -> GameSummary:
        correct_count = sum(1 for t in self.trials if t.correct)
        total_count = max(1, len(self.trials))
        time_spent = time.time() - self.start_time
        total_score = sum(t.score for t in self.trials)
        
        await event_bus.publish("ui.expression.change", "neutral")
        
        return GameSummary(
            total_score=total_score,
            correct_count=correct_count,
            total_count=total_count,
            time_spent=time_spent,
            difficulty_achieved=self.difficulty
        )
