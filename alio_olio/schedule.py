from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# 노션 "ALIO 채용공고"의 날짜 속성명 → "관심 전형 일정"의 유형 옵션명.
# 선언 순서가 곧 전형 진행 순서이며, 노션에 쓰는 순서도 이를 따른다.
STAGES: dict[str, str] = {
    "서류발표일": "서류발표",
    "필기일정": "필기",
    "필기발표일": "필기발표",
    "면접일정": "면접",
    "최종발표일": "최종발표",
}

# (전형 단계, 발표일인가) → 노션 속성명. 서류·최종은 발표일만, 필기·면접은 시행일도 쓴다.
_FIELD_BY_STAGE: dict[tuple[str, bool], str] = {
    ("서류", True): "서류발표일",
    ("필기", False): "필기일정",
    ("필기", True): "필기발표일",
    ("면접", False): "면접일정",
    ("최종", True): "최종발표일",
}

# 라벨별 정규식 후보. 앞에서부터 시도해 처음 매치되는 것을 채택하므로
# 좁은 표현을 먼저 둔다. 발표일 라벨이 일정 라벨에 먹히지 않도록
# "필기시험"/"면접전형"에는 부정 전방탐색을 건다.
#
# "발표시"는 발표일이 아니라 다른 일정의 시작점을 가리키는 말이다("필기합격자발표시
# ∼ 10.14."는 증빙서류 등록 기간이지 발표일이 아니다). 발표일 라벨은 모두 뒤에
# "시"가 붙지 않을 때만 인정한다.
_NOT_WHEN = r"(?!시)"
_LABELS: dict[str, list[str]] = {
    "서류발표일": [rf"서류(?:전형|심사)합격자?발표{_NOT_WHEN}", rf"서류(?:전형|심사)결과발표{_NOT_WHEN}",
                rf"서류전형합격발표{_NOT_WHEN}"],
    "필기일정": [r"필기시험(?!합격|응시대상)", r"필기전형(?!합격)", r"필기고사", r"필기평가"],
    "필기발표일": [rf"필기(?:시험|전형)?합격자?발표{_NOT_WHEN}", rf"필기(?:시험|전형)결과발표{_NOT_WHEN}"],
    "면접일정": [r"면접시험(?!합격)", r"면접전형(?!합격)", r"면접심사(?!합격)"],
    "최종발표일": [rf"최종합격자?(?:결정및)?발표{_NOT_WHEN}"],
}

# 라벨과 날짜 사이에 끼는 잡음("’26.", 괄호, 표 구분자)을 허용하는 폭.
# 넓히면 엉뚱한 표의 날짜를 물어오므로 의도적으로 짧게 유지한다.
_GAP = r"[^0-9]{0,14}"
_DATE = r"(?:['’]?(\d{2,4})[.\-년])?(\d{1,2})[.\-월](\d{1,2})\.?(?:일)?(?:\([월화수목금토일]\))?"

# 굵은 글씨를 두 번 렌더링하는 PDF가 있다("1차전형1차전형시행시행").
# 숫자까지 포함해 접으면 "’26.9.9."가 "’26.9."로 무너지므로 한글 덩어리만 대상으로 한다.
_DOUBLED = re.compile(r"([가-힣]{3,12})\1")
_DOUBLED_THRESHOLD = 20


@dataclass(frozen=True)
class ScheduleHit:
    day: date
    evidence: str


def normalize(text: str) -> str:
    collapsed = re.sub(r"\s+", "", text.replace("　", " "))
    if len(_DOUBLED.findall(collapsed)) > _DOUBLED_THRESHOLD:
        collapsed = _DOUBLED.sub(lambda m: m.group(1), collapsed)
    return collapsed


def _resolve_year(raw: str | None, month: int, reference: date) -> int:
    if raw:
        return int(raw) + 2000 if len(raw) == 2 else int(raw)
    # 연도를 생략한 공고("8.21(금)")는 접수 시작일 기준으로 추정한다.
    # 접수월보다 여섯 달 이상 이르면 이듬해 일정으로 본다.
    return reference.year + (1 if month < reference.month - 6 else 0)


def extract_schedule(text: str, reference: date) -> dict[str, ScheduleHit]:
    """공고문 텍스트에서 전형 단계별 날짜를 뽑는다. 못 찾은 단계는 키가 없다."""
    flat = normalize(text)
    found: dict[str, ScheduleHit] = {}
    for field, patterns in _LABELS.items():
        for pattern in patterns:
            hit = _first_date(flat, pattern, reference)
            if hit:
                found[field] = hit
                break
    return found


