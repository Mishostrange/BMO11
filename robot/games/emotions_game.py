import random
import time
from robot.games.base_game import BaseGame, GameResult, GameSummary
from robot.games.game_registry import GameRegistry
from robot.services.event_bus import event_bus

@GameRegistry.register("emotions")
class EmotionsGame(BaseGame):
    """
    Emotion Recognition Game.
    Easy: Robot shows an emotion face, child identifies it from 2 options.
    Medium: Robot shows an emotion face, child identifies it from 4 options.
    Hard: Robot describes a situation, child identifies the resulting emotion.
    """
    def __init__(self):
        super().__init__()
        self.emotions = ["happy", "sad", "angry", "scared"]
        self.situations = {
            "You got a brand new toy for your birthday!": "happy",
            "You dropped your favorite ice cream on the floor.": "sad",
            "Someone pushed you and took your turn on the swing.": "angry",
            "A loud thunder noise woke you up in the dark.": "scared",
            "Your best friend came over to play!": "happy",
            "You lost your favorite teddy bear.": "sad"
        }
        self.current_target = None
        self.trial_start = None

    async def start(self, child_id: int, difficulty: int) -> str:
        self.child_id = child_id
        # Map system difficulty 1-5 to Easy(1), Medium(2), Hard(3)
        self.difficulty = min(3, max(1, difficulty))
        self.start_time = time.time()
        self.trials = []
        # Generate the first face/scenario immediately
        await self._generate_trial()
        return "Let's play the feelings game! I will show you a face, and you tell me how I feel."

    async def _generate_trial(self) -> str:
        if self.difficulty == 1:
            # Easy: 2 options
            pool = random.sample(self.emotions, 2)
            self.current_target = random.choice(pool)
            self._current_options = pool
            await event_bus.publish("ui.expression.change", self.current_target)
            options_text = " or ".join(pool)
            prompt = f"Look at my face. Do you think I feel {options_text}?"
            
        elif self.difficulty == 2:
            # Medium: 4 options
            pool = list(self.emotions)
            random.shuffle(pool)
            self.current_target = random.choice(pool)
            self._current_options = pool
            await event_bus.publish("ui.expression.change", self.current_target)
            options_text = ", ".join(pool[:-1]) + f", or {pool[-1]}"
            prompt = f"Look at my face. Which face is this? Is it {options_text}?"
            
        else:
            # Hard: Situational
            situation, emotion = random.choice(list(self.situations.items()))
            self.current_target = emotion
            self._current_options = list(self.emotions)
            await event_bus.publish("ui.expression.change", "thinking")
            prompt = f"Listen to this story: {situation} How do you think that makes me feel?"
            
        self.trial_start = time.time()
        await self._publish_state(prompt)
        return prompt

    async def _publish_state(self, prompt: str = ""):
        await event_bus.publish("game.state_update", {
            "game_type": "emotions",
            "state": {
                "target":  self.current_target,
                "options": getattr(self, "_current_options", []),
            },
            "prompt": prompt or "Say the emotion you see!",
            "score_text": f"Score: {sum(1 for t in self.trials if t.correct)}/{len(self.trials)}",
        })

    async def evaluate(self, response: str) -> GameResult:
        if not self.current_target:
            prompt = await self._generate_trial()
            return GameResult(correct=False, score=0, response_time=0, feedback=prompt)

        response_time = time.time() - self.trial_start
        correct = self.current_target in response.lower()
        
        score = 1.0 if correct else 0.0
        
        if correct:
            feedback = "Yes! That is exactly right! You are great at reading feelings."
            await event_bus.publish("ui.expression.change", "happy")
        else:
            feedback = f"Good try! Actually, I was feeling {self.current_target}."
            await event_bus.publish("ui.expression.change", "neutral")
        
        result = GameResult(correct=correct, score=score, response_time=response_time, feedback=feedback)
        self.trials.append(result)
        
        await event_bus.publish("game.scored", {"game_type": "emotions", "score": score, "child_id": self.child_id})
        
        self.current_target = None 
        return result

    async def reward(self, result: GameResult) -> dict:
        if result.correct:
            tokens = self.difficulty
            await event_bus.publish("ui.animation.trigger", {"type": "confetti"})
            return {"tokens_earned": tokens, "animation": "confetti"}
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
