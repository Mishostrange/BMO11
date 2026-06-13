import pygame
import asyncio
import logging
import time
from robot.config.settings import settings
from robot.services.event_bus import event_bus
from robot.gui.animations import FaceAnimations

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
        
        # Subscribe to expression changes
        event_bus.subscribe("ui.expression.change", self._on_expression_change)
        event_bus.subscribe("ui.animation.trigger", self._on_animation_trigger)
        
        self.active_overlay = None
        self.overlay_end_time = 0

    async def _on_expression_change(self, event_type: str, expression: str):
        logger.debug(f"Changing expression to: {expression}")
        self.current_expression = expression

    async def _on_animation_trigger(self, event_type: str, data: dict):
        anim_type = data.get("type")
        logger.debug(f"Triggering animation overlay: {anim_type}")
        self.active_overlay = anim_type
        self.overlay_end_time = time.time() + 2.0 # Show for 2 seconds

    def _draw_overlay(self):
        if not self.active_overlay:
            return
            
        if time.time() > self.overlay_end_time:
            self.active_overlay = None
            return
            
        # Very simple placeholder for overlays
        # In a real app, this would be a particle system
        font = pygame.font.SysFont(None, 72)
        text = ""
        if self.active_overlay == "confetti":
            text = "🎉"
        elif self.active_overlay == "star_burst":
            text = "⭐"
        elif self.active_overlay == "fireworks":
            text = "🎆"
            
        if text:
            # Note: Pygame default fonts might not support emojis well, 
            # this is a placeholder. Text rendering or images should be used.
            img = font.render(self.active_overlay.replace('_', ' ').upper(), True, (255, 215, 0))
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
                    
            # Calculate tick for procedural animations (sin waves)
            tick = time.time() - self.start_time
            
            # Draw face
            self.animator.draw(self.screen, self.current_expression, tick)
            
            # Draw overlays (rewards)
            self._draw_overlay()
            
            pygame.display.flip()
            
            # Yield to asyncio event loop
            await asyncio.sleep(1.0 / self.fps)
            
        pygame.quit()
        logger.info("Stopped Pygame face display")

    def stop(self):
        self._is_running = False
