import logging
import json
import re
import math
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from robot.database.connection import db

logger = logging.getLogger(__name__)

# ── Topic extraction keyword map ─────────────────────────────────────────────
INTEREST_KEYWORDS: Dict[str, List[str]] = {
    "animals": ["dog", "cat", "lion", "elephant", "fish", "bird", "horse", "rabbit", "monkey", "turtle"],
    "space":   ["space", "rocket", "moon", "stars", "planet", "astronaut", "galaxy"],
    "vehicles": ["car", "truck", "train", "airplane", "bus", "boat", "helicopter"],
    "food":    ["pizza", "cake", "fruit", "chocolate", "ice cream", "sandwich", "pasta"],
    "sports":  ["football", "soccer", "basketball", "swimming", "running", "bike"],
    "music":   ["song", "music", "dance", "sing", "drum", "guitar", "piano"],
    "art":     ["draw", "paint", "color", "crayon", "picture", "art"],
    "stories": ["story", "book", "read", "fairy tale", "adventure", "hero"],
    "games":   ["game", "play", "lego", "puzzle", "toy", "minecraft", "roblox"],
    "nature":  ["tree", "flower", "rain", "sun", "ocean", "beach", "mountain"],
}

# ── Memory decay constants (spaced repetition) ───────────────────────────────
# Importance decays by 0.05 per day of non-reference, floor 0.1
DECAY_RATE_PER_DAY = 0.05
DECAY_FLOOR        = 0.1


