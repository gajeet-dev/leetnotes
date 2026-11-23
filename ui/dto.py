from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from problems.models import Problem


class ProblemCatalogDTO(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    q: str = ""
    difficulty: Problem.Difficulty | None = None
    status: str = ""

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=100, ge=1, le=1000)

    @field_validator("difficulty", mode="before")
    @classmethod
    def normalize_difficulty(cls, v: Any) -> Any:
        if not v:
            return None
        if isinstance(v, str):
            clean = v.strip().lower()
            for choice in Problem.Difficulty:
                if choice.value.lower() == clean:
                    return choice
        return v

    @field_validator("page", mode="before")
    @classmethod
    def normalize_page(cls, v: Any) -> Any:
        if v == "" or v is None:
            return 1
        return v

    @field_validator("per_page", mode="before")
    @classmethod
    def normalize_per_page(cls, v: Any) -> Any:
        if v == "" or v is None:
            return 100
        return v
