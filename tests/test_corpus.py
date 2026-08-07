"""실제 공고문 15건으로 고정한 회귀 테스트.

`tests/fixtures/corpus.json` 은 ALIO에서 받은 공고문에서 뽑은 전형일정표와 본문이다
(PDF 원본은 크므로 넣지 않는다). 기대값은 공고문 원문을 눈으로 확인해 적었다.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from alio_olio.schedule import STAGES, resolve

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "corpus.json").read_text(encoding="utf-8"))

# seq: (접수 시작일, 기관)
POSTINGS = {
    "302985": (date(2026, 7, 22), "한국석유관리원"),
    "303024": (date(2026, 7, 23), "한국자활복지개발원"),
    "303028": (date(2026, 7, 23), "한국영유아보육·교육진흥원"),
    "303046": (date(2026, 7, 24), "전남대학교병원"),
    "303102": (date(2026, 7, 27), "한국보훈복지의료공단"),
    "303212": (date(2026, 7, 29), "한국보건의료연구원"),
    "303269": (date(2026, 7, 30), "한국고용노동교육원"),
    "303274": (date(2026, 7, 31), "한국에너지재단"),
    "303296": (date(2026, 7, 31), "경상국립대학교병원"),
    "303344": (date(2026, 7, 31), "한국주택금융공사"),
    "302926": (date(2026, 7, 22), "한국해양교통안전공단"),
    "303139": (date(2026, 7, 29), "한국부동산원"),
    "303396": (date(2026, 8, 3), "시청자미디어재단"),
    "302968": (date(2026, 7, 22), "한국수력원자력"),
    "303549": (date(2026, 8, 5), "근로복지공단"),
}

# 공고문 원문과 대조해 확인한 정답. 여기 없는 단계는 공고에 없거나 아직 못 뽑는 값이다.
CONFIRMED = {
    "302985": {"서류발표일": "2026-08-14", "필기일정": "2026-08-22", "필기발표일": "2026-08-26",
               "면접일정": "2026-09-02", "최종발표일": "2026-09-17"},
    "303024": {"서류발표일": "2026-08-21", "면접일정": "2026-08-27", "최종발표일": "2026-09-02"},
    "303028": {"필기일정": "2026-08-29", "면접일정": "2026-09-09"},
    "303046": {"필기발표일": "2026-09-01", "면접일정": "2026-09-08", "최종발표일": "2026-09-22"},
    "303212": {"필기일정": "2026-09-15", "면접일정": "2026-10-06"},
    "303269": {"서류발표일": "2026-09-02", "필기일정": "2026-09-12", "필기발표일": "2026-09-22",
               "면접일정": "2026-10-07"},
    "303274": {"필기일정": "2026-09-05", "면접일정": "2026-09-14"},
    "303296": {"서류발표일": "2026-08-10", "필기일정": "2026-08-29", "필기발표일": "2026-09-02",
               "면접일정": "2026-09-04"},
    "303344": {"서류발표일": "2026-09-08", "필기일정": "2026-09-19", "필기발표일": "2026-09-30",
               "면접일정": "2026-10-19"},
    "302926": {"서류발표일": "2026-08-26", "필기일정": "2026-09-05", "필기발표일": "2026-09-09",
               "면접일정": "2026-09-14", "최종발표일": "2026-10-01"},
    "303139": {"서류발표일": "2026-08-21", "필기일정": "2026-09-05", "면접일정": "2026-09-29",
               "최종발표일": "2026-11-06"},
    "303396": {"서류발표일": "2026-08-28", "면접일정": "2026-09-02"},
    "302968": {"필기일정": "2026-09-05", "면접일정": "2026-10-12", "최종발표일": "2026-12-22"},
    # 필기 합격자 발표는 10.2.(금)인데 라벨이 "필기"가 빠진 "합격자발표"뿐이라 못 뽑는다.
    # 바로 뒤 "필기합격자발표시∼10.14."(증빙서류 등록 기간)를 물어오던 것은 고쳤다.
    "303549": {"필기일정": "2026-09-19", "면접일정": "2026-10-26", "최종발표일": "2026-11-24"},
    "303102": {},  # 스캔 이미지 PDF라 글자가 없다. 아무것도 못 뽑는 것이 정답이다.
}

# 아직 틀리게 뽑는 값. 고쳐지면 이 목록에서 빼고 CONFIRMED로 옮긴다.
KNOWN_WRONG = {
    ("303046", "필기일정"),    # 8/23이 정답인데 8/10을 가져온다 (HWP는 표 구조가 없다)
    ("303212", "서류발표일"),  # 8/31이 정답인데 9/2를 가져온다 (셀 병합으로 표가 무너짐)
}


def extracted(seq: str) -> dict[str, str]:
    entry = CORPUS[seq]
    found = resolve(entry["text"], [entry["table"]] if entry["table"] else [], POSTINGS[seq][0])
    return {field: hit.day.isoformat() for field, hit in found.items()}


@pytest.mark.parametrize("seq", sorted(CORPUS))
def test_confirmed_dates_are_extracted(seq):
    result = extracted(seq)
    for field, expected in CONFIRMED[seq].items():
        assert result.get(field) == expected, f"{POSTINGS[seq][1]} {field}"


@pytest.mark.parametrize("seq", sorted(CORPUS))
def test_no_unexpected_values_appear(seq):
    """확인된 정답도 아니고 알려진 오답도 아닌 값이 새로 생기면 실패한다."""
    for field, value in extracted(seq).items():
        if CONFIRMED[seq].get(field) == value or (seq, field) in KNOWN_WRONG:
            continue
        pytest.fail(f"{POSTINGS[seq][1]} {field}={value} 는 확인되지 않은 값입니다")


def test_overall_accuracy_does_not_regress():
    """평평한 텍스트 정규식만 쓰던 시절 28개, 표 파싱 도입 후 41개, 물려받은 단계를
    시행일에 쓰지 않게 고친 뒤 43개, 근로복지공단을 코퍼스에 넣어 46개다."""
    correct = sum(1 for seq in CORPUS for field, expected in CONFIRMED[seq].items()
                  if extracted(seq).get(field) == expected)
    assert correct >= 46, f"정답 {correct}개 — 근로복지공단 추가 시점(46)보다 떨어졌습니다"


def test_chronology_is_never_violated():
    order = list(STAGES)
    for seq in CORPUS:
        days = [value for field, value in sorted(extracted(seq).items(), key=lambda x: order.index(x[0]))]
        assert days == sorted(days), f"{POSTINGS[seq][1]} 전형 순서가 뒤집혔습니다: {days}"
