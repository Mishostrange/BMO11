import asyncio
import logging
import threading
import warnings
from werkzeug.serving import make_server

# Suppress harmless FutureWarnings from third-party libraries (e.g., insightface/scikit-image)
warnings.filterwarnings('ignore', category=FutureWarning)

# Config and Services
from robot.config.logging_config import setup_logging
from robot.config.settings import settings
from robot.services.event_bus import event_bus
from robot.services.service_registry import service_registry

# Database
from robot.database.connection import db
from robot.database.migrations import run_migrations

# Subsystems
from robot.audio.capture import AudioCapture
from robot.audio.playback import AudioPlayback
from robot.vad.silero_vad import SileroVADNode
from robot.stt.whisper_stt import FasterWhisperNode
from robot.tts.piper_tts import PiperTTSNode
from robot.llm.groq_provider import GroqProvider
from robot.llm.gemini_provider import GeminiProvider
from robot.llm.provider_manager import ProviderManager
from robot.memory.short_term import ShortTermMemory
from robot.memory.long_term import LongTermMemory
from robot.memory.vector_store import VectorMemory
from robot.memory.memory_manager import MemoryManager
from robot.therapy.session_manager import SessionManager
from robot.therapy.engine import TherapyEngine
from robot.engagement.camera import CameraManager
from robot.engagement.detector import EngagementDetector
from robot.engagement.face_recognition_service import FaceRecognitionService
from robot.engagement.gesture_recognizer import GestureRecognizer
from robot.emotion.voice_analyzer import VoiceAnalyzer
from robot.emotion.face_analyzer import FaceAnalyzer
from robot.emotion.fusion import EmotionFusion
from robot.emotion.emotion_tracker import EmotionTracker
from robot.safety.content_filter import ContentFilter
from robot.safety.distress_monitor import DistressMonitor
from robot.analytics.session_tracker import SessionTracker
from robot.analytics.progress_tracker import ProgressTracker
from robot.services.voice_commands import VoiceCommandHandler

# Games & Rewards
import robot.games.colors_game
import robot.games.emotions_game
import robot.games.speech_repeat_game
import robot.games.turn_taking_game
import robot.games.focus_game
import robot.games.social_skills_game
import robot.games.memory_match_game
import robot.games.imitation_game
import robot.therapy.sensory_regulation
from robot.rewards.reward_system import RewardSystem
from robot.rewards.badge_catalog import BadgeEngine
from robot.difficulty.adaptive import AdaptiveDifficulty
from robot.therapy.game_orchestrator import GameOrchestrator

# Companion Architecture (v3)
from robot.therapy.state_manager import state_manager, CompanionState
from robot.therapy.emotional_continuity import emotion_engine

# GUI & Dashboard
from robot.gui.face_display import FaceDisplay
from robot.dashboard.app import create_app

logger = logging.getLogger(__name__)

