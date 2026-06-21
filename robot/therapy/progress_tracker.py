import logging
from typing import Dict
from robot.services.event_bus import event_bus
from robot.database.connection import db

logger = logging.getLogger(__name__)

class ProgressTracker:
    """
    Subscribes to session and game events to maintain a rolling average of
    the child's attention, speech, and emotion scores, storing them in the DB.
    """
    
    def __init__(self):
        self.session_attention_scores = []
        self.session_speech_scores = []
        self.session_emotion_scores = []
        
        self.total_engagement_frames = 0
        self.eye_contact_frames = 0
        self.social_skill_scores = []
        
        event_bus.subscribe("engagement.update", self._on_engagement)
        event_bus.subscribe("speech.transcribed", self._on_speech)
        event_bus.subscribe("emotion.detected", self._on_emotion)
        event_bus.subscribe("social_skills.update", self._on_social_skill)
        event_bus.subscribe("session.ended", self._on_session_ended)
        event_bus.subscribe("session.started", self._on_session_started)

    async def _on_session_started(self, event_type: str, data: dict):
        self.session_attention_scores.clear()
        self.session_speech_scores.clear()
        self.session_emotion_scores.clear()
        self.total_engagement_frames = 0
        self.eye_contact_frames = 0
        self.social_skill_scores.clear()

    async def _on_engagement(self, event_type: str, data: dict):
        self.session_attention_scores.append(data.get("score", 0.0))
        self.total_engagement_frames += 1
        
        # Determine eye contact based on yaw and pitch
        yaw = abs(data.get("yaw", 90))
        pitch = abs(data.get("pitch", 90))
        # If looking within a 15-degree cone of the camera, consider it direct eye contact
        if yaw < 15 and pitch < 15:
            self.eye_contact_frames += 1

    async def _on_speech(self, event_type: str, text: str):
        # Very simple heuristic: more words = higher speech engagement score
        words = len(text.split())
        score = min(1.0, words / 10.0)
        self.session_speech_scores.append(score)

    async def _on_emotion(self, event_type: str, data: dict):
        emotion = data.get("emotion", "neutral")
        if emotion in ["happy", "excited"]:
            score = 1.0
        elif emotion in ["sad", "angry", "frustrated"]:
            score = 0.0
        else:
            score = 0.5
        self.session_emotion_scores.append(score)

    async def _on_social_skill(self, event_type: str, data: dict):
        # Emitted by the social skills game
        score = data.get("score", 0.0)
        self.social_skill_scores.append(score)

    async def _on_session_ended(self, event_type: str, data: dict):
        child_id = data.get("child_id")
        session_id = data.get("session_id")
        
        if not child_id:
            return
            
        avg_attention = sum(self.session_attention_scores) / len(self.session_attention_scores) if self.session_attention_scores else 0.5
        avg_speech = sum(self.session_speech_scores) / len(self.session_speech_scores) if self.session_speech_scores else 0.5
        avg_emotion = sum(self.session_emotion_scores) / len(self.session_emotion_scores) if self.session_emotion_scores else 0.5
        avg_eye_contact = (self.eye_contact_frames / self.total_engagement_frames) if self.total_engagement_frames > 0 else 0.0
        avg_social = sum(self.social_skill_scores) / len(self.social_skill_scores) if self.social_skill_scores else 0.5
        
        # 1. Save session summary
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE sessions 
                    SET attention_score = ?, speech_score = ?, engagement_score = ?, eye_contact_score = ?, social_skill_score = ?
                    WHERE id = ?
                    """,
                    (avg_attention, avg_speech, avg_emotion, avg_eye_contact, avg_social, session_id)
                )
                
                # 2. Update child lifetime moving averages (simple EMA)
                cursor.execute("SELECT attention_score, speech_score FROM children WHERE id = ?", (child_id,))
                row = cursor.fetchone()
                if row:
                    old_att, old_spc = row
                    new_att = (old_att * 0.8) + (avg_attention * 0.2)
                    new_spc = (old_spc * 0.8) + (avg_speech * 0.2)
                    
                    cursor.execute(
                        "UPDATE children SET attention_score = ?, speech_score = ? WHERE id = ?",
                        (new_att, new_spc, child_id)
                    )
            logger.info(f"Progress Tracker updated for child {child_id}. Att: {new_att:.2f}, Spc: {new_spc:.2f}")
        except Exception as e:
            logger.error(f"Failed to save progress: {e}")
