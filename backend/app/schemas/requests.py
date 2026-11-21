"""Public request contracts; re-exported by backend.py for compatibility."""
from pydantic import BaseModel, Field

class GenerateRequest(BaseModel):
    use_cache: bool = True
    text: str = Field(min_length=1, max_length=2000)
    reference_id: str | None = None
    models: list[str] = Field(min_length=1, max_length=3)
    reference_text: str = Field(default="", max_length=4000)
    language: str = "German"


class BatchRequest(BaseModel):
    reference_id: str
    models: list[str] = Field(min_length=1, max_length=3)
    texts: list[str] = Field(min_length=1, max_length=100)
    repetitions: int = Field(default=1, ge=1, le=20)
    reference_text: str = ""


class RatingRequest(BaseModel):
    job_id: str
    rater: str = Field(default="Evaluator", min_length=1, max_length=100)
    category: str = Field(pattern="^(naturalness|similarity|intelligibility)$")
    rating: int = Field(ge=1, le=5)


class LayoutRequest(BaseModel):
    active: list[str]


class Login(BaseModel):
    token: str = Field(max_length=1000)


