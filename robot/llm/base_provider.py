from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Any

class ProviderError(Exception):
    """Base exception for LLM provider errors."""
    pass

class RateLimitError(ProviderError):
    """Raised when a provider's rate limit is exceeded."""
    pass

class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider (e.g., 'groq', 'gemini')."""
        pass

    @abstractmethod
    async def stream_chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat responses chunk by chunk.
        Raises RateLimitError if quota exceeded.
        """
        pass
        
    @abstractmethod
    async def chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> str:
        """
        Generate a complete chat response.
        Raises RateLimitError if quota exceeded.
        """
        pass
