import asyncio
import logging
import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator
from robot.config.settings import settings
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class SileroVADNode:
    def __init__(self):
        self.sample_rate = settings.audio.SAMPLE_RATE
        self.threshold = settings.vad.THRESHOLD
        
        logger.info("Loading Silero VAD model (ONNX)...")
        # Load the lightweight ONNX model
        self.model = load_silero_vad(onnx=True)
        
        self.vad_iterator = VADIterator(
            self.model,
            threshold=self.threshold,
            sampling_rate=self.sample_rate,
            min_silence_duration_ms=settings.vad.MIN_SILENCE_DURATION_MS,
            speech_pad_ms=settings.vad.SPEECH_PAD_MS
        )
        
        self._speech_buffer = []
        self._is_speaking = False
        
    async def process_chunk(self, audio_chunk: np.ndarray):
        """
        Process a single 512-sample chunk of audio.
        Requires float32 audio in range [-1.0, 1.0].
        """
        # Ensure correct shape and type
        audio_tensor = torch.from_numpy(audio_chunk.flatten())
        
        # Determine speech state
        speech_dict = self.vad_iterator(audio_tensor, return_seconds=False)
        
        # State transitions
        if speech_dict is not None:
            if 'start' in speech_dict:
                # Speech just started
                self._is_speaking = True
                self._speech_buffer = [audio_chunk.copy()]
                logger.debug("VAD: Speech started")
                await event_bus.publish('speech.started')
                
            elif 'end' in speech_dict:
                # Speech just ended
                self._is_speaking = False
                if self._speech_buffer:
                    self._speech_buffer.append(audio_chunk.copy())
                    
                    # Combine all buffered chunks
                    full_speech = np.concatenate(self._speech_buffer)
                    logger.debug(f"VAD: Speech ended. Buffered {len(full_speech)/self.sample_rate:.2f}s")
                    
                    # Publish the collected audio
                    await event_bus.publish('speech.ended', full_speech)
                    self._speech_buffer = []
        else:
            # Continuing current state
            if self._is_speaking:
                self._speech_buffer.append(audio_chunk.copy())
                
                # Safety check: don't buffer more than 30 seconds to avoid OOM
                max_chunks = (30 * self.sample_rate) // len(audio_chunk)
                if len(self._speech_buffer) > max_chunks:
                    logger.warning("VAD: Speech buffer exceeded 30s. Forcing end.")
                    full_speech = np.concatenate(self._speech_buffer)
                    await event_bus.publish('speech.ended', full_speech)
                    self._speech_buffer = []
                    self._is_speaking = False
                    self.vad_iterator.reset_states()

    def reset(self):
        """Reset VAD state machine."""
        self.vad_iterator.reset_states()
        self._speech_buffer = []
        self._is_speaking = False
