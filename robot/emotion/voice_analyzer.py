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

NOTE: Pure numpy/scipy implementation — NO librosa/numba dependency.
      This ensures compatibility with any NumPy version (including 2.5+).
"""

import asyncio
import logging
import numpy as np
from scipy.fft import fft
from scipy.signal import get_window
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

# ── Pure-numpy DSP helpers ────────────────────────────────────────────────────

def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)

def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 40) -> np.ndarray:
    """Build a mel filterbank matrix (n_mels, n_fft//2+1)."""
    low_hz   = 0.0
    high_hz  = sr / 2.0
    low_mel  = _hz_to_mel(np.array([low_hz]))[0]
    high_mel = _hz_to_mel(np.array([high_hz]))[0]
    mel_pts  = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_pts   = _mel_to_hz(mel_pts)
    bin_pts  = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        lo, cen, hi = bin_pts[m-1], bin_pts[m], bin_pts[m+1]
        for k in range(lo, cen):
            if cen != lo:
                fb[m-1, k] = (k - lo) / (cen - lo)
        for k in range(cen, hi):
            if hi != cen:
                fb[m-1, k] = (hi - k) / (hi - cen)
    return fb

def _stft_magnitude(y: np.ndarray, n_fft: int, hop_length: int) -> np.ndarray:
    """Compute STFT magnitude (n_fft//2+1, frames)."""
    window = get_window("hann", n_fft, fftbins=True).astype(np.float32)
    frames = []
    for start in range(0, max(1, len(y) - n_fft + 1), hop_length):
        frame = y[start:start + n_fft]
        if len(frame) < n_fft:
            frame = np.pad(frame, (0, n_fft - len(frame)))
        frames.append(np.abs(fft(frame * window)[:n_fft // 2 + 1]))
    return np.column_stack(frames) if frames else np.zeros((n_fft // 2 + 1, 1))

def _compute_mfcc(y: np.ndarray, sr: int, n_mfcc: int,
                  n_fft: int, hop_length: int) -> np.ndarray:
    """Compute MFCCs (n_mfcc, frames) using pure numpy/scipy."""
    mag  = _stft_magnitude(y, n_fft, hop_length)
    fb   = _mel_filterbank(sr, n_fft, n_mels=40)
    mel  = np.maximum(1e-10, fb @ mag)
    log_mel = np.log(mel)
    # DCT-II
    n_mels = log_mel.shape[0]
    dct = np.cos(np.pi / n_mels * np.outer(np.arange(n_mfcc), np.arange(0.5, n_mels)))
    return dct @ log_mel

def _delta(data: np.ndarray, width: int = 9) -> np.ndarray:
    """Simple delta (first derivative) of feature matrix."""
    pad = width // 2
    padded = np.pad(data, ((0, 0), (pad, pad)), mode="edge")
    slope = np.arange(-pad, pad + 1, dtype=np.float64)
    norm = (slope ** 2).sum() or 1.0
    return np.array([np.convolve(padded[i], slope[::-1], "valid") / norm
                     for i in range(data.shape[0])])

def _estimate_pitch(y: np.ndarray, sr: int,
                    fmin: float = 65.0, fmax: float = 2100.0) -> float:
    """Lightweight autocorrelation-based pitch estimate (no numba)."""
    y = y - y.mean()
    n = len(y)
    if n < 2:
        return 0.0
    lag_min = max(1, int(sr / fmax))
    lag_max = min(n - 1, int(sr / fmin))
    if lag_min >= lag_max:
        return 0.0
    corr = np.correlate(y, y, mode="full")[n - 1:]
    peak_lag = lag_min + int(np.argmax(corr[lag_min:lag_max]))
    return float(sr / peak_lag) if peak_lag > 0 else 0.0


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

        # ── MFCCs (pure numpy/scipy) ──────────────────────────────────────────
        mfcc       = _compute_mfcc(y, sr, N_MFCC, N_FFT, HOP_LENGTH)
        mfcc_delta = _delta(mfcc)
        mfcc_mean  = mfcc.mean(axis=1)
        delta_mean = mfcc_delta.mean(axis=1)

        # ── Energy (RMS) ──────────────────────────────────────────────────────
        mag      = _stft_magnitude(y, N_FFT, HOP_LENGTH)
        rms      = np.sqrt(np.mean(mag ** 2, axis=0))
        mean_rms = float(np.mean(rms))
        rms_var  = float(np.var(rms))

        # ── Spectral centroid (brightness) ────────────────────────────────────
        freqs     = np.linspace(0, sr / 2, mag.shape[0])
        mag_sum   = mag.sum(axis=0)
        mag_sum   = np.where(mag_sum == 0, 1e-10, mag_sum)
        centroid  = (freqs[:, None] * mag).sum(axis=0) / mag_sum
        mean_cent = float(np.mean(centroid))

        # ── Zero-crossing rate ────────────────────────────────────────────────
        frames_zcr = []
        for start in range(0, max(1, len(y) - HOP_LENGTH), HOP_LENGTH):
            frame = y[start:start + HOP_LENGTH]
            frames_zcr.append(float(np.mean(np.abs(np.diff(np.sign(frame)))) / 2))
        mean_zcr = float(np.mean(frames_zcr)) if frames_zcr else 0.0

        # ── Pitch (autocorrelation, no numba) ────────────────────────────────
        mean_pitch = _estimate_pitch(y, sr)
        pitch_std  = 0.0   # single estimate; std not available without per-frame

        return {
            "mean_rms":    mean_rms,
            "rms_var":     rms_var,
            "mean_pitch":  mean_pitch,
            "pitch_std":   pitch_std,
            "mean_cent":   mean_cent,
            "mean_zcr":    mean_zcr,
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
