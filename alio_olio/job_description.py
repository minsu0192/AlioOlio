"""NCS 직무기술서에서 직무 내용을 뽑는다.

기관마다 표 모양이 다르다. 한수원은 라벨이 첫 칸에 있고 글자가 두 번씩 찍혀 있다.
근로복지공단은 첫 칸에 세부 직무명이 오고 라벨이 두 번째 칸에 온다. 그래서 칸 위치를
정해 두지 않고, 라벨과 일치하는 칸을 찾은 뒤 그 행의 다음 내용 칸을 값으로 읽는다.
"""
from __future__ import annotations

import re

from .questions import normalize_cell

# 노션 속성명 → 직무기술서에서 찾을 라벨.
# "직무수행"처럼 짧게 두면 "직무수행태도"까지 주요 업무로 딸려 들어온다(한국부동산원).
LABELS: dict[str, tuple[str, ...]] = {
    "주요 업무": ("직무수행내용", "주요사업"),
    "필요 지식·기술": ("필요지식", "필요기술"),
    # 같은 칸을 기관마다 다르게 부른다(한국부동산원은 "직업공통능력").
    "직무 핵심역량": ("직업기초능력", "직업공통능력"),
}

# 값 칸으로 볼 최소 길이. "○" 하나만 있거나 표가 밀려 빈 칸이 오는 경우를 거른다.
_MIN_VALUE = 10

# 노션 속성 하나에 담을 최대 길이. 한수원 직무기술서는 채용분야가 열 개가 넘어
# 필요지식만 1만 자가 넘는다. 다 넣으면 표가 못 읽을 지경이 되므로 잘라 두고
# 원문은 "직무기술서 링크"로 열게 한다.
_MAX_FIELD = 1500

# 문서마다 다른 글머리표를 하나로 맞춘다. "ㅇ"과 영문 "o"를 글머리표로 쓰는 기관도
# 있어 낱자로 떨어져 있을 때만 바꾼다(영어 단어 속 o까지 건드리면 안 된다).
_BULLET = re.compile(r"[○◦●▪·]\s*|(?:(?<=^)|(?<=\s))[ㅇo](?=\s)")
# "○ ○"처럼 글머리표가 겹쳐 찍힌 자리는 하나로 줄인다.
_REPEATED_BULLET = re.compile(r"(?:·\s*){2,}")


def _label_of(cell: str) -> str | None:
    """칸이 어떤 라벨인지. 라벨 칸은 짧으므로 긴 문장은 보지 않는다."""
    flat = re.sub(r"\s+", "", normalize_cell(cell))
    if not flat or len(flat) > 12:
        return None
    for field, names in LABELS.items():
        # 라벨 앞에 기관명이 붙기도 한다("한국부동산원주요사업", "공단주요사업").
        if any(flat.startswith(name) or flat.endswith(name) for name in names):
            return field
    return None


def _clean(value: str) -> str:
    text = normalize_cell(value)
    text = _BULLET.sub(" · ", text)
    text = _REPEATED_BULLET.sub("· ", text)
    return re.sub(r"\s+", " ", text).strip(" ·")


def extract_profile(tables: list[list[list[str]]]) -> dict[str, str]:
    """직무기술서 표에서 노션에 채울 값을 뽑는다.

    한 문서에 채용분야가 여럿이면(한수원은 사무·기계·전기전자…) 같은 라벨이 여러 번
    나온다. 나온 순서대로 모으되 같은 문장은 한 번만 남긴다.
    """
    found: dict[str, list[str]] = {field: [] for field in LABELS}
    for table in tables:
        for row in table:
            field = None
            for cell in row:
                if field is None:
                    field = _label_of(cell)
                    continue
                value = _clean(cell)
                if len(value) >= _MIN_VALUE:
                    if value not in found[field]:
                        found[field].append(value)
                    break
    return {field: _fit("\n".join(values)) for field, values in found.items() if values}


def _fit(value: str) -> str:
    if len(value) <= _MAX_FIELD:
        return value
    cut = value[:_MAX_FIELD]
    # 문장이나 항목 중간에서 끊기지 않게 마지막 구분자까지만 남긴다.
    edge = max(cut.rfind("\n"), cut.rfind(" · "))
    if edge > _MAX_FIELD // 2:
        cut = cut[:edge]
    return cut.rstrip(" ·\n") + " … (나머지는 직무기술서 링크 참고)"
