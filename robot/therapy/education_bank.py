"""
robot/therapy/education_bank.py
──────────────────────────────────────────────────────────────────────────────
Educational question bank for BMO.

BMO naturally weaves simple educational questions into casual conversation —
covering colours, numbers, animals, shapes, feelings, and nature.

These are NOT forced as tasks. They are soft, curious prompts that BMO 
injects occasionally when the conversation is flowing well and the child 
seems engaged and positive.
"""

import random
import time

# Question bank — grouped by topic.
# Each entry: {"question": str, "topic": str, "age_min": int}
QUESTIONS = [
    # Colours
    {"question": "Hey, what colour is the sky on a sunny day?",            "topic": "colours",   "age_min": 3},
    {"question": "What colour are bananas?",                               "topic": "colours",   "age_min": 3},
    {"question": "Can you name three colours you can see right now?",      "topic": "colours",   "age_min": 4},
    {"question": "What colour do you get when you mix red and blue?",      "topic": "colours",   "age_min": 5},
    {"question": "What colour are leaves in autumn?",                      "topic": "colours",   "age_min": 5},

    # Numbers & counting
    {"question": "How many fingers do you have on one hand?",              "topic": "numbers",   "age_min": 3},
    {"question": "Can you count from one to ten for me?",                  "topic": "numbers",   "age_min": 3},
    {"question": "What number comes after seven?",                         "topic": "numbers",   "age_min": 4},
    {"question": "If you have two apples and eat one, how many are left?", "topic": "numbers",   "age_min": 5},
    {"question": "What is two plus two?",                                  "topic": "numbers",   "age_min": 5},

    # Animals
    {"question": "What sound does a dog make?",                            "topic": "animals",   "age_min": 3},
    {"question": "Which animal is the biggest — a cat, a dog, or an elephant?", "topic": "animals", "age_min": 4},
    {"question": "What do fish live in — water or trees?",                 "topic": "animals",   "age_min": 3},
    {"question": "Can you name an animal that can fly?",                   "topic": "animals",   "age_min": 4},
    {"question": "What do bees make?",                                     "topic": "animals",   "age_min": 4},
    {"question": "Which animal has a very long neck?",                     "topic": "animals",   "age_min": 4},

    # Shapes
    {"question": "What shape is a ball?",                                  "topic": "shapes",    "age_min": 3},
    {"question": "How many sides does a triangle have?",                   "topic": "shapes",    "age_min": 4},
    {"question": "What shape is a pizza?",                                 "topic": "shapes",    "age_min": 4},
    {"question": "Can you draw a square in the air with your finger?",     "topic": "shapes",    "age_min": 4},

    # Feelings
    {"question": "What makes you feel happy?",                             "topic": "feelings",  "age_min": 3},
    {"question": "When you feel sad, what helps you feel better?",         "topic": "feelings",  "age_min": 4},
    {"question": "What does it feel like to be excited?",                  "topic": "feelings",  "age_min": 4},
    {"question": "Can you show me what a surprised face looks like?",      "topic": "feelings",  "age_min": 3},

    # Nature & Science
    {"question": "What do plants need to grow?",                           "topic": "nature",    "age_min": 4},
    {"question": "Where does rain come from?",                             "topic": "nature",    "age_min": 5},
    {"question": "What do we call the big, bright star in our sky?",       "topic": "nature",    "age_min": 4},
    {"question": "What season comes after winter?",                        "topic": "nature",    "age_min": 5},
    {"question": "Can you name something that grows underground?",         "topic": "nature",    "age_min": 5},

    # Language & Letters
    {"question": "What letter does your name start with?",                 "topic": "language",  "age_min": 4},
    {"question": "Can you tell me a word that starts with the letter B?",  "topic": "language",  "age_min": 5},
    {"question": "What is the opposite of hot?",                           "topic": "language",  "age_min": 4},
    {"question": "What is the opposite of big?",                           "topic": "language",  "age_min": 4},

    # Social skills
    {"question": "What do you say when someone gives you something nice?", "topic": "social",    "age_min": 3},
    {"question": "How do you greet a new friend?",                         "topic": "social",    "age_min": 4},
    {"question": "What should you do if you want to use something someone else has?", "topic": "social", "age_min": 4},
]

# How often (seconds) BMO can ask an educational question
_MIN_INTERVAL_SECONDS = 120   # at most one question every 2 minutes
_last_asked_time: float = 0.0
_asked_indices: set = set()


def get_educational_question(child_age: int = 5, topic_preference: str = None) -> str:
    """
    Returns a randomly selected educational question appropriate for the child's age.
    Returns an empty string if it's too soon to ask another question (cooldown).

    Args:
        child_age:        Child's age for age filtering.
        topic_preference: Optional topic to prefer (e.g. "animals").
    """
    global _last_asked_time, _asked_indices

    now = time.time()
    if now - _last_asked_time < _MIN_INTERVAL_SECONDS:
        return ""

    if child_age is None:
        child_age = 5

    # Filter by age
    eligible = [q for q in QUESTIONS if q["age_min"] <= child_age]
    if not eligible:
        eligible = QUESTIONS  # fallback: use all

    # Prefer topic if given
    if topic_preference:
        preferred = [q for q in eligible if q["topic"] == topic_preference]
        if preferred:
            eligible = preferred

    # Avoid repeating recently asked questions
    fresh = [q for i, q in enumerate(eligible) if i not in _asked_indices]
    if not fresh:
        _asked_indices.clear()  # reset when all have been asked
        fresh = eligible

    chosen = random.choice(fresh)
    idx = QUESTIONS.index(chosen) if chosen in QUESTIONS else -1
    if idx >= 0:
        _asked_indices.add(idx)

    _last_asked_time = now
    return chosen["question"]


def reset_cooldown():
    """Reset the question cooldown (useful for testing)."""
    global _last_asked_time
    _last_asked_time = 0.0
