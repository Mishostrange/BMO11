import asyncio
import logging
import queue
import numpy as np
import sounddevice as sd
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class AudioCapture:
    def __init__(self):
        self.sample_rate = settings.audio.SAMPLE_RATE
        self.channels = settings.audio.CHANNELS
        self.chunk_size = settings.audio.CHUNK_SIZE
        self.dtype = settings.audio.DTYPE
        
        self._audio_queue = queue.Queue()
        self._stream = None
        self._is_running = False
        self._muted = False   # True while BMO is speaking (prevents echo feedback)

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice from a high-priority C thread."""
        if status:
            if status.input_overflow:
                pass  # Ignore harmless overflow warnings to reduce log spam
            else:
                logger.debug(f"Audio capture status: {status}")
        
        # Make a copy since indata buffer is reused by sounddevice
        if self._is_running and not self._muted:
            self._audio_queue.put(indata.copy())

    def start(self):
        """Start the audio capture stream."""
        if self._is_running:
            return

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.chunk_size,
                callback=self._audio_callback
            )
            self._stream.start()
            self._is_running = True
            logger.info(f"Started audio capture (SR: {self.sample_rate}, Chunk: {self.chunk_size})")
        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            raise

    def stop(self):
        """Stop the audio capture stream."""
        self._is_running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("Stopped audio capture")

    def mute(self):
        """Mute microphone input (called when BMO starts speaking)."""
        self._muted = True
        # Drain any leftover chunks from the queue to prevent stale audio
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def unmute(self):
        """Unmute microphone input (called when BMO finishes speaking)."""
        # Drain any leftover chunks from the queue to prevent stale audio/reverb
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        self._muted = False

    async def get_chunk_async(self) -> np.ndarray:
        """Async generator that yields audio chunks as they arrive."""
        loop = asyncio.get_running_loop()
        while self._is_running:
            try:
                # Use run_in_executor to not block the event loop on queue.get
                chunk = await loop.run_in_executor(None, self._audio_queue.get, True, 0.1)
                yield chunk
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            except Exception as e:
                logger.error(f"Error reading from audio queue: {e}")
                break
