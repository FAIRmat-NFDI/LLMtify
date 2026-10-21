"""Generic plugin registry. One instance per extension point.

Usage:
    EXECUTORS = Registry[type[Executor]]("executor")

    @EXECUTORS.register("hello")
    class HelloExecutor(...): ...

    cls = EXECUTORS.get("hello")
"""

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class RegistryError(KeyError):
    pass


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, key: str) -> Callable[[T], T]:
        def decorator(obj: T) -> T:
            self.add(key, obj)
            return obj

        return decorator

    def add(self, key: str, obj: T) -> None:
        if key in self._items:
            raise RegistryError(f"{self.kind} '{key}' already registered")
        self._items[key] = obj

    def get(self, key: str) -> T:
        try:
            return self._items[key]
        except KeyError:
            raise RegistryError(
                f"Unknown {self.kind} '{key}'. Registered: {sorted(self._items)}"
            ) from None

    def keys(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items
