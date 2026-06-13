import random
import time
import difflib
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("speech")
class SpeechRepeatGame(BaseGame):
    def __init__(self):
        super().__init__()
        self.levels = {
            1: ["cat", "dog", "ball", "sun", "car"],
            2: ["red apple", "big dog", "fast car"],
            3: ["I like to play", "The sun is hot"],
            4: ["She sells seashells by the seashore"]
        }
        self.current_target = None
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        self.difficulty = difficulty
        self.start_time = time.time()
        self.trials = []
        return "Let's play the parrot game! I will say something, and you say it back to me."

    async def _generate_trial(self) -> str:
        level_key = min(self.difficulty, max(self.levels.keys()))
        pool = self.levels[level_key]
        self.current_target = random.choice(pool)
        
        self.trial_start = time.time()
        return f"Can you say: {self.current_target}?"

    def _word_similarity(self, s1: str, s2: str) -> float:
        """Levenshtein-based similarity ratio."""
        return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        
        # Calculate similarity score
        similarity = self._word_similarity(self.current_target, response)
        
        # Determine success threshold based on difficulty
        # Lower difficulty = more forgiving
        threshold = 0.6 if self.difficulty == 1 else 0.8
        
        correct = similarity >= threshold
        
        if correct:
            feedback = "Excellent speaking!"
        else:
            feedback = f"You tried very hard! Listen again: {self.current_target}"
            
        result = GameResult(
            correct=correct, 
            score=similarity, 
            response_time=response_time, 
            feedback=feedback,
            data={"target": self.current_target, "recognized": response, "similarity": similarity}
        )
        self.trials.append(result)
        self.current_target = None 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            await event_bus.publish("ui.animation.trigger", {"type": "star_burst"})
            tokens = 2 if self.difficulty > 2 else 1
            return {"tokens_earned": tokens, "animation": "star_burst"}
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
