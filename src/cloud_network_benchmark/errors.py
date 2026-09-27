from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class BenchmarkError(Exception):
    message: str
    code: str = "benchmark_error"
    exit_code: int = 1
    field: Optional[str] = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        return {"error": payload}


class UsageError(BenchmarkError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "usage_error", 2)


class ValidationError(BenchmarkError):
    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message, "validation_error", 3, field)


class CollisionError(BenchmarkError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "collision_error", 4)


class PersistenceError(BenchmarkError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "persistence_error", 5)
