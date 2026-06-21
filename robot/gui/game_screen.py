"""
robot/gui/game_screen.py
──────────────────────────────────────────────────────────────────────────────
Visual Game Screen — Pygame overlay rendered on the FaceDisplay window.

Activated when a game starts (ui.screen.change → 'game').
Shows a visual board for whichever game is active:
  - memory_match  → flip card grid with emoji symbols
  - emotions      → large emotion face + multiple choice buttons
  - social_skills → scenario text + multiple choice buttons
  - speech_repeat → waveform + text prompt
  - imitation     → action image/text + mirror prompt
  - (fallback)    → game title + voice-prompt banner

Receives real-time state via events:
  game.state_update  → {game_type, state_data}  — pushed by each game
  game.finished      → hides board, shows celebration
"""

import pygame
import math
import time
import logging
from typing import Optional, Dict, Any, List

from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
BG_DARK        = (14,  17,  32)
PANEL_BG       = (24,  30,  52)
CARD_FACE_DOWN = (40,  54, 100)
CARD_FACE_UP   = (56, 189, 212)   # teal
CARD_MATCHED   = (74, 222, 128)   # green
CARD_BORDER    = (80, 110, 200)
ACCENT_GOLD    = (255, 200,  60)
ACCENT_PINK    = (236,  72, 153)
TEXT_WHITE     = (240, 245, 255)
TEXT_DIM       = (120, 140, 180)
BTN_NEUTRAL    = (40,  52,  90)
BTN_HOVER      = (60,  80, 140)
BTN_CORRECT    = (34, 197,  94)
BTN_WRONG      = (239,  68,  68)

# Emoji symbols per card type (ASCII fallbacks if emoji fails)
CARD_SYMBOLS: Dict[str, str] = {
    # animals
    "dog": "🐕", "cat": "🐈", "bird": "🐦", "fish": "🐟",
    "lion": "🦁", "bear": "🐻", "fox": "🦊", "duck": "🦆",
    # colors
    "red": "❤️", "blue": "💙", "green": "💚", "yellow": "💛",
    "orange": "🧡", "purple": "💜", "pink": "🩷", "white": "🤍",
    # emotions
    "happy": "😊", "sad": "😢", "angry": "😠", "scared": "😨",
    "surprised": "😲", "calm": "😌", "excited": "🤩", "tired": "😴",
    # shapes
    "circle": "⭕", "square": "⬛", "triangle": "🔺", "star": "⭐",
    "heart": "❤️", "diamond": "💎", "oval": "🥚", "rectangle": "📄",
}

EMOTION_COLORS: Dict[str, tuple] = {
    "happy":    (255, 200,  60),
    "sad":      ( 56, 189, 212),
    "angry":    (239,  68,  68),
    "scared":   (168,  85, 247),
    "surprised":(251, 146,  60),
    "calm":     ( 74, 222, 128),
    "excited":  (236,  72, 153),
    "neutral":  (120, 140, 180),
}


