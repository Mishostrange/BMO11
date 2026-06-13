import asyncio
import logging

# We mock sounddevice and other heavy modules for the test if they aren't installed, 
# or we just try importing to see if syntax is correct.
try:
    import sounddevice as sd
except ImportError:
    pass

from robot.audio.capture import AudioCapture
from robot.audio.playback import AudioPlayback
from robot.vad.silero_vad import SileroVADNode
from robot.stt.whisper_stt import FasterWhisperNode
from robot.tts.kokoro_tts import KokoroTTSNode

def test_imports():
    print("Imports successful!")
    print(f"AudioCapture defined: {AudioCapture}")
    print(f"AudioPlayback defined: {AudioPlayback}")
    print(f"SileroVADNode defined: {SileroVADNode}")
    print(f"FasterWhisperNode defined: {FasterWhisperNode}")
    print(f"KokoroTTSNode defined: {KokoroTTSNode}")

if __name__ == "__main__":
    test_imports()
