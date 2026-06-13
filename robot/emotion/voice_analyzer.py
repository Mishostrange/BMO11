"""
emotion/voice_analyzer.py
─────────────────────────────────────────────────────────────────────────────
Real-time voice emotion analysis using MFCC + spectral features.

Pipeline
────────
1. Receive full speech buffer from VAD ('speech.ended')
2. Extract feature vector: MFCCs, delta-MFCCs, energy, pitch, spectral centroid
3. Apply lightweight heuristic classifier (RPi5-safe, no heavy model loading)
4. Publish 'emotion.voice' event
5. Log result to DB emotion_log table
6. Feed result into ShortTermMemory rolling window

Future upgrade: swap heuristic classifier with a TFLite SER model for
accuracy without sacrificing RPi5 performance.
"""

import asyncio
import logging
import numpy as np
import librosa
from typing import Tuple, Dict

from robot.services.event_bus import event_bus
from robot.database.connection import db

logger = logging.getLogger(__name__)

# ── Emotion label index (maps to internal classifier output) ─────────────────
EMOTIONS = ["neutral", "happy", "sad", "angry", "scared", "excited"]

# ── Feature extraction config ────────────────────────────────────────────────
N_MFCC       = 13    # MFCCs to extract
HOP_LENGTH   = 512
N_FFT        = 1024


