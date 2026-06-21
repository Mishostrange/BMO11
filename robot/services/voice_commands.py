import asyncio
import logging
import os
import numpy as np
from robot.services.event_bus import event_bus
from robot.services.service_registry import service_registry

logger = logging.getLogger(__name__)

class VoiceCommandHandler:
    """Handles voice commands like 'bmo play music'."""
    
    def __init__(self):
        self.audio_playback = None
        self.tts = None
        self.music_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "music")
        self.is_playing_music = False
        self.music_stream = None
        
        # Ensure music directory exists
        os.makedirs(self.music_dir, exist_ok=True)
        
        # Subscribe to transcribed speech
        event_bus.subscribe('speech.transcribed', self._on_speech_transcribed)
        
    def _on_speech_transcribed(self, event_type: str, text: str):
        """Handle transcribed speech and check for commands."""
        text_lower = text.lower().strip()
        logger.info(f"Voice command handler received: {text_lower}")
        
        # Check for play music command
        if "bmo" in text_lower and "play music" in text_lower:
            logger.info("Detected 'bmo play music' command")
            asyncio.create_task(self._play_music())
        
        # Check for stop music command
        elif "stop music" in text_lower or "stop the music" in text_lower:
            logger.info("Detected stop music command")
            asyncio.create_task(self._stop_music())
    
    async def _play_music(self):
        """Play music from the music directory."""
        try:
            # Get services
            self.audio_playback = service_registry.get("audio_playback")
            self.tts = service_registry.get("tts")
            
            if not self.audio_playback:
                logger.error("Audio playback service not available")
                return
            
            # Find music files
            music_files = [f for f in os.listdir(self.music_dir) 
                          if f.endswith(('.mp3', '.wav', '.ogg'))]
            
            if not music_files:
                logger.warning(f"No music files found in {self.music_dir}")
                if self.tts:
                    await self.tts.speak("I don't have any music files to play.")
                return
            
            # Play the first music file
            music_file = music_files[0]
            music_path = os.path.join(self.music_dir, music_file)
            logger.info(f"Playing music: {music_file}")
            
            if self.tts:
                await self.tts.speak("Okay, I'll play some music for you!")
            
            # Use pygame mixer for music playback
            import pygame.mixer
            pygame.mixer.init()
            pygame.mixer.music.load(music_path)
            pygame.mixer.music.play()
            self.is_playing_music = True
            
            # Wait for music to finish or be stopped
            while pygame.mixer.music.get_busy() and self.is_playing_music:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error playing music: {e}")
    
    async def _stop_music(self):
        """Stop music playback."""
        try:
            import pygame.mixer
            pygame.mixer.music.stop()
            self.is_playing_music = False
            logger.info("Music stopped")
            
            if self.tts:
                await self.tts.speak("Okay, I stopped the music.")
                
        except Exception as e:
            logger.error(f"Error stopping music: {e}")