def _first_date(flat: str, pattern: str, reference: date) -> ScheduleHit | None:
    for match in re.finditer(pattern + _GAP + _DATE, flat):
        year_text, month, day = match.group(1), int(match.group(2)), int(match.group(3))
        try:
            resolved = date(_resolve_year(year_text, month, reference), month, day)
        except ValueError:
            continue
        evidence = flat[max(0, match.start() - 12):match.end() + 12]
        return ScheduleHit(resolved, evidence)
    return None


# ── 표 기반 추출 ────────────────────────────────────────────────────────────
# 공고문의 전형일정은 대부분 표다. 그런데 발표일은 "합격자발표: 9.2.(수)"처럼 단계명 없이
# 적혀서, 평평한 텍스트에서는 어느 단계의 발표일인지 알 방법이 없다. 셀 경계가 있어야
# "그 행이 어느 단계 행이냐"로 붙일 수 있다.

# 위에서부터 검사한다. "최종 합격자 발표"는 최종이지 서류가 아니다.
# 인성검사·적성검사는 필기와 면접 사이의 별도 단계라 일부러 넣지 않는다 —
# 넣으면 "온라인 인성검사" 행의 날짜를 면접일로 잘못 가져온다(실측 확인).
_STAGE_KEYWORDS = [
    ("최종", r"최종합격|최종선발|최종결과"),
    ("면접", r"면접"),
    ("필기", r"필기|NCS직무역량|직무능력평가|직무수행능력평가"),
    ("서류", r"서류|입사지원서|지원서접수|원서접수|적격심사|자격심사"),
]
_DATE_COLUMN = re.compile(r"일\s*정|일\s*자|예정일|기\s*간|날\s*짜")
# 헤더 칸은 "구 분", "일 정"처럼 짧다. 산문이 담긴 긴 칸에 "일정"이란 글자가 우연히
# 들어 있는 것을 헤더로 착각하면 전형방법 표를 일정표로 고른다(실측 확인).
_HEADER_MAX = 12


def _is_date_header(cell: str) -> bool:
    squeezed = _squeeze(cell)
    return len(squeezed) <= _HEADER_MAX and bool(_DATE_COLUMN.search(squeezed))
_ANNOUNCE_COLUMN = re.compile(r"발\s*표")
_ANNOUNCE_INLINE = re.compile(r"합격자?\s*발표|합격발표|발표일|결과발표")

# 셀 하나에 단계와 날짜가 (단계, 날짜) 순으로 들어 있는 경우: "필기전형:9.19.(토)"
_CELL_STAGE_DATE = re.compile(r"(?P<label>[가-힣]{2,10})\s*[:：]?\s*(?P<date>" + _DATE + r")")

# 표에 없는 값을 채운 뒤, 서로 앞뒤가 맞는지 확인할 순서
_CHRONOLOGY = list(STAGES)
_HORIZON_DAYS = 400


def _classify(text: str) -> str | None:
    for stage, pattern in _STAGE_KEYWORDS:
        if re.search(pattern, text):
            return stage
    return None


def _cell_date(cell: str, reference: date) -> date | None:
    for match in re.finditer(_DATE, _squeeze(cell)):
        day = _to_date(match, reference)
        if day:
            return day
    return None


# 굵은 글씨를 글자 단위로 두 번 찍는 PDF가 있다("필필기기시시험험"). 셀 대부분이 그런
# 경우에만 접는다 — 한국어에도 "간간이"처럼 정상적인 겹글자가 있기 때문이다.
_CHAR_DOUBLED = re.compile(r"([가-힣])\1")


def _squeeze(cell: str) -> str:
    squeezed = re.sub(r"\s+", "", cell or "")
    hangul = sum(1 for char in squeezed if "가" <= char <= "힣")
    if hangul >= 6 and len(_CHAR_DOUBLED.findall(squeezed)) * 2 > hangul * 0.6:
        squeezed = _CHAR_DOUBLED.sub(lambda m: m.group(1), squeezed)
    return squeezed


def _to_date(match: re.Match, reference: date) -> date | None:
    month, day = int(match.group(2)), int(match.group(3))
    try:
        return date(_resolve_year(match.group(1), month, reference), month, day)
    except ValueError:
        return None


def pick_schedule_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """전형일정표 하나를 고른다.

    헤더에 일정/일자/예정일이 없는 표는 후보에서 뺀다. 이 조건이 없으면 자격 가점표처럼
    "필기시험" 열이 있는 다른 표를 골라버린다(실측 확인).
    """
    best, best_score = None, 0
    for table in tables:
        if not table or not any(_is_date_header(cell) for cell in table[0]):
            continue
        flat = " ".join(_squeeze(cell) for row in table for cell in row)
        stages = {stage for stage, pattern in _STAGE_KEYWORDS if re.search(pattern, flat)}
        score = len(stages) * 2 + min(len(re.findall(_DATE, flat)), 8)
        if score > best_score:
            best, best_score = table, score
    return best if best_score >= 6 else None


