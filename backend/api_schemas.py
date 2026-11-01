"""Pydantic schemas for Voice Comparison Studio API requests."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelControls(RequestModel):
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, allow_inf_nan=False)
    speech_rate: float | None = Field(default=None, ge=0.8, le=1.1, allow_inf_nan=False)


class GenerateRequest(RequestModel):
    use_cache: bool = True
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1, max_length=3)
    reference_text: str = Field(default="", max_length=4000)
    language: str = Field(default="German", pattern="^(German|English|de|en)$")
    temperature: float | None = Field(default=None, ge=0.1, le=1.5, allow_inf_nan=False)
    speech_rate: float | None = Field(default=None, ge=0.8, le=1.1, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    model_settings: dict[Literal["qwen3", "cosyvoice3", "xtts-v2"], ModelControls] = (
        Field(default_factory=dict)
    )


class BatchRequest(RequestModel):
    language: Literal["German", "English", "de", "en"] = "German"
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = Field(default="", max_length=4000)


class RatingRequest(RequestModel):
    job_id: str
    rater: str = Field(default="Evaluator", min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(RequestModel):
    active: list[str] = Field(max_length=3)


class Login(RequestModel):
    token: str = Field(default="", max_length=1000)
    username: str = Field(default="", max_length=100)
    password: str = Field(default="", max_length=1000)
    transport: Literal["cookie", "bearer"] = "cookie"


class PasswordReset(RequestModel):
    token: str = Field(min_length=1, max_length=1000)
    password: str = Field(min_length=12, max_length=1000)
