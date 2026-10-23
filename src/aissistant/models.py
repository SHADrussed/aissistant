from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    CLIENT = "клиент"
    MONEY = "деньги"
    URGENT = "срочно"
    STUDY = "учёба"
    PERSONAL = "личное"
    OTHER = "другое"


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[Category] = Field(min_length=1, max_length=6)
    summary: str = Field(min_length=1, max_length=240)
    recommendation: str = Field(min_length=1, max_length=200)
    draft: str = Field(max_length=300)
    agreement: str = Field(max_length=200)


@dataclass(frozen=True)
class Message:
    source: str
    scope: str
    id: str
    text: str
    link: str