def parse_schedule_table(table: list[list[str]], reference: date) -> dict[str, ScheduleHit]:
    header = [_squeeze(cell) for cell in table[0]]
    date_col = next((i for i, cell in enumerate(header) if _is_date_header(cell)), None)
    announce_col = next((i for i, cell in enumerate(header) if _ANNOUNCE_COLUMN.search(cell)), None)
    if date_col is None:
        return {}
    label_col, labelled_rows = _label_column(table, date_col, announce_col)
    if labelled_rows < 2:
        # 셀 병합으로 단계 이름이 사라진 표다. 억지로 읽으면 옆 행의 날짜를 가져온다.
        return {}

    found: dict[str, ScheduleHit] = {}
    stage = None
    for row in table[1:]:
        label = _squeeze(row[label_col]) if label_col < len(row) else ""
        # 라벨이 "합격자 발표"뿐인 행은 바로 앞 단계의 발표일이다.
        own = _classify(label)
        stage = own or stage
        if stage is None:
            continue
        schedule_cell = row[date_col] if date_col < len(row) else ""
        # 라벨에 "발표"가 들어간 행은 시행일이 아니라 발표일 행이다. "필기시험 응시대상자
        # 발표"를 필기 시행일로 읽으면 실제 시험일을 놓친다(실측 확인).
        if "발표" in label:
            day = _cell_date(schedule_cell, reference)
            _record(found, stage, True, day, schedule_cell)
        elif own:
            # 물려받은 단계로는 시행일을 적지 않는다. 전형 일정표에는 "추가정보 제출",
            # "자기소개서 제출"처럼 단계 이름이 없는 지원자 제출 마감 행이 섞여 있고,
            # 이 행들이 앞 단계를 물려받으면 정작 뒤에 오는 진짜 시행일 행을 밀어낸다
            # (한수원: 필기를 8.31 추가정보 제출로, 면접을 9.28 자소서 제출로 읽었다).
            _record(found, stage, False, _stage_date(schedule_cell, stage, reference), schedule_cell)
        # 비고 칸에 "· 합격자발표: 9.2.(수)"만 적어두는 표도 흔하다.
        _record(found, stage, True, _announce_date(row, announce_col, reference), " ".join(row))
    return found


def _record(found: dict[str, ScheduleHit], stage: str, announce: bool,
            day: date | None, evidence: str) -> None:
    field = _FIELD_BY_STAGE.get((stage, announce))
    if field and day:
        found.setdefault(field, ScheduleHit(day, _squeeze(evidence)[:80]))


def _label_column(table: list[list[str]], date_col: int, announce_col: int | None) -> tuple[int, int]:
    """단계 이름이 가장 많이 들어 있는 칸을 고른다.

    비고·내용 칸까지 보고 판정하면 "서류전형 합격자 발표" 행이 다음 단계를 안내하는
    비고 문구 때문에 필기로 잘못 분류된다(실측 확인).
    """
    best, best_hits = 0, -1
    for index in range(max(len(row) for row in table)):
        if index == date_col or index == announce_col:
            continue
        hits = sum(1 for row in table[1:]
                   if index < len(row) and _classify(_squeeze(row[index])))
        if hits > best_hits:
            best, best_hits = index, hits
    return best, best_hits


def _stage_date(cell: str, stage: str, reference: date) -> date | None:
    """한 셀에 날짜가 여럿이면 그 단계 이름에 가장 가까운 날짜를 고른다.

    "·(IT직렬) 코딩시험:9.12 ·(全직렬) 필기전형:9.19" 에서 필기전형은 9.19다.
    """
    squeezed = _squeeze(cell)
    labelled = [(m, _classify(m.group("label"))) for m in _CELL_STAGE_DATE.finditer(squeezed)]
    for match, matched_stage in labelled:
        if matched_stage == stage and not _ANNOUNCE_INLINE.search(match.group("label")):
            return _to_date(re.match(_DATE, match.group("date")), reference)
    return _cell_date(_ANNOUNCE_INLINE.split(squeezed)[0], reference)


