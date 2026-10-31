"""Immutable request-scoped controls, independent of process environment mutation."""
from contextvars import ContextVar
from dataclasses import dataclass

@dataclass(frozen=True)
class GenerationConfig:
    temperature: float | None = None
    speech_rate: float | None = None
    seed: int | None = None

current = ContextVar('generation_config', default=GenerationConfig())
