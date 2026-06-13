import asyncio
import logging
import numpy as np
from faster_whisper import WhisperModel
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class FasterWhisperNode:
    def __init__(self, model_size="tiny.en"):
        logger.info(f"Loading Faster Whisper model: {model_size}")
        # Use CPU with int8 quantization for best RPi5 performance
        self.model = WhisperModel(
            model_size, 
            device="cpu", 
            compute_type="int8"
        )
        
        # Subscribe to VAD events
        event_bus.subscribe('speech.ended', self._on_speech_ended)
        
        # Warmup the model with dummy audio to prevent slow first inference
        self._warmup()

    def _warmup(self):
        """Run a dummy transcription to load model into memory."""
        logger.debug("Warming up Whisper model...")
        dummy_audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        try:
            self.model.transcribe(dummy_audio, beam_size=1)
            logger.info("Whisper model warmup complete")
        except Exception as e:
            logger.error(f"Whisper warmup failed: {e}")

    async def _on_speech_ended(self, event_type: str, audio_data: np.ndarray):
        """Event handler for when VAD detects end of speech."""
        logger.debug("STT Node received speech buffer. Transcribing...")
        
        try:
            # Run transcription in a separate thread so it doesn't block the async loop
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None, 
                self._transcribe_sync, 
                audio_data
            )
            
            if text and text.strip():
                logger.info(f"User: {text}")
                await event_bus.publish('speech.transcribed', text)
            else:
                logger.debug("STT: No speech detected in buffer")
                
        except Exception as e:
            logger.error(f"STT transcription failed: {e}", exc_info=True)

    def _transcribe_sync(self, audio_data: np.ndarray) -> str:
        """Synchronous transcription function (runs in executor)."""
        # We optimize for latency:
        # - beam_size=1 is much faster than default 5
        # - best_of=1 disables multiple candidate generations
        # - vad_filter=False because we already did VAD externally
        # - without_timestamps=True saves generation time
        
        segments, _ = self.model.transcribe(
            audio_data,
            beam_size=1,
            best_of=1,
            language="en",
            vad_filter=False,
            without_timestamps=True,
            condition_on_previous_text=False  # Crucial for preventing hallucination loops
        )
        
        return " ".join(seg.text for seg in segments).strip()
