import cv2
import asyncio
import logging
import numpy as np
import time
from insightface.app import FaceAnalysis
from robot.config.settings import settings
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class CameraManager:
    """
    Centralized camera and perception pipeline.
    Captures frames, runs MediaPipe Face Mesh ONCE per frame, 
    and publishes a rich 'perception.frame' event to prevent duplicate processing.
    """
    
    def __init__(self):
        self.camera_index = settings.perception.CAMERA_INDEX
        self.fps = settings.perception.FPS
        self.cap = None
        self.picam2 = None
        self.use_picamera2 = False
        self._is_running = False

        # Initialize InsightFace centrally
        # 'buffalo_s' is lightweight and perfectly compatible with aarch64
        # We only need detection every frame. Recognition is throttled manually later.
        self.app = FaceAnalysis(name='buffalo_s', allowed_modules=['recognition', 'detection'])
        self.app.prepare(ctx_id=0, det_size=(640, 640)) # 0 means CPU/auto
        
        # State for Tracking and Smoothing
        self.next_track_id = 0
        self.trackers = {} # track_id -> {"centroid": (x,y), "bbox_ema": bbox, "frames_since_seen": 0, "last_embedding": emb, "frames_since_emb": 0}
        self.max_disappeared = 10 # frames before dropping a track
        self.ema_alpha = 0.5 # Smoothing factor for bounding boxes

    def start(self):
        if self._is_running:
            return
            
        try:
            import sys
            # Allow importing system-installed picamera2 from within a virtual environment
            if '/usr/lib/python3/dist-packages' not in sys.path:
                sys.path.append('/usr/lib/python3/dist-packages')
                
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(main={"format": 'RGB888', "size": (640, 480)})
            self.picam2.configure(config)
            self.picam2.start()
            self.use_picamera2 = True
            self._is_running = True
            logger.info("Started camera using Picamera2")
            return
        except ImportError:
            logger.info("Picamera2 not found, falling back to cv2.VideoCapture")
        except Exception as e:
            logger.warning(f"Picamera2 init failed: {e}. Falling back to cv2.VideoCapture")

        # Optimize for lowest latency: small buffer, MJPG format if possible
        # Use V4L2 backend for Raspberry Pi
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            logger.error(f"Failed to open camera at index {self.camera_index}")
            return
            
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Prevent frame queueing/latency
        
        self._is_running = True
        logger.info(f"Started camera {self.camera_index} with central Face Mesh")

    def stop(self):
        self._is_running = False
        if self.use_picamera2 and self.picam2:
            self.picam2.stop()
            self.picam2 = None
        elif self.cap:
            self.cap.release()
            self.cap = None
        logger.info("Stopped camera")

    async def run_loop(self):
        loop = asyncio.get_running_loop()
        
        while self._is_running:
            start_t = time.time()
            try:
                frame = None
                rgb_frame = None
                ret = False

                if self.use_picamera2:
                    # Picamera2 returns RGB by default with RGB888 config
                    rgb_frame = await loop.run_in_executor(None, self.picam2.capture_array)
                    if rgb_frame is not None:
                        ret = True
                        frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
                else:
                    # Read frame in executor
                    ret, frame = await loop.run_in_executor(None, self.cap.read)
                    if ret:
                        # 1. Convert to RGB ONE time
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                if ret:
                    rgb_frame.flags.writeable = False
                    
                    # Quick check if frame is pitch black (could be virtual camera / blocked)
                    if getattr(self, '_frame_count', 0) % 30 == 0:
                        brightness = np.mean(frame)
                        if brightness < 5.0:
                            logger.warning(f"Camera frame is very dark! (brightness: {brightness:.2f}). Is it covered?")
                        elif brightness > 250.0:
                            logger.warning(f"Camera frame is completely white! (brightness: {brightness:.2f})")
                    self._frame_count = getattr(self, '_frame_count', 0) + 1

                    # 2. Run InsightFace ONE time centrally
                    def _detect():
                        # We run the full pipeline to get bboxes and embeddings. 
                        # To fully throttle embeddings we would need to split app.get into det and rec.
                        # For simplicity, we run get() but we will manage track_ids and EMA.
                        return self.app.get(frame)  
                        
                    faces = await loop.run_in_executor(None, _detect)
                    
                    target_face = None
                    if faces:
                        # Centroid tracking logic
                        current_centroids = []
                        for f in faces:
                            cx = (f.bbox[0] + f.bbox[2]) / 2.0
                            cy = (f.bbox[1] + f.bbox[3]) / 2.0
                            current_centroids.append((cx, cy, f))
                            
                        # Match to existing tracks
                        assigned_track_ids = set()
                        for cx, cy, f in current_centroids:
                            best_match_id = None
                            best_dist = float('inf')
                            
                            for tid, tdata in self.trackers.items():
                                if tid in assigned_track_ids: continue
                                dist = np.linalg.norm(np.array((cx, cy)) - np.array(tdata["centroid"]))
                                if dist < 100: # Threshold for matching
                                    if dist < best_dist:
                                        best_dist = dist
                                        best_match_id = tid
                                        
                            if best_match_id is not None:
                                # Update existing track
                                tdata = self.trackers[best_match_id]
                                tdata["centroid"] = (cx, cy)
                                tdata["frames_since_seen"] = 0
                                # EMA Smoothing for bounding box
                                old_bbox = tdata["bbox_ema"]
                                new_bbox = f.bbox
                                smoothed_bbox = old_bbox * (1 - self.ema_alpha) + new_bbox * self.ema_alpha
                                tdata["bbox_ema"] = smoothed_bbox
                                f.bbox = smoothed_bbox # override for downstream
                                
                                # Throttle embedding update: keep old embedding if not time yet
                                tdata["frames_since_emb"] += 1
                                if tdata["frames_since_emb"] < 5 and tdata["last_embedding"] is not None:
                                    f.embedding = tdata["last_embedding"]
                                else:
                                    tdata["last_embedding"] = f.embedding
                                    tdata["frames_since_emb"] = 0
                                    
                                f.track_id = best_match_id
                                assigned_track_ids.add(best_match_id)
                            else:
                                # Create new track
                                new_id = self.next_track_id
                                self.next_track_id += 1
                                self.trackers[new_id] = {
                                    "centroid": (cx, cy),
                                    "bbox_ema": f.bbox,
                                    "frames_since_seen": 0,
                                    "last_embedding": f.embedding,
                                    "frames_since_emb": 0
                                }
                                f.track_id = new_id
                                assigned_track_ids.add(new_id)
                                
                        # Cleanup old tracks
                        tracks_to_delete = []
                        for tid, tdata in self.trackers.items():
                            if tid not in assigned_track_ids:
                                tdata["frames_since_seen"] += 1
                                if tdata["frames_since_seen"] > self.max_disappeared:
                                    tracks_to_delete.append(tid)
                        for tid in tracks_to_delete:
                            del self.trackers[tid]

                        # Assume largest face is target (Active User Selection will handle this better later)
                        faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
                        target_face = faces[0]
                    else:
                        # Increment frames_since_seen for all tracks if no faces found
                        tracks_to_delete = []
                        for tid, tdata in self.trackers.items():
                            tdata["frames_since_seen"] += 1
                            if tdata["frames_since_seen"] > self.max_disappeared:
                                tracks_to_delete.append(tid)
                        for tid in tracks_to_delete:
                            del self.trackers[tid]

                    # 3. Publish rich payload
                    payload = {
                        "frame_bgr": frame,
                        "frame_rgb": rgb_frame,
                        "insight_face": target_face,
                        "all_faces": faces,
                        "timestamp": start_t
                    }
                    
                    await event_bus.publish('perception.frame', payload)
                else:
                    logger.warning("Failed to read from camera.")
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Camera loop error: {e}")
                
            # Maintain target FPS
            elapsed = time.time() - start_t
            sleep_time = max(0, (1.0 / self.fps) - elapsed)
            await asyncio.sleep(sleep_time)
