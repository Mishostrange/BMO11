from typing import Dict, Type
from robot.games.base_game import BaseGame

class GameRegistry:
    """Registry for available therapy games."""
    
    _games: Dict[str, Type[BaseGame]] = {}

    @classmethod
    def register(cls, name: str):
        """Decorator to register a game class."""
        def wrapper(game_class: Type[BaseGame]):
            cls._games[name] = game_class
            return game_class
        return wrapper

    @classmethod
    def get_game(cls, name: str) -> BaseGame:
        """Instantiate a game by name."""
        if name not in cls._games:
            raise ValueError(f"Game '{name}' is not registered.")
        return cls._games[name]()
        
    @classmethod
    def list_games(cls) -> list:
        return list(cls._games.keys())
