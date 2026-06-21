import pygame
import logging
from pathlib import Path
from robot.config.settings import BASE_DIR, settings

logger = logging.getLogger(__name__)

# Maps every semantic expression the robot publishes → the faces/ subfolder name.
# Any expression not listed here will fall back to 'idle'.
EXPRESSION_MAP = {
    "neutral":   "idle",
    "happy":     "idle",      # idle shows BMO's neutral happy face
    "calm":      "idle",
    "sad":       "idle",      # fall back to idle; extend with a sad folder later
    "angry":     "idle",
    "scared":    "idle",
    "speaking":  "speaking",
    "thinking":  "thinking",
    "listening": "listening",
    "capturing": "capturing",
    "error":     "error",
    "warmup":    "warmup",
}

# Per-animation frame rate (frames per second)
ANIM_FPS = {
    "speaking":  8,   # cycle mouth frames quickly
    "thinking":  4,   # slow, thoughtful
    "listening": 6,
    "default":   3,   # idle / single-frame animations
}


class FaceAnimations:
    """Manages rendering of BMO's face using image sequences from faces/."""

    def __init__(self):
        self.width  = settings.ui.WIDTH
        self.height = settings.ui.HEIGHT

        self.faces_dir  = BASE_DIR / "faces"
        self.animations: dict = {}          # name → list[pygame.Surface]
        self._load_animations()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load_animations(self):
        if not self.faces_dir.exists():
            logger.error(f"Faces directory not found at {self.faces_dir}")
            return

        for anim_dir in sorted(self.faces_dir.iterdir()):
            if not anim_dir.is_dir():
                continue
            anim_name = anim_dir.name
            frames    = []
            for img_path in sorted(anim_dir.glob("*.png")):
                try:
                    img = pygame.image.load(str(img_path)).convert_alpha()
                    img = pygame.transform.smoothscale(img, (self.width, self.height))
                    frames.append(img)
                except Exception as e:
                    logger.error(f"Failed to load {img_path}: {e}")

            if frames:
                self.animations[anim_name] = frames
                logger.info(f"Loaded animation '{anim_name}' ({len(frames)} frame(s))")

        if not self.animations:
            logger.warning("No face animations were loaded!")

    # ── Rendering ────────────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface, expression: str, tick: float):
        """Blit the correct animation frame onto *surface*."""
        anim_name = EXPRESSION_MAP.get(expression, "idle")

        # Graceful fallback chain: mapped name → 'idle' → first available
        frames = (
            self.animations.get(anim_name)
            or self.animations.get("idle")
            or next(iter(self.animations.values()), None)
        )

        if frames:
            fps       = ANIM_FPS.get(anim_name, ANIM_FPS["default"])
            frame_idx = int(tick * fps) % len(frames)
            surface.blit(frames[frame_idx], (0, 0))
        else:
            surface.fill((20, 24, 40))   # deep navy fallback

