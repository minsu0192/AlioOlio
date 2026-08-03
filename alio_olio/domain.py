from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


def split_values(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


@dataclass
class Posting:
    seq: int
    organization: str
    title: str
    start_date: date
    end_date: date
    registered_date: date
    status: str = "진행중"
    headcount: int | None = None
    employment_types: list[str] = field(default_factory=list)
    career_types: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    work_areas: list[str] = field(default_factory=list)
    ncs: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    replacement: str = ""
    detail_text: str = ""
    url: str = ""

    def searchable_text(self) -> str:
        return " ".join(
            [self.organization, self.title, self.detail_text]
            + self.employment_types + self.career_types + self.locations
            + self.work_areas + self.ncs + self.education
        ).casefold()

    def to_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("start_date", "end_date", "registered_date"):
            data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "Posting":
        copy = dict(data)
        for key in ("start_date", "end_date", "registered_date"):
            copy[key] = date.fromisoformat(copy[key])
        return cls(**copy)


@dataclass(frozen=True)
class FilterRule:
    enabled: bool
    category: str
    name: str
    value: str = ""
    rule: str = "official"


OFFICIAL_FIELD_MAP = {
    "고용형태": "employment_types",
    "채용구분": "career_types",
    "근무지": "locations",
    "근무분야": "work_areas",
    "NCS": "ncs",
    "학력": "education",
    "기관": "organization",
}
