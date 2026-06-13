import logging
import os
from typing import AsyncGenerator, List, Dict
from google import genai
from google.genai.errors import APIError

from robot.llm.base_provider import LLMProvider, ProviderError, RateLimitError
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class GeminiProvider(LLMProvider):
    def __init__(self):
        api_key = settings.llm.GEMINI_API_KEY
        if not api_key:
            logger.warning("GEMINI_API_KEY is not set. GeminiProvider will fail on use.")
            
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.model = settings.llm.FALLBACK_MODEL

    @property
    def name(self) -> str:
        return "gemini"

    def _convert_messages(self, messages: List[Dict[str, str]]) -> str:
        """
        Convert OpenAI-style message list to a format suitable for Gemini.
        Gemini's generate_content takes a string or parts. We'll combine system and user prompts.
        """
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt += f"System Instructions: {content}\n\n"
            elif role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        return prompt.strip()

    async def stream_chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        if not self.client:
            raise ProviderError("Gemini client not initialized (missing API key)")
            
        try:
            prompt = self._convert_messages(messages)
            logger.debug(f"Sending stream request to Gemini (model: {self.model})")
            
            # Gemini Python SDK doesn't natively support full Async yet in the new sdk for stream
            # but we can wrap it or assume the sync stream is fast enough. 
            # For this implementation, we'll iterate over the synchronous generator.
            # In a fully productionized version, this should be run in an executor if it blocks.
            response = self.client.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens
                )
            )
            
            for chunk in response:
                if chunk.text:
                    yield chunk.text
                    # Small sleep to yield to event loop since we are wrapping sync iter
                    import asyncio
                    await asyncio.sleep(0)
                    
        except APIError as e:
            if "429" in str(e) or "quota" in str(e).lower():
                logger.warning(f"Gemini rate limit exceeded: {e}")
                raise RateLimitError(str(e))
            logger.error(f"Gemini API error: {e}")
            raise ProviderError(str(e))
        except Exception as e:
            logger.error(f"Gemini streaming error: {e}")
            raise ProviderError(str(e))

    async def chat(
        self, 
        messages: List[Dict[str, str]], 
        max_tokens: int = 150,
        temperature: float = 0.7
    ) -> str:
        if not self.client:
            raise ProviderError("Gemini client not initialized (missing API key)")
            
        try:
            prompt = self._convert_messages(messages)
            logger.debug(f"Sending chat request to Gemini (model: {self.model})")
            
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens
                )
            )
            return response.text
            
        except APIError as e:
            if "429" in str(e) or "quota" in str(e).lower():
                logger.warning(f"Gemini rate limit exceeded: {e}")
                raise RateLimitError(str(e))
            logger.error(f"Gemini API error: {e}")
            raise ProviderError(str(e))
        except Exception as e:
            logger.error(f"Gemini chat error: {e}")
            raise ProviderError(str(e))
