import asyncio
import logging
import os
from typing import AsyncGenerator
import numpy as np
from kokoro_onnx import Kokoro
from robot.config.settings import BASE_DIR
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class KokoroTTSNode:
    def __init__(self, playback_node=None):
        self.playback_node = playback_node
        self.sample_rate = 24000
        
        models_dir = BASE_DIR / "models"
        bmo_model_path = models_dir / "bmo.onnx"
        default_model_path = models_dir / "kokoro-v1.0.onnx"
        voices_path = models_dir / "voices-v1.0.bin"
        
        # Load custom model if exists, else fallback
        try:
            if bmo_model_path.exists():
                logger.info(f"Loading custom TTS model: {bmo_model_path}")
                self.kokoro = Kokoro(str(bmo_model_path), str(voices_path))
                # Custom model should have its voice name mapped in voices.bin or use default
                self.voice_name = "af_heart"  # Need to map to correct voice key
            elif default_model_path.exists():
                logger.info(f"Loading default TTS model: {default_model_path}")
                self.kokoro = Kokoro(str(default_model_path), str(voices_path))
                self.voice_name = "af_heart" # Default female, change if needed
            else:
                logger.warning("TTS models not found! TTS will be disabled. See models/README.md")
                self.kokoro = None
        except Exception as e:
            logger.error(f"Failed to load Kokoro TTS: {e}")
            self.kokoro = None

        event_bus.subscribe('tts.synthesize', self._on_synthesize_request)

    async def _on_synthesize_request(self, event_type: str, text: str):
        """Handle request to speak text."""
        if not self.kokoro:
            logger.warning("Cannot synthesize: TTS model not loaded.")
            return
            
        logger.debug(f"Synthesizing: {text}")
        await self.synthesize_and_play(text)

    async def synthesize_stream(self, text: str) -> AsyncGenerator[np.ndarray, None]:
        """Stream chunks of synthesized audio from text."""
        if not self.kokoro:
            return
            
        try:
            # create_stream returns a synchronous generator
            stream = self.kokoro.create_stream(
                text,
                voice=self.voice_name,
                speed=1.0,
                lang="en-us"
            )
            
            loop = asyncio.get_running_loop()
            while True:
                try:
                    # Run the synchronous generator step in a thread
                    samples, sample_rate = await loop.run_in_executor(None, next, stream)
                    yield samples
                except StopIteration:
                    break
                
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")

    async def synthesize_and_play(self, text: str):
        """Synthesizes text and pushes chunks directly to the playback queue."""
        if not self.playback_node:
            logger.error("Playback node not attached to TTS node")
            return
            
        # UI state update
        await event_bus.publish('ui.expression.change', 'speaking')
        
        async for audio_chunk in self.synthesize_stream(text):
            self.playback_node.enqueue_chunk(audio_chunk)
            
        # Give a small delay for UI to revert back to neutral or waiting state
        # In a real app, the playback node should emit a 'playback.finished' event
        # but for simplicity we rely on the therapy engine to control states
