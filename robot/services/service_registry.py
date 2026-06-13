from typing import Type, TypeVar, Dict, Any

T = TypeVar('T')

class ServiceRegistry:
    def __init__(self):
        self._services: Dict[Type, Any] = {}

    def register(self, interface: Type[T], implementation: T) -> None:
        """Register a service implementation."""
        self._services[interface] = implementation

    def get(self, interface: Type[T]) -> T:
        """Retrieve a service implementation."""
        if interface not in self._services:
            raise KeyError(f"Service {interface.__name__} is not registered.")
        return self._services[interface]

# Global singleton
service_registry = ServiceRegistry()
