import logging
import os
from typing import AsyncGenerator, List, Dict
from groq import AsyncGroq
import groq

from robot.llm.base_provider import LLMProvider, ProviderError, RateLimitError
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class GroqProvider(LLMProvider):
    def __init__(self):
        api_key = settings.llm.GROQ_API_KEY
        if not api_key:
            logger.warning("GROQ_API_KEY is not set. GroqProvider will fail on use.")
            
        self.client = AsyncGroq(api_key=api_key)
        self.model = settings.llm.PRIMARY_MODEL

    @property
    def name(self) -> str:
        return "groq"

    async def stream_chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        try:
            logger.debug(f"Sending stream request to Groq (model: {self.model})")
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except groq.RateLimitError as e:
            logger.warning(f"Groq rate limit exceeded: {e}")
            raise RateLimitError(str(e))
        except Exception as e:
            logger.error(f"Groq streaming error: {e}")
            raise ProviderError(str(e))

    async def chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> str:
        try:
            logger.debug(f"Sending chat request to Groq (model: {self.model})")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False
            )
            return response.choices[0].message.content
            
        except groq.RateLimitError as e:
            logger.warning(f"Groq rate limit exceeded: {e}")
            raise RateLimitError(str(e))
        except Exception as e:
            logger.error(f"Groq chat error: {e}")
            raise ProviderError(str(e))
