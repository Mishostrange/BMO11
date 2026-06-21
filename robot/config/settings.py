import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / ".env")

@dataclass
class AudioConfig:
    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1
    CHUNK_SIZE: int = 512
    DTYPE: str = 'float32'

@dataclass
class VADConfig:
    THRESHOLD: float = 0.4
    MIN_SILENCE_DURATION_MS: int = 1000  # Allow longer pauses mid-sentence (was 250)
    SPEECH_PAD_MS: int = 300             # Capture more trailing audio (was 50)

@dataclass
class LLMConfig:
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    PRIMARY_MODEL: str = "llama-3.3-70b-versatile"
    FALLBACK_MODEL: str = "gemini-2.5-flash"

@dataclass
class DatabaseConfig:
    PATH: str = str(BASE_DIR / "data" / "bmo.db")
    VECTOR_DB_PATH: str = str(BASE_DIR / "data" / "chromadb")

@dataclass
class UIConfig:
    WIDTH: int = 800
    HEIGHT: int = 480
    FPS: int = 30
    FULLSCREEN: bool = False

@dataclass
class PerceptionConfig:
    CAMERA_INDEX: int = 0
    FPS: int = 15
    FACE_DETECTION_CONFIDENCE: float = 0.5
    ENGAGEMENT_THRESHOLD: float = 0.4

@dataclass
class DashboardConfig:
    PORT: int = 5000
    SECRET_KEY: str = os.environ.get("DASHBOARD_SECRET", "bmo-secret-change-me")

@dataclass
class BmoSettings:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

# Global instance
settings = BmoSettings()