class LongTermMemory:
    """
    SQLite-backed long-term memory with:
    - Auto-extraction of interests from conversation text
    - Spaced-repetition importance scoring (decay & boost)
    - Domain-categorised memory retrieval
    - End-of-session consolidation helper
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def add_memory(
        self,
        child_id:   int,
        memory_type: str,       # interest | preference | milestone | note | observation
        content:    str,
        category:   str = None, # domain label  (social/emotional/speech/…)
        importance: float = 0.5,
    ):
        """Insert a single memory entry, avoiding exact duplicates."""
        try:
            with db.get_cursor() as cursor:
                # Dedup: skip if identical content exists for this child
                cursor.execute(
                    "SELECT id FROM memories WHERE child_id=? AND content=?",
                    (child_id, content),
                )
                if cursor.fetchone():
                    # Bump importance instead of duplicating
                    self._boost_memory(cursor, child_id, content, 0.1)
                    return

                cursor.execute(
                    """
                    INSERT INTO memories (child_id, memory_type, category, content, importance)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (child_id, memory_type, category, content, min(importance, 1.0)),
                )
            logger.debug(f"[Memory] +{memory_type}/{category}: {content[:60]}")
        except Exception as e:
            logger.error(f"[Memory] add_memory error: {e}")

    def extract_memories_from_text(self, child_id: int, text: str) -> List[str]:
        """
        Scan a piece of conversation text for implicit interests and auto-store them.
        Returns list of newly discovered category labels.
        """
        discovered = []
        text_lower = text.lower()

        for category, keywords in INTEREST_KEYWORDS.items():
            for kw in keywords:
                if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                    content = f"Showed interest in: {kw} ({category})"
                    self.add_memory(
                        child_id,
                        memory_type="interest",
                        content=content,
                        category=category,
                        importance=0.6,
                    )
                    if category not in discovered:
                        discovered.append(category)
                    break  # one match per category per utterance

        return discovered

    def get_child_profile(self, child_id: int) -> Optional[Dict]:
        """Fetch base child profile row with all JSON arrays parsed."""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT * FROM children WHERE id=?", (child_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                profile = dict(row)
                # Parse JSON fields for convenience
                for field in ["preferred_games", "favorite_topics", "favorite_animals", "sensory_preferences"]:
                    try:
                        profile[field] = json.loads(profile.get(field) or "[]")
                    except Exception:
                        profile[field] = []
                return profile
        except Exception as e:
            logger.error(f"[Memory] get_child_profile error: {e}")
            return None

    def get_memories(
        self,
        child_id:    int,
        memory_type: str  = None,
        category:    str  = None,
        limit:       int  = 10,
        min_importance: float = 0.0,
    ) -> List[Dict]:
        """
        Fetch memories ordered by decayed importance (recent + relevant first).
        Applies temporal decay before returning so callers always see live scores.
        """
        try:
            self._apply_decay(child_id)          # update importance scores

            query  = "SELECT * FROM memories WHERE child_id=?"
            params: list = [child_id]

            if memory_type:
                query  += " AND memory_type=?"
                params.append(memory_type)
            if category:
                query  += " AND category=?"
                params.append(category)
            if min_importance > 0:
                query  += " AND importance>=?"
                params.append(min_importance)

            query += " ORDER BY importance DESC, last_referenced DESC LIMIT ?"
            params.append(limit)

            with db.get_cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()
                # Touch last_referenced for top memories
                ids = [r["id"] for r in rows[:5]]
                if ids:
                    placeholders = ",".join(["?"] * len(ids))
                    cursor.execute(
                        f"UPDATE memories SET last_referenced=CURRENT_TIMESTAMP "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"[Memory] get_memories error: {e}")
            return []

    def get_context_for_child(self, child_id: int) -> str:
        """
        Build a compact context string for the LLM system prompt.
        Pulls: profile basics + top interests + key milestones + observations.
        """
        profile = self.get_child_profile(child_id)
        if not profile:
            return ""

        parts: List[str] = []

        # ── Basic profile ────────────────────────────────────────────────────
        age_str = f", age {profile['age']}" if profile.get("age") else ""
        parts.append(f"Child: {profile['name']}{age_str}.")
        parts.append(f"Communication level: {profile['communication_level']}.")

        # ── Stored topic interests (JSON column) ─────────────────────────────
        try:
            fav = profile.get("favorite_topics") or []
            if isinstance(fav, str):
                fav = json.loads(fav)
            if fav:
                parts.append(f"Favourite topics: {', '.join(fav)}.")
        except Exception:
            pass

        # ── Favourite animals ────────────────────────────────────────────────
        try:
            animals = profile.get("favorite_animals") or []
            if isinstance(animals, str):
                animals = json.loads(animals)
            if animals:
                parts.append(f"Favourite animals: {', '.join(animals)}.")
        except Exception:
            pass

        # ── Sensory preferences ──────────────────────────────────────────────
        try:
            sensory = profile.get("sensory_preferences") or []
            if isinstance(sensory, str):
                sensory = json.loads(sensory)
            if sensory:
                parts.append(f"Sensory preferences: {', '.join(sensory)}.")
        except Exception:
            pass

        # ── Auto-discovered interests ────────────────────────────────────────
        interests = self.get_memories(child_id, memory_type="interest", limit=6, min_importance=0.3)
        if interests:
            labels = list({m["category"] for m in interests if m.get("category")})
            if labels:
                parts.append(f"Observed interests: {', '.join(labels)}.")

        # ── Milestones ───────────────────────────────────────────────────────
        milestones = self.get_memories(child_id, memory_type="milestone", limit=3)
        if milestones:
            parts.append("Recent milestones: " + "; ".join(m["content"] for m in milestones) + ".")

        # ── Important observations ───────────────────────────────────────────
        notes = self.get_memories(
            child_id, memory_type="observation", limit=3, min_importance=0.5
        )
        if notes:
            parts.append(
                "Therapist notes: " + "; ".join(m["content"] for m in notes) + "."
            )

        return "\n".join(parts)

    def add_milestone(self, child_id: int, milestone: str, importance: float = 0.8):
        """Shortcut for recording a therapy milestone."""
        self.add_memory(child_id, "milestone", milestone, category="therapy", importance=importance)

    def consolidate_session(self, child_id: int, session_summary: str):
        """
        Store an end-of-session observation note. Called by TherapyEngine at session end.
        Also extracts interests from the summary text.
        """
        self.add_memory(
            child_id,
            memory_type="observation",
            content=session_summary,
            category="session",
            importance=0.6,
        )
        self.extract_memories_from_text(child_id, session_summary)

    def get_interests_by_domain(self, child_id: int) -> Dict[str, int]:
        """Return a domain → mention_count map for the dashboard."""
        memories = self.get_memories(child_id, memory_type="interest", limit=100)
        counts: Dict[str, int] = {}
        for m in memories:
            cat = m.get("category", "other")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _apply_decay(self, child_id: int):
        """
        Apply temporal importance decay to unreferenced memories.
        Memories not referenced in N days lose DECAY_RATE_PER_DAY * N importance.
        """
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE memories
                    SET importance = MAX(
                        ?,
                        importance - ? * CAST(
                            (julianday('now') - julianday(
                                COALESCE(last_referenced, created_at)
                            )) AS REAL
                        )
                    )
                    WHERE child_id = ? AND importance > ?
                    """,
                    (DECAY_FLOOR, DECAY_RATE_PER_DAY, child_id, DECAY_FLOOR),
                )
        except Exception as e:
            logger.error(f"[Memory] decay error: {e}")

    def _boost_memory(self, cursor, child_id: int, content: str, boost: float):
        """Increase importance of an existing memory (dedup boost)."""
        cursor.execute(
            """
            UPDATE memories
            SET importance = MIN(1.0, importance + ?),
                last_referenced = CURRENT_TIMESTAMP
            WHERE child_id=? AND content=?
            """,
            (boost, child_id, content),
        )