def _announce_date(row: list[str], announce_col: int | None, reference: date) -> date | None:
    if announce_col is not None and announce_col < len(row):
        return _cell_date(row[announce_col], reference)
    # 발표 칸이 따로 없으면 같은 행 안에서 "합격자발표" 뒤에 붙은 날짜를 쓴다.
    # 일정 칸에 "·필기전형:9.19 ·합격자 발표:9.30"처럼 함께 적는 표도 있으므로 제외하지 않는다.
    for cell in row:
        parts = _ANNOUNCE_INLINE.split(_squeeze(cell))
        if len(parts) > 1:
            day = _cell_date(parts[1], reference)
            if day:
                return day
    return None


def validate(found: dict[str, ScheduleHit], reference: date) -> dict[str, ScheduleHit]:
    """접수 기간 밖이거나 전형 순서를 어기는 값을 버린다.

    채용 캘린더에서 틀린 필기일은 놓친 필기일보다 나쁘다. 의심스러우면 비워 두고
    "미정"으로 넘긴다.
    """
    horizon = reference.toordinal() + _HORIZON_DAYS
    kept: dict[str, ScheduleHit] = {}
    previous = reference
    for field in _CHRONOLOGY:
        hit = found.get(field)
        if hit is None:
            continue
        if not (previous <= hit.day and hit.day.toordinal() <= horizon):
            continue
        kept[field] = hit
        previous = hit.day
    return kept


# 전형 이름 바로 뒤에 항목 기호가 붙은 자리만 그 전형의 절이 열리는 머리말로 본다.
# 본문에는 "장소: 서류전형 합격자에 한하여 별도 안내"처럼 다른 전형을 가리키는 말도
# 섞여 있어서, 가장 가까운 언급을 그대로 믿으면 엉뚱한 절에 묶인다.
_SECTION_HEAD = re.compile(r"(서류|필기|면접)전형(?=[•·])")
# "최종합격자발표"는 제 이름을 달고 있으니 절에 묶을 필요가 없다.
_BARE_ANNOUNCE = re.compile(rf"(?<!최종)합격자발표(?!시)[^0-9]{{0,6}}(?P<date>{_DATE})")
# 절 머리말에서 이만큼 떨어지면 다른 이야기로 본다. 근로복지공단의 필기 발표는 278자다.
_SECTION_REACH = 400


def section_announcements(text: str, reference: date) -> dict[str, ScheduleHit]:
    """절 안에 "합격자발표: 9.11."로만 적어 둔 공고를 읽는다.

    "필기전형 •일시: 9.19. •장소: … •합격자발표: 10.2."처럼 전형 이름이 절 머리에만
    나오고 발표 줄에는 안 붙는 양식이 있다. 그 줄만 보면 어느 전형의 발표인지 알 수
    없으므로 바로 앞 절 머리말에 묶는다(근로복지공단에서 확인).
    """
    flat = normalize(text)
    heads = [(m.start(), m.group(1)) for m in _SECTION_HEAD.finditer(flat)]
    found: dict[str, ScheduleHit] = {}
    for match in _BARE_ANNOUNCE.finditer(flat):
        prior = [head for head in heads if head[0] < match.start()]
        if not prior:
            continue
        position, stage = prior[-1]
        if match.start() - position > _SECTION_REACH:
            continue
        day = _to_date(re.match(_DATE, match.group("date")), reference)
        _record(found, stage, True, day, flat[position:match.end()][-80:])
    return found


def resolve(text: str | None, tables: list[list[list[str]]], reference: date) -> dict[str, ScheduleHit]:
    """표를 먼저 읽고, 빠진 필드만 본문 정규식으로 보충한 뒤 앞뒤를 검사한다."""
    found: dict[str, ScheduleHit] = {}
    table = pick_schedule_table(tables)
    if table:
        found.update(parse_schedule_table(table, reference))
    if text:
        for field, hit in extract_schedule(text, reference).items():
            found.setdefault(field, hit)
        # 이름을 제대로 단 라벨을 다 쓰고도 남은 칸만 절 구조로 채운다.
        for field, hit in section_announcements(text, reference).items():
            found.setdefault(field, hit)
    return validate(found, reference)


def stages_in_process(process_text: str) -> set[str]:
    """ALIO 전형절차 문구를 보고 이 공고에 존재하는 단계를 추린다.

    날짜를 못 뽑았을 때 "미정" 행을 만들 단계를 고르는 데 쓴다. 없는 단계까지
    미정으로 만들면 캘린더가 지저분해진다.
    """
    flat = normalize(process_text)
    stages = {"최종발표일"}
    if "서류" in flat:
        stages.add("서류발표일")
    if "필기" in flat:
        stages.update({"필기일정", "필기발표일"})
    if "면접" in flat:
        stages.add("면접일정")
    return stages
