"""Direct in-process model interface backed by backend/models."""

from models import (
    generate_audio,
    get_engine,
    model_availability,
    preload_all_models,
)

__all__ = [
    "generate_audio",
    "get_engine",
    "model_availability",
    "preload_all_models",
]
