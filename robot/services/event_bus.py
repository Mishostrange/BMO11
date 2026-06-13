import asyncio
import logging
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)

class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        if handler not in self.subscribers[event_type]:
            self.subscribers[event_type].append(handler)
            logger.debug(f"Subscribed handler to {event_type}")

    def unsubscribe(self, event_type: str, handler: Callable):
        if event_type in self.subscribers and handler in self.subscribers[event_type]:
            self.subscribers[event_type].remove(handler)
            logger.debug(f"Unsubscribed handler from {event_type}")

    async def publish(self, event_type: str, data: Any = None):
        logger.debug(f"Publishing event: {event_type}")
        if event_type in self.subscribers:
            handlers = self.subscribers[event_type]
            # Create tasks for all handlers to run concurrently
            tasks = [asyncio.create_task(self._safe_invoke(handler, event_type, data)) for handler in handlers]
            if tasks:
                await asyncio.gather(*tasks)

    async def _safe_invoke(self, handler: Callable, event_type: str, data: Any):
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event_type, data)
            else:
                # Run synchronous handlers in executor to avoid blocking the event loop
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, handler, event_type, data)
        except Exception as e:
            logger.error(f"Error executing handler for event {event_type}: {e}", exc_info=True)

# Global singleton
event_bus = EventBus()
