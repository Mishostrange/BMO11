import pygame
import math
import random
import logging
from robot.config.settings import settings

logger = logging.getLogger(__name__)

class FaceAnimations:
    """Manages rendering of BMO's face using primitive shapes in Pygame."""

    def __init__(self):
        # BMO style colors
        self.COLORS = {
            "bg_neutral": (166, 219, 185), # Light teal green
            "bg_angry": (224, 102, 102),   # Soft red
            "bg_sad": (159, 197, 232),     # Soft blue
            "bg_calm": (180, 167, 214),    # Soft purple
            "eye": (25, 60, 40),           # Dark green/black
            "mouth": (25, 60, 40),
            "blush": (100, 180, 140, 128)  # Semi-transparent
        }
        
        self.width = settings.display.WIDTH
        self.height = settings.display.HEIGHT
        self.center_x = self.width // 2
        self.center_y = self.height // 2
        
        self.eye_dist = int(self.width * 0.25)
        self.eye_radius = int(self.width * 0.08)
        self.mouth_y = self.center_y + int(self.height * 0.15)
        
        self.blink_state = 1.0 # 1.0 = open, 0.0 = closed
        self.is_blinking = False
        self.blink_speed = 0.3
        self.last_blink_time = 0

    def draw(self, surface, expression: str, tick: float):
        """Draw the face on the surface based on expression."""
        
        # 1. Background color
        bg_color = self.COLORS["bg_neutral"]
        if expression == "angry": bg_color = self.COLORS["bg_angry"]
        elif expression == "sad": bg_color = self.COLORS["bg_sad"]
        elif expression == "calm": bg_color = self.COLORS["bg_calm"]
        surface.fill(bg_color)
        
        # 2. Handle blinking logic
        self._update_blink(tick)
        
        # 3. Draw Eyes
        left_eye_pos = (self.center_x - self.eye_dist, self.center_y - int(self.height * 0.1))
        right_eye_pos = (self.center_x + self.eye_dist, self.center_y - int(self.height * 0.1))
        
        self._draw_eye(surface, left_eye_pos, expression, tick, is_left=True)
        self._draw_eye(surface, right_eye_pos, expression, tick, is_left=False)
        
        # 4. Draw Mouth
        self._draw_mouth(surface, expression, tick)

    def _update_blink(self, tick: float):
        if not self.is_blinking:
            if random.random() < 0.01: # Random chance to blink
                self.is_blinking = True
                self.blink_state = 1.0
        else:
            self.blink_state -= self.blink_speed
            if self.blink_state <= -1.0: # Went fully closed and opened again
                self.is_blinking = False
                self.blink_state = 1.0

    def _draw_eye(self, surface, pos, expression, tick, is_left):
        x, y = pos
        color = self.COLORS["eye"]
        
        # Idle animation (slight floating)
        y += int(math.sin(tick * 2) * 5)
        
        current_radius = self.eye_radius
        height_mult = max(0.1, abs(self.blink_state)) if self.is_blinking else 1.0
        
        rect = pygame.Rect(
            x - current_radius, 
            y - int(current_radius * height_mult), 
            current_radius * 2, 
            int(current_radius * 2 * height_mult)
        )

        if expression in ["happy", "neutral", "calm", "speaking"]:
            pygame.draw.ellipse(surface, color, rect)
        elif expression == "sad":
            # Flattened top
            pygame.draw.ellipse(surface, color, rect)
            pygame.draw.rect(surface, bg_color, (x - current_radius, y - current_radius, current_radius * 2, current_radius))
        elif expression == "angry":
            # Angled top
            pygame.draw.ellipse(surface, color, rect)
            angle = -15 if is_left else 15
            # Simplified angry eyebrow using a line
            eyebrow_y = y - current_radius
            end_y = eyebrow_y + (10 if is_left else -10)
            pygame.draw.line(surface, color, (x - current_radius - 10, eyebrow_y), (x + current_radius + 10, end_y), 8)
        elif expression == "thinking":
            if is_left:
                pygame.draw.ellipse(surface, color, rect)
            else:
                # Squint right eye
                squint_rect = pygame.Rect(x - current_radius, y - int(current_radius * 0.3), current_radius * 2, int(current_radius * 0.6))
                pygame.draw.ellipse(surface, color, squint_rect)
        elif expression == "scared":
            # Wide eyes
            wide_rect = pygame.Rect(x - int(current_radius*1.2), y - int(current_radius*1.2), int(current_radius*2.4), int(current_radius*2.4))
            pygame.draw.ellipse(surface, color, wide_rect)

    def _draw_mouth(self, surface, expression, tick):
        x, y = self.center_x, self.mouth_y
        color = self.COLORS["mouth"]
        
        width = int(self.width * 0.15)
        
        if expression == "happy" or expression == "neutral":
            # Smile curve
            rect = pygame.Rect(x - width//2, y - width//4, width, width//2)
            pygame.draw.arc(surface, color, rect, math.pi, 2*math.pi, 6)
        elif expression == "sad":
            # Frown curve
            rect = pygame.Rect(x - width//2, y, width, width//2)
            pygame.draw.arc(surface, color, rect, 0, math.pi, 6)
        elif expression == "speaking":
            # Animated open mouth
            open_amount = abs(math.sin(tick * 10)) * (width // 2)
            rect = pygame.Rect(x - width//2, y, width, max(10, int(open_amount)))
            pygame.draw.ellipse(surface, color, rect)
        elif expression == "angry":
            # Straight line
            pygame.draw.line(surface, color, (x - width//2, y), (x + width//2, y), 6)
        elif expression == "thinking":
            # Small circle offset
            rect = pygame.Rect(x + width//4, y, width//3, width//3)
            pygame.draw.ellipse(surface, color, rect)
        elif expression == "scared":
            # Large circle
            rect = pygame.Rect(x - width//4, y, width//2, width//2)
            pygame.draw.ellipse(surface, color, rect)
        elif expression == "calm":
            # Small smile
            rect = pygame.Rect(x - width//4, y - width//8, width//2, width//4)
            pygame.draw.arc(surface, color, rect, math.pi, 2*math.pi, 4)