class BMO:
    def __init__(self):
        self.flask_server = None
        self.flask_thread = None
        self.gui = None
        self.camera = None
        self.audio_capture = None
        self.audio_playback = None
        
    async def bootstrap(self):
        """Initialize all components."""
        logger.info("Bootstrapping BMO system...")
        
        # 1. Database
        run_migrations()  # auto-applies schema + migrations
        
        # 2. Memory stack
        stm  = ShortTermMemory()
        ltm  = LongTermMemory()
        vmem = VectorMemory()
        self.memory_manager = MemoryManager(stm, ltm, vmem)
        session_manager = SessionManager()
        
        service_registry.register("stm",            stm)
        service_registry.register("ltm",            ltm)
        service_registry.register("vmem",           vmem)
        service_registry.register("memory_manager", self.memory_manager)
        service_registry.register("session_manager", session_manager)
        
        # 3. Audio & Voice Pipeline
        self.audio_capture  = AudioCapture()
        self.audio_playback = AudioPlayback()
        
        vad = SileroVADNode()
        stt = FasterWhisperNode()
        tts = PiperTTSNode(playback_node=self.audio_playback, capture_node=self.audio_capture)
        
        service_registry.register("audio_capture",  self.audio_capture)
        service_registry.register("audio_playback", self.audio_playback)
        service_registry.register("tts",            tts)
        
        # 4. LLM Setup
        groq_provider   = GroqProvider()
        gemini_provider = GeminiProvider()
        llm_manager     = ProviderManager(primary=groq_provider, fallback=gemini_provider)
        
        # 5. Therapy Engine
        self.engine = TherapyEngine(llm_manager, self.memory_manager, session_manager)
        service_registry.register("engine", self.engine)
        
        # 6. Perception & Emotion
        self.camera    = CameraManager()
        engagement     = EngagementDetector()
        face_recognition = FaceRecognitionService()
        gesture_recognizer = GestureRecognizer()
        voice_analyzer = VoiceAnalyzer()
        voice_analyzer.stm = stm   # Wire STM reference for rolling emotion window
        face_analyzer  = FaceAnalyzer()
        fusion         = EmotionFusion()
        emotion_tracker = EmotionTracker()  # session-level emotion state machine
        
        service_registry.register("emotion_tracker", emotion_tracker)
        service_registry.register("gesture_recognizer", gesture_recognizer)
        
        # 7. Safety & Tracking
        from robot.therapy.progress_tracker import ProgressTracker
        # content_filter  = ContentFilter()
        # distress_monitor = DistressMonitor()
        progress_tracker = ProgressTracker()
        
        service_registry.register("progress_tracker", progress_tracker)
        
        # 8. Games & Rewards
        rewards      = RewardSystem()
        adaptive_diff = AdaptiveDifficulty()
        
        # 8b. Game Orchestrator — wires dashboard → games → TherapyEngine
        game_orchestrator = GameOrchestrator(adaptive_diff)
        self.engine.game_orchestrator = game_orchestrator   # back-wire
        service_registry.register("game_orchestrator", game_orchestrator)
        
        # 8c. Badge Engine (auto-awards badges after game results)
        badge_engine = BadgeEngine()
        service_registry.register("badge_engine", badge_engine)
        
        # 9. Voice Commands
        voice_command_handler = VoiceCommandHandler()
        service_registry.register("voice_command_handler", voice_command_handler)

        # 10. Companion Architecture — register singletons so they are ready
        # state_manager and emotion_engine are module-level singletons;
        # importing them above is sufficient to activate their event subscriptions.
        service_registry.register("state_manager",  state_manager)
        service_registry.register("emotion_engine", emotion_engine)
        state_manager.set_state(CompanionState.IDLE)  # explicit initial state
        logger.info("[BMO] CompanionStateManager and EmotionalContinuityEngine registered.")

        # 11. GUI
        self.gui = FaceDisplay()

        logger.info("Bootstrapping complete.")

    def start_flask(self):
        """Run Flask dashboard in a separate thread."""
        app = create_app()
        host = "0.0.0.0"
        port = settings.dashboard.PORT
        
        # Use werkzeug make_server to allow graceful shutdown
        self.flask_server = make_server(host, port, app)
        logger.info(f"Starting dashboard on http://{host}:{port}")
        self.flask_server.serve_forever()

    async def run(self):
        """Main execution loop."""
        await self.bootstrap()
        
        # Start Flask Thread
        self.flask_thread = threading.Thread(target=self.start_flask, daemon=True)
        self.flask_thread.start()
        
        # Start Hardware interfaces
        self.audio_capture.start()
        self.audio_playback.play_stream()
        self.camera.start()
        
        # Start Background Async Tasks
        vad_task = asyncio.create_task(self._vad_loop())
        camera_task = asyncio.create_task(self.camera.run_loop())
        gui_task = asyncio.create_task(self.gui.run_loop())
        
        logger.info("BMO is fully operational.")
        
        # Wait for shutdown signal
        try:
            await asyncio.gather(vad_task, camera_task, gui_task)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def _vad_loop(self):
        """Pump audio chunks into VAD."""
        vad_node = SileroVADNode()
        async for chunk in self.audio_capture.get_chunk_async():
            if not self.gui._is_running: # Stop if GUI closed
                break
            await vad_node.process_chunk(chunk)

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down BMO...")
        if self.audio_capture: self.audio_capture.stop()
        if self.audio_playback: self.audio_playback.stop_stream()
        if self.camera: self.camera.stop()
        if self.gui: self.gui.stop()
        if self.flask_server: self.flask_server.shutdown()
        db.close_all()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    setup_logging()
    bmo = BMO()
    try:
        asyncio.run(bmo.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
