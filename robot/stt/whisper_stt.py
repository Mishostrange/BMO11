import asyncio
import logging
import numpy as np
from faster_whisper import WhisperModel
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class FasterWhisperNode:
    def __init__(self, model_size="base"):
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

    # Known Whisper hallucination phrases to discard (substring match)
    HALLUCINATION_PATTERNS = [
        "thanks for watching", "thanks for listening",
        "please subscribe", "subtitles by",
        "[music]", "[applause]", "[laughter]",
        "amara.org",
    ]
    
    # Exact match hallucinations (prevent substring bugs like "me" in "game")
    EXACT_HALLUCINATIONS = {
        "me", "you", "yeah", "oh", "ah", "um", 
        "thank you", "let's play the memory", 
        "here is the game rule", "which game do you want"
    }
    MIN_AUDIO_DURATION_S = 0.8   # ignore clips shorter than 0.8s
    MIN_RMS_ENERGY = 0.005        # ignore clips that are too quiet (background noise floor)

    async def _on_speech_ended(self, event_type: str, audio_data: np.ndarray):
        """Event handler for when VAD detects end of speech."""
        # --- Gate 1: minimum duration ---
        duration_s = len(audio_data.flatten()) / 16000
        if duration_s < self.MIN_AUDIO_DURATION_S:
            logger.debug(f"STT: Skipping short clip ({duration_s:.2f}s < {self.MIN_AUDIO_DURATION_S}s)")
            return
        
        # --- Gate 2: RMS energy check (drop background noise) ---
        rms = float(np.sqrt(np.mean(audio_data.flatten() ** 2)))
        if rms < self.MIN_RMS_ENERGY:
            logger.info(f"STT: Skipping silent clip (RMS={rms:.4f} < {self.MIN_RMS_ENERGY}) — background noise")
            return
        logger.info(f"STT: Received audio clip (duration={duration_s:.2f}s, RMS={rms:.4f}) — sending to Whisper")

        logger.debug("STT Node received speech buffer. Transcribing...")
        
        try:
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(
                None,
                self._transcribe_sync,
                audio_data
            )

            if text and text.strip():
                # --- Gate 2: hallucination filter ---
                # Remove punctuation for matching
                import string
                clean_text = text.lower().translate(str.maketrans('', '', string.punctuation)).strip()
                
                # 1. Exact match check
                if clean_text in self.EXACT_HALLUCINATIONS:
                    logger.debug(f"STT: Discarded exact hallucination: '{text}'")
                    return
                
                # 2. Substring match check
                if any(h in clean_text for h in self.HALLUCINATION_PATTERNS):
                    logger.debug(f"STT: Discarded substring hallucination: '{text}'")
                    return

                logger.info(f"User: {text}")
                await event_bus.publish('speech.transcribed', text)
            else:
                logger.info("STT: Empty transcription — Whisper returned no text for this audio clip")
                
        except Exception as e:
            logger.error(f"STT transcription failed: {e}", exc_info=True)

    def _transcribe_sync(self, audio_data: np.ndarray) -> str:
        """Synchronous transcription function (runs in executor)."""
        # We optimize for latency:
        # - beam_size=1 is much faster than default 5
        # - best_of=1 disables multiple candidate generations
        # - vad_filter=False because we already did VAD externally
        # Ensure audio is a 1D float32 array
        if len(audio_data.shape) > 1:
            audio_data = audio_data.flatten()
        audio_data = audio_data.astype(np.float32)
        
        segments, info = self.model.transcribe(
            audio_data,
            beam_size=1,
            best_of=1,
            vad_filter=False,
            without_timestamps=True,
            condition_on_previous_text=False
        )
        logger.info(
            f"Detected language: {info.language} "
            f"({info.language_probability:.2f})"
        )
        return " ".join(seg.text for seg in segments).strip()