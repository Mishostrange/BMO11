import time
import logging
import numpy as np
from robot.services.event_bus import event_bus

logger = logging.getLogger(__name__)

class EngagementDetector:
    """
    Attention Engine.
    Subscribes to 'perception.frame' and 'speech.transcribed'.
    Computes a 0-100 Attention Score based on face visibility, position, pose, and gaze.
    Publishes 'engagement.update' with the rich attention data.
    """
    
    def __init__(self):
        event_bus.subscribe('perception.frame', self._process_perception)
        event_bus.subscribe('speech.transcribed', self._process_speech)
        
        self.last_speech_time = 0.0
        self.ema_alpha = 0.2
        self.attention_history = {} # track_id -> score
        
    async def _process_speech(self, event_type: str, payload: dict):
        self.last_speech_time = time.time()

    async def _process_perception(self, event_type: str, payload: dict):
        faces = payload.get("all_faces", [])
        
        # If no faces, we can publish a 0 score for the system
        if not faces:
            await event_bus.publish('engagement.update', {
                "engaged": False,
                "score": 0.0,
                "faces_attention": []
            })
            return
            
        faces_attention = []
        max_score = 0.0
        best_face = None
        
        for face in faces:
            score = 0.0
            pitch = 0.0
            yaw = 0.0
            
            # 1. Visibility (Base 20 pts)
            score += 20.0
            
            # 2. Position & Size (up to 20 pts)
            # Distance from center of 640x480 frame
            cx = (face.bbox[0] + face.bbox[2]) / 2.0
            cy = (face.bbox[1] + face.bbox[3]) / 2.0
            dist_from_center = np.linalg.norm(np.array((cx, cy)) - np.array((320, 240)))
            # Max possible dist is ~400. If dist is 0, give 20 pts. If dist > 300, give 0.
            pos_pts = max(0, 20.0 * (1.0 - (dist_from_center / 300.0)))
            score += pos_pts
            
            # 3. Head Pose (up to 30 pts)
            if hasattr(face, 'kps'):
                left_eye, right_eye, nose = face.kps[0], face.kps[1], face.kps[2]
                left_dist = abs(nose[0] - left_eye[0])
                right_dist = abs(nose[0] - right_eye[0])
                total_dist = left_dist + right_dist
                
                if total_dist > 0:
                    yaw = (right_dist - left_dist) / total_dist
                
                eye_y = (left_eye[1] + right_eye[1]) / 2
                chin_y = face.bbox[3]
                upper_dist = abs(nose[1] - eye_y)
                lower_dist = abs(nose[1] - chin_y)
                total_h = upper_dist + lower_dist
                if total_h > 0:
                    pitch = (lower_dist - upper_dist) / total_h
                    
                # Perfect yaw is 0. Perfect pitch is > 0.
                yaw_penalty = abs(yaw) * 30.0 # if yaw=1 (profile), penalty 30
                pose_pts = max(0, 30.0 - yaw_penalty)
                score += pose_pts
                
                # 4. Gaze (up to 15 pts)
                # Simple proxy: if yaw is small, we assume gaze is good.
                gaze_pts = 15.0 if abs(yaw) < 0.2 else 0.0
                score += gaze_pts
                
            # 5. Session Activity (up to 15 pts)
            time_since_speech = time.time() - self.last_speech_time
            if time_since_speech < 30.0:
                speech_pts = 15.0 * (1.0 - (time_since_speech / 30.0))
                score += speech_pts
                
            # Clamp 0-100
            score = max(0.0, min(100.0, score))
            
            # Apply EMA
            tid = getattr(face, 'track_id', -1)
            old_score = self.attention_history.get(tid, score)
            smoothed_score = old_score * (1 - self.ema_alpha) + score * self.ema_alpha
            self.attention_history[tid] = smoothed_score
            
            faces_attention.append({
                "track_id": tid,
                "score": smoothed_score,
                "pitch": float(pitch),
                "yaw": float(yaw),
                "bbox": face.bbox.tolist(),
                "embedding": face.embedding.tobytes() if face.embedding is not None else None
            })
            
            if smoothed_score > max_score:
                max_score = smoothed_score
                best_face = faces_attention[-1]
                
        # Publish engagement state
        await event_bus.publish('engagement.update', {
            "engaged": max_score >= 40.0,
            "score": float(max_score),
            "best_face": best_face,
            "faces_attention": faces_attention
        })