class VoiceAnalyzer:
    """
    Analyzes speech audio buffers for emotional content.
    Uses MFCC + delta + energy + pitch heuristics (no heavy ML model).
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        # Link to STM for live emotion state (injected after construction)
        self.stm = None
        self.active_session_id: int | None = None
        self.active_child_id:   int | None = None

        event_bus.subscribe("speech.ended",    self._on_speech_ended)
        event_bus.subscribe("session.started", self._on_session_started)
        event_bus.subscribe("session.ended",   self._on_session_ended)

    # ── Event wiring ──────────────────────────────────────────────────────────

    async def _on_session_started(self, _event: str, data: Dict):
        self.active_session_id = data.get("session_id")
        self.active_child_id   = data.get("child_id")

    async def _on_session_ended(self, _event: str, _data: Dict):
        self.active_session_id = None
        self.active_child_id   = None

    async def _on_speech_ended(self, _event: str, audio_data: np.ndarray):
        """Entry point: analyse audio, publish result, update STM, log to DB."""
        loop = asyncio.get_running_loop()

        try:
            # Run CPU-bound extraction in thread pool
            emotion, confidence, features = await loop.run_in_executor(
                None, self._extract_and_classify, audio_data
            )
        except Exception as e:
            logger.error(f"[VoiceAnalyzer] Extraction error: {e}")
            return

        payload = {
            "emotion":    emotion,
            "confidence": confidence,
            "features":   features,
        }

        # 1. Publish
        await event_bus.publish("emotion.voice", payload)

        # 2. Update STM rolling window
        if self.stm is not None:
            self.stm.set_emotion(emotion, confidence)

        # 3. Log to DB (non-blocking)
        if self.active_child_id:
            loop.run_in_executor(
                None,
                self._log_to_db,
                self.active_child_id,
                self.active_session_id,
                "voice",
                emotion,
                confidence,
            )

    # ── Feature extraction ────────────────────────────────────────────────────

    def _extract_features(self, y: np.ndarray) -> Dict[str, float]:
        """Extract a compact feature dict from a raw audio array."""
        # Ensure float32 and minimum length
        y = y.astype(np.float32).flatten()
        if len(y) < N_FFT:
            y = np.pad(y, (0, N_FFT - len(y)))

        sr = self.sample_rate

        # ── MFCCs ─────────────────────────────────────────────────────────────
        mfcc = librosa.feature.mfcc(
            y=y, sr=sr, n_mfcc=N_MFCC,
            hop_length=HOP_LENGTH, n_fft=N_FFT
        )
        mfcc_delta  = librosa.feature.delta(mfcc)
        mfcc_mean   = mfcc.mean(axis=1)
        delta_mean  = mfcc_delta.mean(axis=1)

        # ── Energy (RMS) ──────────────────────────────────────────────────────
        rms       = librosa.feature.rms(y=y, hop_length=HOP_LENGTH)[0]
        mean_rms  = float(np.mean(rms))
        rms_var   = float(np.var(rms))

        # ── Spectral centroid (brightness) ────────────────────────────────────
        centroid  = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=N_FFT)[0]
        mean_cent = float(np.mean(centroid))

        # ── Zero-crossing rate (noisiness / fricatives) ───────────────────────
        zcr      = librosa.feature.zero_crossing_rate(y, hop_length=HOP_LENGTH)[0]
        mean_zcr = float(np.mean(zcr))

        # ── Pitch (F0) ────────────────────────────────────────────────────────
        try:
            f0, voiced_flag, _ = librosa.pyin(
                y,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sr,
            )
            valid_f0   = f0[voiced_flag] if voiced_flag is not None else np.array([])
            mean_pitch = float(np.mean(valid_f0)) if len(valid_f0) > 0 else 0.0
            pitch_std  = float(np.std(valid_f0))  if len(valid_f0) > 0 else 0.0
        except Exception:
            mean_pitch = 0.0
            pitch_std  = 0.0

        return {
            # Raw summary stats
            "mean_rms":    mean_rms,
            "rms_var":     rms_var,
            "mean_pitch":  mean_pitch,
            "pitch_std":   pitch_std,
            "mean_cent":   mean_cent,
            "mean_zcr":    mean_zcr,
            # MFCC statistics (first 4 coefficients are most discriminative)
            "mfcc_0":      float(mfcc_mean[0]),
            "mfcc_1":      float(mfcc_mean[1]),
            "mfcc_2":      float(mfcc_mean[2]),
            "mfcc_3":      float(mfcc_mean[3]),
            "delta_mfcc_0": float(delta_mean[0]),
            "delta_mfcc_1": float(delta_mean[1]),
        }

    def _classify(self, f: Dict[str, float]) -> Tuple[str, float]:
        """
        Heuristic rule-based classifier.
        Rules are tuned for children's speech on a 16 kHz mono mic.

        Returns (emotion_label, confidence 0-1).
        """
        rms   = f["mean_rms"]
        pitch = f["mean_pitch"]
        pstd  = f["pitch_std"]
        cent  = f["mean_cent"]
        zcr   = f["mean_zcr"]

        # ── High energy zone ──────────────────────────────────────────────────
        if rms > 0.06:
            if pstd > 60 and cent > 2500:
                return "excited", 0.75      # High variability + bright spectrum
            if pitch > 280 and pstd > 40:
                return "happy",   0.70      # High stable pitch
            if pitch > 320 and rms > 0.09:
                return "angry",   0.72      # Very high pitch + loud
            if zcr > 0.15:
                return "excited", 0.60      # High ZCR = rapid articulation

        # ── Low energy zone (silence / whisper) ─────────────────────────────
        # IMPORTANT: rms < 0.015 is effectively silence or ambient room noise.
        # Never classify silence as 'sad' — only classify if there is clear voiced pitch.
        if rms < 0.015:
            if pitch > 50 and pitch < 180 and pstd > 5:
                return "sad",     0.58      # Quiet + clearly voiced low pitch
            return "neutral",  0.52         # Ambient noise → neutral, not sad

        # ── Mid energy ───────────────────────────────────────────────────────
        if 0.015 <= rms <= 0.06:
            if pstd < 20 and 150 < pitch < 260:
                return "neutral", 0.70
            if pitch > 260:
                return "happy",   0.60
            if pitch > 50 and pitch < 150 and rms < 0.03:
                return "sad",     0.58      # Only sad if there is actual speech (pitch > 50)

        return "neutral", 0.55

    def _extract_and_classify(
        self, audio_data: np.ndarray
    ) -> Tuple[str, float, Dict[str, float]]:
        """Combined extraction + classification (runs in thread executor)."""
        features          = self._extract_features(audio_data)
        emotion, confidence = self._classify(features)
        logger.debug(
            f"[VoiceAnalyzer] {emotion} ({confidence:.2f}) | "
            f"rms={features['mean_rms']:.3f} pitch={features['mean_pitch']:.1f} "
            f"pstd={features['pitch_std']:.1f}"
        )
        return emotion, confidence, features

    # ── DB logging ────────────────────────────────────────────────────────────

    def _log_to_db(
        self,
        child_id:   int,
        session_id: int | None,
        source:     str,
        emotion:    str,
        confidence: float,
    ):
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO emotion_log
                        (child_id, session_id, source, emotion, confidence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (child_id, session_id, source, emotion, confidence),
                )
        except Exception as e:
            logger.error(f"[VoiceAnalyzer] DB log error: {e}")
