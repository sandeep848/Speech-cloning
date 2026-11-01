"""Immutable request-scoped controls, independent of process environment mutation."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float | None = None
    speech_rate: float | None = None
    seed: int | None = None


current = ContextVar("generation_config", default=GenerationConfig())


def effective_parameters(model, config):
    import os
    from models.pacing import get_speech_rate

    temperature = config.temperature
    if temperature is None and model != "cosyvoice3":
        temperature = float(
            os.getenv(
                "QWEN_TEMPERATURE" if model == "qwen3" else "XTTS_TEMPERATURE",
                ".6" if model == "qwen3" else ".75",
            )
        )
    return {
        "temperature": temperature,
        "speech_rate": config.speech_rate
        if config.speech_rate is not None
        else get_speech_rate(model),
        "seed": config.seed,
        "top_p": float(os.getenv("QWEN_TOP_P", ".85"))
        if model == "qwen3"
        else 0.85
        if model == "xtts-v2"
        else None,
        "top_k": int(os.getenv("QWEN_TOP_K", "30"))
        if model == "qwen3"
        else 50
        if model == "xtts-v2"
        else None,
        "subtalker_temperature": float(os.getenv("QWEN_SUBTALKER_TEMP", ".6"))
        if model == "qwen3"
        else None,
        "subtalker_top_p": float(os.getenv("QWEN_SUBTALKER_TOP_P", ".85"))
        if model == "qwen3"
        else None,
        "repetition_penalty": float(os.getenv("XTTS_REPETITION_PENALTY", "10"))
        if model == "xtts-v2"
        else None,
        "native_speed_multiplier": float(os.getenv("XTTS_SPEED", "1"))
        if model == "xtts-v2"
        else 1,
        "pauses_ms": {
            key: float(os.getenv(key, str(value)))
            for key, value in {
                "COMMA_PAUSE_MS": 150,
                "SENTENCE_PAUSE_MS": 320,
                "PARAGRAPH_PAUSE_MS": 600,
                "CONTINUATION_PAUSE_MS": 90,
                "MAX_INTERNAL_PAUSE_MS": 650,
            }.items()
        },
    }
