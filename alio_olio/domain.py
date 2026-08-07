from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

# 고용형태 뒤에 딸려 오는 "대체인력여부 예/아니오"는 고용형태가 아니다. 그대로 두면
# "정규직"과 "정규직 대체인력여부 아니오"가 서로 다른 선택지로 갈라진다.
_REPLACEMENT_TAIL = re.compile(r"\s*대체인력여부\s*(?:예|아니오)\s*$")


def split_values(value: str | None) -> list[str]:
    """쉼표로 나누되 괄호 안의 쉼표는 건드리지 않는다.

    ALIO는 "기타(기능노무직(취사원, 보조원))"처럼 괄호 안에서 쉼표를 쓴다. 그냥
    쪼개면 "기타(기능노무직(취사원"과 "보조원))"이 각각 선택지로 남는다.
    """
    parts: list[str] = []
    depth, current = 0, []
    for char in value or "":
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    cleaned = (_REPLACEMENT_TAIL.sub("", part).strip() for part in parts)
    return [part for part in cleaned if part]


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


# 노션 캘린더는 "구분" 선택 속성의 색으로 카드를 칠한다. 고용형태에서 유도한다.
# 채용형 인턴은 결국 정규직 전환이라 정규직과 같은 색으로 묶는다.
CATEGORY_RULES = [
    ("정규직", ("정규직", "청년인턴(채용형)")),
    ("체험형인턴", ("청년인턴(체험형)",)),
]


def categorize(employment_types: list[str]) -> str:
    for label, keywords in CATEGORY_RULES:
        if any(keyword in employment_types for keyword in keywords):
            return label
    return "기타"
