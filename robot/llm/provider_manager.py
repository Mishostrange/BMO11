import logging
from typing import AsyncGenerator, List, Dict
import time

from robot.llm.base_provider import LLMProvider, ProviderError, RateLimitError

logger = logging.getLogger(__name__)

class ProviderManager:
    def __init__(self, primary: LLMProvider, fallback: LLMProvider):
        self.primary = primary
        self.fallback = fallback
        
        # Track when primary hit a rate limit to allow cooldown
        self._primary_cooldown_until = 0
        self._cooldown_duration = 60  # seconds

    def _get_active_provider(self) -> LLMProvider:
        """Determine which provider to use based on cooldown state."""
        if time.time() < self._primary_cooldown_until:
            logger.debug(f"Primary provider on cooldown. Using fallback: {self.fallback.name}")
            return self.fallback
        return self.primary

    def _handle_primary_failure(self):
        """Put primary on cooldown."""
        self._primary_cooldown_until = time.time() + self._cooldown_duration
        logger.warning(f"Primary provider {self.primary.name} failed/rate-limited. Cooldown for {self._cooldown_duration}s.")

    async def stream_chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        
        provider = self._get_active_provider()
        
        try:
            async for chunk in provider.stream_chat(messages, max_tokens, temperature):
                yield chunk
        except RateLimitError:
            if provider == self.primary:
                self._handle_primary_failure()
                logger.info(f"Failing over to {self.fallback.name} for stream...")
                # Retry with fallback
                async for chunk in self.fallback.stream_chat(messages, max_tokens, temperature):
                    yield chunk
            else:
                # Both failed
                logger.error("All providers rate limited.")
                yield "I'm having a hard time thinking right now. Let's take a little break."
        except Exception as e:
            logger.error(f"Provider {provider.name} error: {e}")
            if provider == self.primary:
                self._handle_primary_failure()
                async for chunk in self.fallback.stream_chat(messages, max_tokens, temperature):
                    yield chunk
            else:
                yield "I'm feeling a bit confused. Can we try again?"

    async def chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> str:
        
        provider = self._get_active_provider()
        
        try:
            return await provider.chat(messages, max_tokens, temperature)
        except RateLimitError:
            if provider == self.primary:
                self._handle_primary_failure()
                logger.info(f"Failing over to {self.fallback.name} for chat...")
                return await self.fallback.chat(messages, max_tokens, temperature)
            else:
                logger.error("All providers rate limited.")
                return "I'm having a hard time thinking right now. Let's take a little break."
        except Exception as e:
            logger.error(f"Provider {provider.name} error: {e}")
            if provider == self.primary:
                self._handle_primary_failure()
                return await self.fallback.chat(messages, max_tokens, temperature)
            else:
                return "I'm feeling a bit confused. Can we try again?"
