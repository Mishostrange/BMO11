import asyncio
import logging
import numpy as np
from piper.voice import PiperVoice
from robot.config.settings import BASE_DIR
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class PiperTTSNode:
    def __init__(self, playback_node=None, capture_node=None):
        self.playback_node = playback_node
        self.capture_node  = capture_node   # AudioCapture — muted while BMO speaks
        self.voice         = None
        self._sample_rate  = 22050          # updated after model load

        models_dir      = BASE_DIR / "models"
        bmo_model_path  = models_dir / "bmo.onnx"
        bmo_config_path = models_dir / "bmo.onnx.json"

        try:
            if bmo_model_path.exists():
                if bmo_config_path.exists():
                    logger.info(f"Loading Piper TTS model: {bmo_model_path}")
                    self.voice = PiperVoice.load(str(bmo_model_path), str(bmo_config_path))
                    self._sample_rate = self.voice.config.sample_rate
                    logger.info(f"Piper sample rate: {self._sample_rate} Hz")

                    # Restart the playback stream at the model's native sample rate
                    if self.playback_node:
                        self.playback_node.reopen(self._sample_rate)
                else:
                    logger.error(
                        f"Missing config: place {bmo_config_path.name} next to bmo.onnx"
                    )
            else:
                logger.warning(f"Piper model not found at {bmo_model_path}.")
        except Exception as e:
            logger.error(f"Failed to load Piper TTS: {e}")

        event_bus.subscribe("tts.synthesize", self._on_synthesize_request)

    # ── Public API ────────────────────────────────────────────────────────────

    async def _on_synthesize_request(self, event_type: str, text: str):
        if not self.voice:
            logger.warning("Cannot synthesize: Piper TTS model not loaded.")
            return
        await self.synthesize_and_play(text)

    async def synthesize_and_play(self, text: str):
        """
        1. Mute mic immediately.
        2. Pre-synthesize ALL audio into a single buffer in a thread
           (no streaming — eliminates underflow/glitch gaps).
        3. Send the single buffer to playback all at once.
        4. Wait for playback queue to drain.
        5. Extra silence tail, then unmute mic.
        """
        if not self.playback_node:
            logger.error("No playback node attached to PiperTTSNode.")
            return

        # ── 1. Mute mic BEFORE any audio reaches the speaker ─────────────────
        if self.capture_node:
            self.capture_node.mute()

        await event_bus.publish("ui.expression.change", "speaking")

        try:
            loop = asyncio.get_running_loop()

            # ── 2. Pre-synthesize into a single float32 buffer ────────────────
            def _synthesize_all() -> np.ndarray:
                chunks = []
                for chunk in self.voice.synthesize(text):
                    arr = chunk.audio_float_array
                    if len(arr.shape) > 1:
                        arr = arr.flatten()
                    chunks.append(arr.astype(np.float32))
                if chunks:
                    out = np.concatenate(chunks)
                    # Normalize and clip to prevent digital audio clipping (glitches)
                    max_val = np.max(np.abs(out))
                    if max_val > 0.95:
                        out = (out / max_val) * 0.95
                    return np.clip(out, -1.0, 1.0)
                return np.array([], dtype=np.float32)

            audio_buf = await loop.run_in_executor(None, _synthesize_all)

            if len(audio_buf) == 0:
                logger.warning("Piper returned empty audio.")
                return

            # ── 3. Enqueue the complete audio in one shot ─────────────────────
            self.playback_node.enqueue_chunk(audio_buf)

            # ── 4. Wait for the playback queue to fully drain ─────────────────
            # Duration of the audio + 300 ms safety margin to account for PyAudio startup latency
            duration_s = len(audio_buf) / self._sample_rate
            await asyncio.sleep(duration_s + 0.3)

            # ── 5. Extra tail silence so speaker reverb settles ───────────────
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Piper TTS synthesis error: {e}")
        finally:
            # Always unmute, even on error
            if self.capture_node:
                self.capture_node.unmute()
            await event_bus.publish("ui.expression.change", "neutral")
