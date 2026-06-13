import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("turn_taking")
class TurnTakingGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.starters = [
            "Once upon a time, there was a little dog who loved to...",
            "If I went to the moon, I would bring...",
            "My favorite thing to eat for breakfast is...",
            "When I go to the park, I like to play on the..."
        ]
        self.trial_start = None
        self.turn_count = 0

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        self.turn_count = 0
        return "Let's build a story together! I'll start, and then it's your turn."

    async def _generate_trial(self) -> str:
        self.turn_count += 1
        prompt = random.choice(self.starters)
        self.trial_start = time.time()
        return f"My turn: {prompt} Now your turn!"

    async def evaluate(self, response: str) -> GameResult:
        if self.turn_count == 0:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        
        # Turn taking evaluation is mostly about participation and waiting for the prompt
        # We assume if we got a response that isn't empty, they took their turn.
        # In a more advanced implementation, we'd check if they interrupted (using VAD timings).
        
        word_count = len(response.split())
        correct = word_count > 0
        
        if not correct:
            feedback = "I didn't hear you. Let's try your turn again."
            score = 0.0
        else:
            if self.turn_count < 3: # Keep the game going for a few turns
                feedback = "Great addition! Now it's my turn again. " + random.choice(self.starters) + " Your turn!"
                self.trial_start = time.time() # Reset timer for next turn immediately
            else:
                feedback = "That was a wonderful story we built together!"
            score = 1.0
            
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        
        if correct and self.turn_count >= 3:
            self.turn_count = 0 # End of current game loop
            
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct and self.turn_count == 0: # Only reward at end of full story loop
            await event_bus.publish("ui.animation.trigger", {"type": "confetti"})
            return {"tokens_earned": 2, "animation": "confetti"}
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
