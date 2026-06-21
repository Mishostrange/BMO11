"""
robot/gui/games_dashboard.py
──────────────────────────────────────────────────────────────────────────────
Child-Friendly Games Dashboard — Pygame Screen.

This screen sits between the BMO face and the individual game views.
It is activated by the event  'ui.screen.change'  with data 'games_dashboard'.
The TherapyEngine can also voice-command the screen by publishing
'ui.show_dashboard'.

Layout (800 × 480):
  ┌─────────────────────────────────────────────────────────────────┐
  │                  ✨ BMO's Game Room ✨  (header)                  │
  ├──────────┬──────────┬──────────┬──────────┬────────────────────┤
  │  😊      │  🃏      │  🗣️     │  🤸     │                    │
  │ Emotion  │ Memory   │ Speech   │Imitation │   XP / Stars       │
  │  Game    │  Match   │ Therapy  │  Game    │   panel            │
  │ ★★★☆☆   │ ★★☆☆☆   │ ★★★★☆   │ ★☆☆☆☆   │                    │
  ├──────────┴──────────┴──────────┴──────────┴────────────────────┤
  │  Last score: 80%    Difficulty: Medium    Tap a card to play!  │
  └─────────────────────────────────────────────────────────────────┘

The dashboard is rendered inside FaceDisplay's run_loop when
`active_screen == 'games_dashboard'`.  Clicking (or voice-selecting)
a game card publishes 'game.launch' with the game_type so TherapyEngine
can start the correct game.
"""

import pygame
import math
import time
import logging
from typing import Optional, Dict, Any, List, Tuple

from robot.services.event_bus import event_bus
from robot.database.connection import db

logger = logging.getLogger(__name__)

# ── Color palette ─────────────────────────────────────────────────────────────
BG_COLOR         = (20,  24,  40)   # deep navy
CARD_BG          = (32,  40,  68)   # card face
CARD_HOVER       = (50,  62, 100)   # card on hover
CARD_BORDER      = (80, 100, 180)
ACCENT_GOLD      = (255, 200,  60)
ACCENT_TEAL      = ( 56, 189, 212)
ACCENT_PINK      = (236,  72, 153)
ACCENT_GREEN     = ( 74, 222, 128)
TEXT_LIGHT       = (220, 230, 255)
TEXT_DIM         = (120, 130, 160)
STAR_ON          = (255, 200,  50)
STAR_OFF         = ( 60,  70,  90)
PANEL_BG         = (28,  36,  60)
TEXT_WHITE       = (255, 255, 255)

# ── Game card definitions ─────────────────────────────────────────────────────
GAME_CARDS: List[Dict[str, Any]] = [
    {
        "game_type":   "emotions",
        "title":       "Feelings Game",
        "icon":        "😊",
        "description": "Identify emotions!",
        "accent":      (252, 211,  77),   # amber
        "xp_reward":   10,
    },
    {
        "game_type":   "memory_match",
        "title":       "Memory Match",
        "icon":        "🃏",
        "description": "Find matching pairs!",
        "accent":      ( 99, 102, 241),   # indigo
        "xp_reward":   15,
    },
    {
        "game_type":   "speech",
        "title":       "Echo Game",
        "icon":        "🎙️",
        "description": "Repeat what I say!",
        "accent":      ( 52, 211, 153),   # emerald
        "xp_reward":   10,
    },
    {
        "game_type":   "imitation",
        "title":       "Copy Cat",
        "icon":        "🤸",
        "description": "Copy my moves!",
        "accent":      (236,  72, 153),   # pink
        "xp_reward":   20,
    },
    {
        "game_type":   "social_skills",
        "title":       "Friend Skills",
        "icon":        "🤝",
        "description": "Be a good friend!",
        "accent":      ( 56, 189, 212),   # teal
        "xp_reward":   10,
    },
    {
        "game_type":   "colors",
        "title":       "Colors Game",
        "icon":        "🎨",
        "description": "Identify colors!",
        "accent":      (167,  85, 247),   # violet
        "xp_reward":    8,
    },
]