class GameScreen:
    """Renders a visual game board over the Pygame window."""

    def __init__(self, width: int, height: int):
        self.width  = width
        self.height = height
        self.active = False

        # Current game state
        self.game_type:   Optional[str]       = None
        self.game_state:  Dict[str, Any]      = {}
        self.score_text:  str                 = ""
        self.prompt_text: str                 = ""
        self.is_finished: bool                = False
        self.finish_timer: float              = 0.0

        # Animation
        self._start_time: float = time.time()

        # Cache fonts to prevent OS queries in the hot loop
        try:
            self.font_header = pygame.font.SysFont("segoeuiemoji,segoeui", 30, bold=True)
            self.font_face   = pygame.font.SysFont("segoeuiemoji,segoeui", 54)
            self.font_emoji  = pygame.font.SysFont("segoeuiemoji,segoeui", 30)
            self.font_card_num = pygame.font.SysFont("segoeuiemoji,segoeui", 20)
        except Exception:
            self.font_header = pygame.font.SysFont("segoeui", 30, bold=True)
            self.font_face   = pygame.font.SysFont("segoeui", 54)
            self.font_emoji  = pygame.font.SysFont("segoeui", 30)
            self.font_card_num = pygame.font.SysFont("segoeui", 20)

        self.font_score      = pygame.font.SysFont("segoeui", 22)
        self.font_prompt     = pygame.font.SysFont("segoeui", 22)
        self.font_tiny       = pygame.font.SysFont("segoeui", 16)
        self.font_btn        = pygame.font.SysFont("segoeui", 24, bold=True)
        self.font_q          = pygame.font.SysFont("segoeui", 26, bold=True)
        self.font_wait       = pygame.font.SysFont("segoeui", 28)
        self.font_celeb_big  = pygame.font.SysFont("segoeui", 64, bold=True)
        self.font_celeb_sub  = pygame.font.SysFont("segoeui", 32)

        # Subscribe to state updates from games
        event_bus.subscribe("game.state_update", self._on_state_update)
        event_bus.subscribe("game.finished",     self._on_game_finished)
        event_bus.subscribe("game.board.show",   self._on_board_show)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_state_update(self, _event: str, data: Dict[str, Any]):
        """Receive live state from the active game."""
        self.game_type  = data.get("game_type", self.game_type)
        self.game_state = data.get("state", {})
        self.prompt_text = data.get("prompt", "")
        self.score_text  = data.get("score_text", "")

    async def _on_game_finished(self, _event: str, data: Dict[str, Any]):
        """Show a celebration screen then auto-dismiss."""
        self.is_finished  = True
        self.finish_timer = time.time() + 4.0

    async def _on_board_show(self, _event: str, data: Dict[str, Any]):
        """Activate the game screen for the specified game type."""
        game_type = data.get("game_type", "")
        self.show(game_type)

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self, game_type: str):
        """Activate this screen for a given game type."""
        self.active      = True
        self.game_type   = game_type
        self.game_state  = {}
        self.is_finished = False
        self.prompt_text = ""
        self.score_text  = ""
        self._start_time = time.time()

    def hide(self):
        self.active      = False
        self.game_type   = None
        self.game_state  = {}
        self.is_finished = False

    # ── Main draw ─────────────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface, tick: float):
        if not self.active:
            return

        # Background
        surface.fill(BG_DARK)

        if self.is_finished and time.time() < self.finish_timer:
            self._draw_celebration(surface, tick)
            return
        elif self.is_finished:
            self.hide()
            return

        # Header bar
        self._draw_header(surface)

        # Route to per-game renderer
        gt = self.game_type or ""
        if gt == "memory_match":
            self._draw_memory_match(surface, tick)
        elif gt == "emotions":
            self._draw_emotions(surface, tick)
        elif gt in ("social_skills", "speech_repeat", "imitation",
                    "colors", "focus", "turn_taking"):
            self._draw_generic(surface, tick)
        else:
            self._draw_generic(surface, tick)

        # Footer prompt
        self._draw_prompt_bar(surface)

    # ── Header ────────────────────────────────────────────────────────────────

    def _draw_header(self, surface: pygame.Surface):
        GAME_NAMES = {
            "memory_match":  "🃏 Memory Match",
            "emotions":      "😊 Feelings Game",
            "social_skills": "🤝 Social Skills",
            "speech_repeat": "🗣️ Speech Game",
            "imitation":     "🤸 Imitation Game",
            "colors":        "🎨 Colors Game",
            "focus":         "🎯 Focus Game",
            "turn_taking":   "🔄 Turn Taking",
        }
        title = GAME_NAMES.get(self.game_type or "", "🎮 Game")

        # Background strip
        header_rect = pygame.Rect(0, 0, self.width, 58)
        pygame.draw.rect(surface, PANEL_BG, header_rect)
        pygame.draw.line(surface, CARD_BORDER, (0, 58), (self.width, 58), 2)

        title_surf = self.font_header.render(title, True, TEXT_WHITE)
        surface.blit(title_surf, (20, 14))

        # Score badge
        if self.score_text:
            score_surf = self.font_score.render(self.score_text, True, ACCENT_GOLD)
            surface.blit(score_surf, (self.width - score_surf.get_width() - 20, 18))

    # ── Prompt bar ────────────────────────────────────────────────────────────

    def _draw_prompt_bar(self, surface: pygame.Surface):
        bar_h = 52
        bar_rect = pygame.Rect(0, self.height - bar_h, self.width, bar_h)
        pygame.draw.rect(surface, PANEL_BG, bar_rect)
        pygame.draw.line(surface, CARD_BORDER,
                         (0, self.height - bar_h), (self.width, self.height - bar_h), 2)

        text = self.prompt_text or "🎙️ Say your answer!"
        surf = self.font_prompt.render(text, True, TEXT_WHITE)
        y = self.height - bar_h + (bar_h - surf.get_height()) // 2
        surface.blit(surf, (20, y))

    # ── Memory Match board ────────────────────────────────────────────────────

    def _draw_memory_match(self, surface: pygame.Surface, tick: float):
        state = self.game_state
        cards:    List[str]  = state.get("cards", [])
        revealed: List[bool] = state.get("revealed", [False] * len(cards))
        category: str        = state.get("category", "animals")

        if not cards:
            self._draw_waiting(surface, "Getting the cards ready...")
            return

        # Layout: fit cards in a grid inside content area (below header, above prompt)
        content_top  = 68
        content_bot  = self.height - 60
        content_h    = content_bot - content_top
        content_w    = self.width - 40

        n = len(cards)
        cols = min(n, 4) if n <= 8 else 5
        rows = math.ceil(n / cols)

        card_w = min(130, (content_w - (cols + 1) * 10) // cols)
        card_h = min(100, (content_h - (rows + 1) * 10) // rows)
        pad_x  = (content_w - cols * card_w - (cols - 1) * 10) // 2 + 20
        pad_y  = content_top + (content_h - rows * card_h - (rows - 1) * 10) // 2

        for i, (card, rev) in enumerate(zip(cards, revealed)):
            col = i % cols
            row = i // cols
            x = pad_x + col * (card_w + 10)
            y = pad_y + row * (card_h + 10)
            rect = pygame.Rect(x, y, card_w, card_h)

            if rev:
                color  = CARD_MATCHED
                border = (100, 255, 150)
            else:
                # Slight pulse on face-down cards
                pulse = 0.5 + 0.5 * math.sin(tick * 2 + i * 0.7)
                b = int(80 + 20 * pulse)
                color  = (CARD_FACE_DOWN[0], CARD_FACE_DOWN[1], b)
                border = CARD_BORDER

            pygame.draw.rect(surface, color, rect, border_radius=12)
            pygame.draw.rect(surface, border, rect, width=2, border_radius=12)

            if rev:
                # Show emoji + label
                symbol = CARD_SYMBOLS.get(card, "?")
                sym_surf = self.font_emoji.render(symbol, True, BG_DARK)
                lbl_surf = self.font_tiny.render(card.upper(), True, BG_DARK)
                surface.blit(sym_surf, sym_surf.get_rect(center=(x + card_w // 2, y + card_h // 2 - 12)))
                surface.blit(lbl_surf, lbl_surf.get_rect(center=(x + card_w // 2, y + card_h - 16)))
            else:
                # Face-down: show card number
                num_surf = self.font_card_num.render(str(i + 1), True, TEXT_DIM)
                surface.blit(num_surf, num_surf.get_rect(center=(x + card_w // 2, y + card_h // 2)))

        # Pairs counter
        pairs_found = state.get("pairs_found", 0)
        total_pairs = state.get("total_pairs", n // 2)
        counter_text = f"Pairs: {pairs_found} / {total_pairs}"
        counter_surf = self.font_prompt.render(counter_text, True, ACCENT_GOLD)
        surface.blit(counter_surf, (self.width - counter_surf.get_width() - 20,
                                     content_bot - 28))

    # ── Emotions board ────────────────────────────────────────────────────────

    def _draw_emotions(self, surface: pygame.Surface, tick: float):
        state = self.game_state
        target:  str       = state.get("target", "")
        options: List[str] = state.get("options", [])
        last_correct: Optional[bool] = state.get("last_correct", None)

        content_top = 68
        content_bot = self.height - 60

        if target:
            # Draw big emotion face in top half
            color = EMOTION_COLORS.get(target, (120, 140, 180))
            cx = self.width // 2
            cy = content_top + (content_bot - content_top) // 3

            pulse = 0.92 + 0.08 * math.sin(tick * 1.5)
            radius = int(80 * pulse)

            glow_surf = pygame.Surface((self.width, content_bot), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*color, 30), (cx, cy - content_top), radius + 30)
            surface.blit(glow_surf, (0, content_top))

            pygame.draw.circle(surface, color, (cx, cy), radius)
            pygame.draw.circle(surface, (255, 255, 255), (cx, cy), radius, width=3)

            # Emoji face label
            symbol = CARD_SYMBOLS.get(target, "😊")
            face_surf = self.font_face.render(symbol, True, (255, 255, 255))
            surface.blit(face_surf, face_surf.get_rect(center=(cx, cy)))

        # Draw option buttons in bottom half
        if options:
            btn_w   = 160
            btn_h   = 48
            gap     = 16
            total_w = len(options) * btn_w + (len(options) - 1) * gap
            start_x = (self.width - total_w) // 2
            btn_y   = content_bot - 80

            for idx, opt in enumerate(options):
                bx = start_x + idx * (btn_w + gap)
                btn_rect = pygame.Rect(bx, btn_y, btn_w, btn_h)

                if last_correct is not None and opt == target:
                    color = BTN_CORRECT
                else:
                    color = BTN_NEUTRAL

                pygame.draw.rect(surface, color, btn_rect, border_radius=12)
                pygame.draw.rect(surface, CARD_BORDER, btn_rect, width=2, border_radius=12)

                label_surf = self.font_btn.render(opt.capitalize(), True, TEXT_WHITE)
                surface.blit(label_surf, label_surf.get_rect(center=btn_rect.center))
        elif not target:
            self._draw_waiting(surface, "Getting ready...")

    # ── Generic game board ────────────────────────────────────────────────────

    def _draw_generic(self, surface: pygame.Surface, tick: float):
        state = self.game_state
        question: str      = state.get("question", "")
        options:  List[str] = state.get("options", [])

        content_top = 68
        content_bot = self.height - 60
        cx = self.width // 2

        if question:
            # Wrap and render question
            words = question.split()
            lines: List[str] = []
            current = ""
            max_w = self.width - 80
            for word in words:
                test = (current + " " + word).strip()
                if self.font_q.size(test)[0] <= max_w:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)

            y = content_top + 30
            for line in lines[:4]:
                surf = self.font_q.render(line, True, TEXT_WHITE)
                surface.blit(surf, surf.get_rect(centerx=cx, y=y))
                y += surf.get_height() + 6

        if options:
            btn_w = 200
            btn_h = 52
            gap   = 14
            cols  = 2 if len(options) > 2 else len(options)
            rows  = math.ceil(len(options) / cols)
            total_w = cols * btn_w + (cols - 1) * gap
            total_h = rows * btn_h + (rows - 1) * gap
            start_x = (self.width - total_w) // 2
            start_y = content_bot - total_h - 20

            for idx, opt in enumerate(options):
                col = idx % cols
                row = idx // cols
                bx = start_x + col * (btn_w + gap)
                by = start_y + row * (btn_h + gap)
                btn_rect = pygame.Rect(bx, by, btn_w, btn_h)

                pulse = 0.5 + 0.5 * math.sin(tick * 2 + idx * 1.2)
                alpha = int(200 + 55 * pulse)
                color = (*BTN_NEUTRAL[:3],)
                pygame.draw.rect(surface, color, btn_rect, border_radius=12)
                pygame.draw.rect(surface, CARD_BORDER, btn_rect, width=2, border_radius=12)

                label_surf = self.font_score.render(opt.capitalize(), True, TEXT_WHITE)
                surface.blit(label_surf, label_surf.get_rect(center=btn_rect.center))
        elif not question:
            self._draw_waiting(surface, "🎙️ Listen and respond!")

    # ── Waiting / placeholder ─────────────────────────────────────────────────

    def _draw_waiting(self, surface: pygame.Surface, msg: str):
        cx = self.width // 2
        cy = self.height // 2
        t  = time.time()

        # Pulsing ring
        for r_off in range(3):
            pulse = 0.5 + 0.5 * math.sin(t * 2 - r_off * 0.8)
            r = int(50 + r_off * 20 + 10 * pulse)
            alpha = int(60 - r_off * 15)
            s = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            pygame.draw.circle(s, (56, 189, 212, alpha), (cx, cy - 30), r)
            surface.blit(s, (0, 0))

        surf = self.font_wait.render(msg, True, TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=cx, centery=cy + 40))

    # ── Celebration screen ────────────────────────────────────────────────────

    def _draw_celebration(self, surface: pygame.Surface, tick: float):
        surface.fill(BG_DARK)
        cx = self.width  // 2
        cy = self.height // 2

        # Firework particles
        for i in range(24):
            angle = (i / 24) * math.tau + tick * 0.5
            r     = 140 + 30 * math.sin(tick * 3 + i)
            px    = int(cx + r * math.cos(angle))
            py    = int(cy + r * math.sin(angle))
            hue   = (i * 15 + int(tick * 60)) % 360
            color = pygame.Color(0)
            color.hsva = (hue, 90, 95, 100)
            pygame.draw.circle(surface, color, (px, py), 6)

        main_surf = self.font_celeb_big.render("🎉 Amazing Job! 🎉", True, ACCENT_GOLD)
        sub_surf  = self.font_celeb_sub.render("Game complete! You did great!", True, TEXT_WHITE)
        surface.blit(main_surf, main_surf.get_rect(center=(cx, cy - 30)))
        surface.blit(sub_surf,  sub_surf.get_rect(center=(cx, cy + 50)))
