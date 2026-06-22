import pygame
import asyncio
import logging
import time
from robot.config.settings import settings
from robot.services.event_bus import event_bus
from robot.gui.animations import FaceAnimations
from robot.gui.games_dashboard import GamesDashboard
from robot.gui.game_screen import GameScreen

logger = logging.getLogger(__name__)

class FaceDisplay:
    """Manages the Pygame window and rendering loop for the robot's face."""

    def __init__(self):
        pygame.init()
        self.width = settings.ui.WIDTH
        self.height = settings.ui.HEIGHT
        self.fps = settings.ui.FPS
        
        # In production on RPi, you might want FULLSCREEN
        # self.screen = pygame.display.set_mode((self.width, self.height), pygame.FULLSCREEN)
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("BMO Face")
        
        self.clock = pygame.time.Clock()
        self.animator = FaceAnimations()
        
        self.current_expression = "neutral"
        self._is_running = False
        self.start_time = time.time()
        
        # Games dashboard overlay
        self.dashboard = GamesDashboard(self.width, self.height)
        # Active game visual board
        self.game_screen = GameScreen(self.width, self.height)
        self.active_screen = "face"   # 'face' | 'games_dashboard' | 'game'
        
        # Subscribe to expression changes and VAD events
        event_bus.subscribe("ui.expression.change", self._on_expression_change)
        event_bus.subscribe("ui.animation.trigger", self._on_animation_trigger)
        event_bus.subscribe("ui.screen.change",     self._on_screen_change)
        event_bus.subscribe("session.started",      self._on_session_started)
        event_bus.subscribe("speech.started",       self._on_speech_started)
        event_bus.subscribe("speech.ended",         self._on_speech_ended_vad)
        
        self.active_overlay = None
        self.overlay_end_time = 0
        
        # Video playback
        self.video_cap = None
        self.video_path = "faces/Videos/BMO dancing to BMO - كينزي (360p, h264).mp4"

        # VAD listening state
        self._listening = False
        self._queued_expression = None

        # Cache fonts
        try:
            self.font_breathe = pygame.font.SysFont("Arial", 36)
            self.font_countdown = pygame.font.SysFont("Arial", 72)
            self.font_overlay = pygame.font.SysFont("Arial", 72)
        except Exception:
            self.font_breathe = pygame.font.SysFont(None, 36)
            self.font_countdown = pygame.font.SysFont(None, 72)
            self.font_overlay = pygame.font.SysFont(None, 72)

    async def _on_expression_change(self, event_type: str, expression: str):
        # Don't override 'listening' if VAD is actively hearing the child
        if self._listening and expression not in ("speaking", "listening"):
            self._queued_expression = expression
            return
        self.current_expression = expression
        self._queued_expression = None

    async def _on_speech_started(self, *_):
        """VAD detected voice — show the listening animation."""
        self._listening = True
        self.current_expression = "listening"

    async def _on_speech_ended_vad(self, *_):
        """VAD finished — restore previous expression."""
        self._listening = False
        self.current_expression = self._queued_expression or "neutral"
        self._queued_expression = None

    async def _on_screen_change(self, _event: str, screen_name: str):
        """Switch between 'face', 'games_dashboard', and 'game' screens."""
        self.active_screen = screen_name
        if screen_name == "games_dashboard":
            self.dashboard.active = True
            self.game_screen.hide()
        elif screen_name == "game":
            self.dashboard.active = False
            # game_type is set separately via game.state_update
        else:  # 'face'
            self.dashboard.active = False
            self.game_screen.hide()

    async def _on_session_started(self, _event: str, data: dict):
        child_id = data.get("child_id")
        if child_id:
            self.dashboard.set_child(child_id)

    async def _on_animation_trigger(self, event_type: str, data: dict):
        anim_type = data.get("type")
        logger.debug(f"Triggering animation overlay: {anim_type}")
        self.active_overlay = anim_type
        # Breathing exercise is longer — give it 30 seconds
        if anim_type == "breathe":
            self.overlay_end_time = time.time() + 30.0
        elif anim_type == "wave":
            self.overlay_end_time = time.time() + 3.0
        elif anim_type == "look_left":
            self.overlay_end_time = time.time() + 2.0
        elif anim_type == "video_dance":
            self.overlay_end_time = time.time() + 60.0  # Max timeout
            if self.video_cap:
                self.video_cap.release()
            import cv2
            import os
            
            # Use absolute path relative to the workspace to ensure it works
            # assuming the CWD is the root of the project
            abs_path = os.path.abspath(self.video_path)
            self.video_cap = cv2.VideoCapture(abs_path)
            if not self.video_cap.isOpened():
                logger.error(f"Failed to open video: {abs_path}")
                self.active_overlay = None
        else:
            self.overlay_end_time = time.time() + 2.0

    def _draw_overlay(self):
        if not self.active_overlay:
            return
            
        if time.time() > self.overlay_end_time:
            self.active_overlay = None
            if self.video_cap:
                self.video_cap.release()
                self.video_cap = None
            return

        # ── Video Playback ────────────────────────────────────────────────────────
        if self.active_overlay == "video_dance" and self.video_cap:
            import cv2
            import numpy as np
            
            ret, frame = self.video_cap.read()
            if not ret:
                self.active_overlay = None
                self.video_cap.release()
                self.video_cap = None
                return
                
            # Convert BGR to RGB
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Resize frame to fit screen
            frame = cv2.resize(frame, (self.width, self.height))
            
            # Pygame expects (W, H, 3) layout
            frame = np.swapaxes(frame, 0, 1)
            surf = pygame.surfarray.make_surface(frame)
            
            self.screen.blit(surf, (0, 0))
            return

        # ── Breathing animation: pulsing calming circle ───────────────────────
        if self.active_overlay == "breathe":
            import math
            t = time.time()
            # Full breath cycle: 4s inhale, 7s hold, 8s exhale = 19s total
            phase = (t % 19)
            if phase < 4:
                progress = phase / 4.0      # inhale: 0 → 1
            elif phase < 11:
                progress = 1.0              # hold
            else:
                progress = 1.0 - (phase - 11) / 8.0  # exhale: 1 → 0
            
            max_r = min(self.width, self.height) // 3
            min_r = max_r // 3
            radius = int(min_r + (max_r - min_r) * progress)
            
            # Calming teal/blue gradient effect via alpha surface
            surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            alpha = int(60 + 80 * progress)
            pygame.draw.circle(surf, (56, 189, 212, alpha),
                               (self.width // 2, self.height // 2), radius)
            pygame.draw.circle(surf, (14, 165, 233, max(0, alpha - 40)),
                               (self.width // 2, self.height // 2), radius - 15)
            self.screen.blit(surf, (0, 0))

            # Instruction text
            if phase < 4:
                label = "Breathe in..."
            elif phase < 11:
                label = "Hold..."
            else:
                label = "Breathe out..."
            text_surf = self.font_breathe.render(label, True, (255, 255, 255))
            text_rect = text_surf.get_rect(center=(self.width // 2, self.height - 60))
            self.screen.blit(text_surf, text_rect)
            return
            
        # ── Wave animation ───────────────────────────────────────────────
        if self.active_overlay == "wave":
            import math
            t = time.time()
            # Draw a waving hand animation
            wave_x = self.width // 2 + int(math.sin(t * 10) * 30)
            wave_y = self.height // 2
            
            # Draw simple hand shape
            pygame.draw.circle(self.screen, (255, 200, 150), (wave_x, wave_y), 40)
            # Draw fingers
            for i in range(5):
                finger_x = wave_x + int(math.sin(t * 10 + i * 0.5) * 20)
                finger_y = wave_y - 50 + i * 10
                pygame.draw.circle(self.screen, (255, 200, 150), (finger_x, finger_y), 8)
            return
            
        # ── Look left animation ───────────────────────────────────────────
        if self.active_overlay == "look_left":
            # Draw eyes looking left
            eye_offset = -20
            left_eye_pos = (self.center_x - self.eye_dist + eye_offset, self.center_y - int(self.height * 0.1))
            right_eye_pos = (self.center_x + self.eye_dist + eye_offset, self.center_y - int(self.height * 0.1))
            
            pygame.draw.circle(self.screen, (25, 60, 40), left_eye_pos, self.eye_radius)
            pygame.draw.circle(self.screen, (25, 60, 40), right_eye_pos, self.eye_radius)
            
            # Draw pupils looking left
            pupil_offset = -10
            pygame.draw.circle(self.screen, (255, 255, 255), 
                            (left_eye_pos[0] + pupil_offset, left_eye_pos[1]), 8)
            pygame.draw.circle(self.screen, (255, 255, 255), 
                            (right_eye_pos[0] + pupil_offset, right_eye_pos[1]), 8)
            return
            
        # ── Other simple overlays ────────────────────────────────────────
        text = ""
        if self.active_overlay == "confetti":
            text = "GREAT JOB!"
        elif self.active_overlay == "star_burst":
            text = "AMAZING!"
        if self.active_overlay == "countdown":
            import math
            remaining = int(math.ceil(self.overlay_end_time - time.time()))
            if remaining > 0:
                text_surf = self.font_countdown.render(str(remaining), True, (255, 200, 100))
                rect = text_surf.get_rect(center=(self.width//2, self.height//2))
                self.screen.blit(text_surf, rect)
        elif text:
            img = self.font_overlay.render(text, True, (255, 215, 0))
            rect = img.get_rect(center=(self.width//2, self.height//4))
            self.screen.blit(img, rect)

    async def run_loop(self):
        """Async main loop for the Pygame window."""
        self._is_running = True
        logger.info("Started Pygame face display")
        
        while self._is_running:
            # Handle Pygame events to prevent OS unresponsiveness
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._is_running = False
                # Forward mouse events to dashboard when active
                if self.dashboard.active:
                    self.dashboard.handle_event(event)
                    
            # Calculate tick for procedural animations (sin waves)
            tick = time.time() - self.start_time
            
            if self.dashboard.active:
                # Dashboard overrides the face view
                self.dashboard.draw(self.screen, tick)
            elif self.active_screen == "game" and self.game_screen.active:
                self.game_screen.draw(self.screen, tick)
            else:
                # Normal face rendering
                self.animator.draw(self.screen, self.current_expression, tick)
            
            # Always draw overlays (like video) on top of whatever screen is active
            self._draw_overlay()
            pygame.display.flip()
            
            # Yield to asyncio event loop
            await asyncio.sleep(1.0 / self.fps)
            
        pygame.quit()
        logger.info("Stopped Pygame face display")

    def stop(self):
        self._is_running = False