class GamesDashboard:
    """
    Pygame overlay that renders the games dashboard on top of the screen.

    Usage:
        dashboard = GamesDashboard(screen_width, screen_height)
        dashboard.set_child(child_id)   # call when session starts
        dashboard.handle_event(pygame_event)
        dashboard.draw(surface, tick)
    """

    def __init__(self, width: int, height: int):
        self.width  = width
        self.height = height

        self.active  = False           # is the dashboard showing?
        self.child_id: Optional[int] = None

        # Stats loaded from DB
        self._last_scores: Dict[str, Optional[float]] = {g["game_type"]: None for g in GAME_CARDS}
        self._total_xp: int = 0
        self._total_stars: int = 0

        self._hovered_card: Optional[int] = None   # index into GAME_CARDS
        self._selected_card: Optional[int] = None  # briefly highlighted on click

        self._card_rects: List[pygame.Rect] = []   # computed in draw()
        self._launching: bool = False              # debounce guard

        # Particle system for background sparkle
        self._particles: List[Dict] = self._init_particles(30)

        # Cache fonts to prevent OS queries in the hot loop
        try:
            self.font_title_lg = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 32, bold=True)
            self.font_title_sm = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 18)
            self.font_card_title = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 16, bold=True)
            self.font_card_desc  = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 13)
            self.font_card_icon  = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 36)
            self.font_card_xp    = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 12)
            self.font_bottom     = pygame.font.SysFont("segoeuiemoji,segoeui,Arial", 18, italic=True)
        except Exception:
            self.font_title_lg = pygame.font.SysFont(None, 32)
            self.font_title_sm = pygame.font.SysFont(None, 18)
            self.font_card_title = pygame.font.SysFont(None, 16)
            self.font_card_desc  = pygame.font.SysFont(None, 13)
            self.font_card_icon  = pygame.font.SysFont(None, 36)
            self.font_card_xp    = pygame.font.SysFont(None, 12)
            self.font_bottom     = pygame.font.SysFont(None, 18)
        
        # Star font
        try:
            self.font_star = pygame.font.SysFont("segoeuiemoji,Arial", 16)
        except Exception:
            self.font_star = pygame.font.SysFont(None, 16)

        # Subscribe to events
        event_bus.subscribe("ui.screen.change",  self._on_screen_change)
        event_bus.subscribe("ui.show_dashboard", self._on_show_dashboard)
        event_bus.subscribe("session.started",   self._on_session_started)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_screen_change(self, _event: str, screen_name: str):
        if screen_name == "games_dashboard":
            self.active = True
            self._load_stats()
        elif screen_name == "face":
            self.active = False

    async def _on_show_dashboard(self, _event: str, data: Any):
        self.active = True
        self._load_stats()

    async def _on_session_started(self, _event: str, data: dict):
        self.child_id = data.get("child_id")
        self._load_stats()

    # ── Database ──────────────────────────────────────────────────────────────

    def set_child(self, child_id: int):
        """Call when a child session starts."""
        self.child_id = child_id
        self._load_stats()

    def _load_stats(self):
        """Load per-game last scores and overall XP/stars from SQLite."""
        if self.child_id is None:
            return
        try:
            with db.get_cursor() as cursor:
                # Last score per game type
                for card in GAME_CARDS:
                    cursor.execute(
                        """
                        SELECT score FROM game_results
                        WHERE child_id=? AND game_type=?
                        ORDER BY played_at DESC LIMIT 1
                        """,
                        (self.child_id, card["game_type"]),
                    )
                    row = cursor.fetchone()
                    self._last_scores[card["game_type"]] = row[0] if row else None

                # Total tokens (used as star proxy)
                cursor.execute(
                    "SELECT SUM(amount) FROM rewards WHERE child_id=? AND reward_type='token'",
                    (self.child_id,),
                )
                row = cursor.fetchone()
                self._total_stars = int(row[0] or 0)
                self._total_xp = self._total_stars * 5
        except Exception as e:
            logger.warning(f"[GamesDashboard] Could not load stats: {e}")

    # ── Input ─────────────────────────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event):
        """Call from the main Pygame event loop."""
        if not self.active:
            return

        if event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            self._hovered_card = None
            for i, rect in enumerate(self._card_rects):
                if rect.collidepoint(mx, my):
                    self._hovered_card = i
                    break

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._launching:
                return  # Already launching, ignore extra clicks
            for i, rect in enumerate(self._card_rects):
                if rect.collidepoint(mx, my):
                    self._selected_card = i
                    self._launching = True
                    asyncio.create_task(self._launch_game(i))
                    break

    # ── Game launch ───────────────────────────────────────────────────────────

    async def _launch_game(self, card_index: int):
        """Publish a game launch event and close dashboard."""
        try:
            card = GAME_CARDS[card_index]
            logger.info(f"[GamesDashboard] Launching: {card['game_type']}")
            self.active = False
            await event_bus.publish("game.launch", {
                "game_type": card["game_type"],
                "child_id":  self.child_id,
            })
        finally:
            self._launching = False

    # ── Rendering ─────────────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface, tick: float):
        """Draw the full dashboard onto `surface`. Called every frame."""
        if not self.active:
            return

        import asyncio  # needed for create_task in handle_event

        # Background
        surface.fill(BG_COLOR)
        self._draw_particles(surface, tick)

        # Header
        self._draw_header(surface, tick)

        # Game cards
        self._draw_cards(surface, tick)

        # Bottom bar
        self._draw_bottom_bar(surface)

    # ── Sub-renderers ─────────────────────────────────────────────────────────

    def _draw_header(self, surface: pygame.Surface, tick: float):
        # Pulsing title glow
        pulse = 0.85 + 0.15 * math.sin(tick * 2.0)
        r = int(ACCENT_GOLD[0] * pulse)
        g = int(ACCENT_GOLD[1] * pulse)
        b = int(ACCENT_GOLD[2] * pulse)

        title = self.font_title_lg.render("✨  BMO's Game Room  ✨", True, (r, g, b))
        surface.blit(title, title.get_rect(center=(self.width // 2, 30)))

        # XP & Stars row
        xp_text   = self.font_title_sm.render(f"⭐  {self._total_stars} Stars", True, ACCENT_GOLD)
        xp_xp     = self.font_title_sm.render(f"⚡  {self._total_xp} XP",    True, ACCENT_TEAL)
        surface.blit(xp_text, (self.width - 230, 16))
        surface.blit(xp_xp,   (self.width - 130, 16))

    def _draw_cards(self, surface: pygame.Surface, tick: float):
        """Draw a grid of game cards."""
        cols     = 3
        rows     = 2
        pad      = 12
        top      = 60
        card_w   = (self.width - (cols + 1) * pad) // cols
        card_h   = (self.height - top - 50 - (rows + 1) * pad) // rows

        self._card_rects = []

        for idx, card in enumerate(GAME_CARDS):
            col = idx % cols
            row = idx // cols
            x   = pad + col * (card_w + pad)
            y   = top + pad + row * (card_h + pad)

            rect = pygame.Rect(x, y, card_w, card_h)
            self._card_rects.append(rect)

            # Hover / selected effects
            is_hovered  = self._hovered_card == idx
            is_selected = self._selected_card == idx

            # Card shadow
            shadow = pygame.Surface((card_w + 6, card_h + 6), pygame.SRCALPHA)
            shadow.fill((0, 0, 0, 60))
            surface.blit(shadow, (x + 3, y + 3))

            # Card background with accent-coloured top border
            bg = CARD_HOVER if is_hovered else CARD_BG
            pygame.draw.rect(surface, bg,         rect, border_radius=14)
            pygame.draw.rect(surface, card["accent"], rect, width=2, border_radius=14)

            # Accent top stripe
            stripe = pygame.Rect(x, y, card_w, 5)
            pygame.draw.rect(surface, card["accent"], stripe,
                             border_radius=14)

            # Icon
            try:
                icon_surf = self.font_card_icon.render(card["icon"], True, TEXT_LIGHT)
                surface.blit(icon_surf, icon_surf.get_rect(center=(x + card_w // 2, y + 36)))
            except Exception:
                pass

            # Text
            title_surf = self.font_card_title.render(card["title"], True, TEXT_WHITE)
            desc_surf  = self.font_card_desc.render(card["description"], True, TEXT_DIM)
            surface.blit(title_surf, title_surf.get_rect(center=(x + card_w // 2, y + 74)))
            surface.blit(desc_surf,  desc_surf.get_rect(center=(x + card_w // 2, y + 92)))

            # XP Pill at bottom right
            xp_w = 40
            xp_h = 20
            xp_rect = pygame.Rect(x + card_w - xp_w - 6, y + card_h - xp_h - 6, xp_w, xp_h)
            pygame.draw.rect(surface, BG_COLOR, xp_rect, border_radius=8)
            xp_text_surf = self.font_card_xp.render(f"+{card['xp_reward']} XP", True, ACCENT_GOLD)
            surface.blit(xp_text_surf, xp_text_surf.get_rect(center=xp_rect.center))

            # Star rating (last score → 0-5 stars)
            last = self._last_scores.get(card["game_type"])
            stars = round((last or 0) * 5) if last is not None else 0
            self._draw_stars(surface, x + 10, y + card_h - 28, stars, 5, 10)

    def _draw_stars(self, surface: pygame.Surface, x: int, y: int,
                    filled: int, total: int, size: int):
        for i in range(total):
            color  = STAR_ON if i < filled else STAR_OFF
            s = self.font_star.render("★", True, color)
            surface.blit(s, (x + i * (size + 3), y))

    def _draw_bottom_bar(self, surface: pygame.Surface):
        prompt = "🎙️ 'Hi BMO, let's play...'    Tap a card or say the name to start"
        surf = self.font_bottom.render(prompt, True, TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=(self.width // 2, self.height - 20)))

    # ── Particles ─────────────────────────────────────────────────────────────

    def _init_particles(self, n: int) -> List[Dict]:
        import random as _random
        return [
            {
                "x": _random.uniform(0, self.width),
                "y": _random.uniform(0, self.height),
                "speed": _random.uniform(0.2, 0.8),
                "size": _random.randint(2, 5),
                "alpha": _random.randint(40, 120),
            }
            for _ in range(n)
        ]

    def _draw_particles(self, surface: pygame.Surface, tick: float):
        psurf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        for p in self._particles:
            # Slowly drift upward, reset at top
            p["y"] -= p["speed"]
            if p["y"] < 0:
                p["y"] = self.height
            alpha = int(p["alpha"] * (0.6 + 0.4 * math.sin(tick + p["x"])))
            pygame.draw.circle(psurf, (255, 255, 255, alpha),
                               (int(p["x"]), int(p["y"])), p["size"])
        surface.blit(psurf, (0, 0))


# Top-level asyncio import needed for create_task inside sync handle_event
import asyncio
