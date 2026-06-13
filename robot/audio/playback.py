import asyncio
import logging
import queue
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

class AudioPlayback:
    def __init__(self, sample_rate: int = 24000):
        # Default Kokoro sample rate is 24000
        self.sample_rate = sample_rate
        self.channels = 1
        
        self._playback_queue = queue.Queue()
        self._buffer = np.array([], dtype='float32')
        self._stream = None
        self._is_playing = False
        self._cancel_flag = False

    def _playback_callback(self, outdata, frames, time_info, status):
        """Called by sounddevice when it needs more audio data."""
        if status:
            logger.warning(f"Audio playback status: {status}")
            
        if self._cancel_flag:
            outdata.fill(0)
            raise sd.CallbackStop()

        try:
            # Fill the internal buffer if we don't have enough frames
            while len(self._buffer) < frames:
                try:
                    chunk = self._playback_queue.get_nowait()
                    if len(chunk.shape) == 2:
                        chunk = chunk.flatten()
                    self._buffer = np.concatenate((self._buffer, chunk))
                except queue.Empty:
                    break

            if len(self._buffer) == 0:
                outdata.fill(0)
            elif len(self._buffer) >= frames:
                outdata[:] = self._buffer[:frames].reshape(-1, 1)
                self._buffer = self._buffer[frames:]
            else:
                outdata[:len(self._buffer)] = self._buffer.reshape(-1, 1)
                outdata[len(self._buffer):].fill(0)
                self._buffer = np.array([], dtype='float32')

        except Exception as e:
            logger.error(f"Playback callback error: {e}")
            outdata.fill(0)

    def play_stream(self):
        """Start the background playback stream."""
        if self._is_playing:
            return

        self._cancel_flag = False
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='float32',
                callback=self._playback_callback
            )
            self._stream.start()
            self._is_playing = True
            logger.info(f"Started audio playback stream (SR: {self.sample_rate})")
        except Exception as e:
            logger.error(f"Failed to start audio playback: {e}")

    def stop_stream(self):
        """Stop the background playback stream."""
        self._is_playing = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            
        # Clear queue
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break

    def enqueue_chunk(self, audio_data: np.ndarray):
        """Add audio data to the playback queue."""
        if not self._is_playing:
            self.play_stream()
        
        # Ensure correct shape
        if len(audio_data.shape) == 1:
            audio_data = audio_data.reshape(-1, 1)
            
        self._playback_queue.put(audio_data)

    def reopen(self, sample_rate: int):
        """Stop current stream and reopen at a new sample rate. Called after TTS model loads."""
        logger.info(f"Reopening playback stream at {sample_rate} Hz")
        self.stop_stream()
        self.sample_rate = sample_rate
        self._buffer = np.array([], dtype='float32')
        self.play_stream()

    def cancel_playback(self):
        """Barge-in: immediately stop current playback."""
        logger.info("Canceling audio playback (barge-in)")
        self._cancel_flag = True
        
        # Clear queue
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
                
        # The callback will see _cancel_flag and raise CallbackStop
        # We need to restart the stream for the next utterance
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._is_playing = False
        self._cancel_flag = False
        self._buffer = np.array([], dtype='float32')
