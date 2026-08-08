"""지원자가 직접 챙겨야 하는 제출·등록 기한을 뽑는다.

전형 일정(시험을 보는 날)과는 다른 종류다. 자기소개서 제출, 증빙서류 등록, 수험표
사진 등록처럼 놓치면 그대로 탈락하는 날짜인데, 전형 일정 추출에서는 오히려 "이건
시험일이 아니다"라고 걸러내던 값이다. 걸러 버리지 말고 따로 모아 캘린더에 올린다.

전형일정표의 행만 본다. 공고문 본문에는 "제출"이 수십 번 나오고(증빙서류 목록,
유의사항, 서술형 문장) 어느 날짜가 어느 행위의 것인지 붙일 방법이 없어, 본문에서
긁으면 "고졸 학교장 추천서 → 2027-01-01" 같은 값이 나온다. 표는 행이 곧 짝이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .schedule import _DATE, _is_date_header, _squeeze, _to_date

# 지원자가 하는 행위. 라벨이 이 말로 끝나야 한다. 안에 들어 있기만 한 것은
# 서류 이름이다("주민등록초본"의 등록은 행위가 아니다).
_ACTION = re.compile(r"(제출|등록|입력|접수|신청)\s*$")

# 지원서 접수는 이미 "지원기간"에 들어 있으므로 따로 올리지 않는다.
_ALREADY_TRACKED = re.compile(r"입사지원서|응시원서|원서접수|지원서접수")

# 라벨로 쓰기엔 너무 긴 칸은 설명문이다.
_LABEL_MAX = 40


@dataclass(frozen=True)
class Deadline:
    label: str
    day: date
    start: date | None


def extract_deadlines(table: list[list[str]] | None, reference: date) -> list[Deadline]:
    """전형일정표에서 제출·등록 기한 행만 골라낸다."""
    if not table:
        return []
    header = [_squeeze(cell) for cell in table[0]]
    date_col = next((i for i, cell in enumerate(header) if _is_date_header(cell)), None)
    if date_col is None:
        return []
    found: dict[str, Deadline] = {}
    for row in table[1:]:
        label = _pick_label(row, date_col)
        if label is None:
            continue
        span = _span(row[date_col] if date_col < len(row) else "", reference)
        if span is None:
            continue
        start, day = span
        found.setdefault(label, Deadline(label, day, start))
    return list(found.values())


def _pick_label(row: list[str], date_col: int) -> str | None:
    for index, cell in enumerate(row):
        if index == date_col:
            continue
        text = _squeeze(cell)
        if not text or len(text) > _LABEL_MAX:
            continue
        if not _ACTION.search(_strip_notes(text)):
            continue
        if _ALREADY_TRACKED.search(text):
            return None
        return _tidy(text)
    return None


def _strip_notes(text: str) -> str:
    """글머리표와 괄호 안 부연을 뗀다. "추가정보(생년월일, 사진)제출"의 괄호 때문에
    행위가 끝에 오지 않는 것처럼 보이는 일을 막는다."""
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r"^[○◦●▪·※▢□\s]+", "", text)
    return re.sub(r"\([^)]*\)", "", text).strip()


def _tidy(text: str) -> str:
    return _strip_notes(text)


def _span(cell: str, reference: date) -> tuple[date | None, date] | None:
    """날짜 칸에서 (시작, 마감)을 읽는다. 기간이면 끝이 마감이다."""
    squeezed = _squeeze(cell)
    days = [day for day in (_to_date(m, reference) for m in re.finditer(_DATE, squeezed)) if day]
    days = [day for day in days if day >= reference]
    if not days:
        return None
    return (days[0], days[-1]) if len(days) > 1 else (None, days[0])
