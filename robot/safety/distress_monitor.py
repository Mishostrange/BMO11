import logging
from robot.services.event_bus import event_bus
from robot.database.connection import db

logger = logging.getLogger(__name__)

class DistressMonitor:
    """Monitors for severe distress and triggers safety protocols."""
    
    def __init__(self):
        event_bus.subscribe('safety.alert', self._on_safety_alert)

    async def _on_safety_alert(self, event_type: str, data: dict):
        alert_type = data.get("type")
        level = data.get("level", 0)
        child_id = data.get("child_id") # May need to be injected or fetched from SessionManager
        
        if alert_type == "high_frustration" and level >= 4:
            logger.warning(f"HIGH DISTRESS DETECTED (Level {level}). Triggering safety protocol.")
            await self._trigger_safety_protocol(child_id, "frustration", level)

    async def _trigger_safety_protocol(self, child_id: int, reason: str, severity: int):
        """Execute actions to de-escalate and notify."""
        
        # 1. Force UI to a very calm state
        await event_bus.publish('ui.expression.change', 'calm')
        
        # 2. Stop any active games or TTS
        await event_bus.publish('tts.cancel', {})
        
        # 3. Log alert for dashboard
        if child_id:
            try:
                with db.get_cursor() as cursor:
                    # In a full schema, we'd have an alerts table. Reusing sessions for now or logging
                    # For MVP, we just log it. A production app needs an `alerts` table.
                    logger.error(f"DB ALERT: Child {child_id} distressed. Reason: {reason}")
            except Exception as e:
                logger.error(f"Error logging distress alert: {e}")
