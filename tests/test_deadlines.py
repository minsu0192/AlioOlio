from datetime import date

from alio_olio.deadlines import extract_deadlines

# 한국수력원자력 전형일정표의 실제 행. 표는 (구분, 일정, 내용) 세 칸이고
# 굵은 글씨가 글자마다 두 번 찍혀 있다.
KHNP = [
    ["구구 분분", "일일 정정", "내내 용용"],
    ["", "8.31.(월)\x0011:00까지", "○○ 본본인인확확인인을을 위위한한 추추가가정정보보\n(생생년년월월일일, 사사진진)제제출출"],
    ["", "9.5(토토)", "○○ 필필기기시시험험"],
    ["", "9.28.(월월)\x00~\x0010.1(목목)\x0017:00", "○○ 자자기기소소개개서서 제제출출"],
]


def test_submission_deadlines_are_picked_from_the_schedule_table():
    """시험 날짜와 같은 표에 들어 있지만 종류가 다르다. 놓치면 그대로 탈락한다."""
    found = {item.label: (item.start, item.day)
             for item in extract_deadlines(KHNP, date(2026, 7, 22))}
    assert found["본인확인을위한추가정보제출"] == (None, date(2026, 8, 31))
    assert found["자기소개서제출"] == (date(2026, 9, 28), date(2026, 10, 1))
    # 시험 날짜는 전형 일정 쪽에서 다루므로 여기 끼면 안 된다.
    assert "필기시험" not in found


def test_a_document_name_is_not_an_action():
    """"주민등록초본"의 등록은 지원자가 하는 행위가 아니라 서류 이름이다."""
    table = [["구분", "일정", "내용"],
             ["1", "~2026.10.30.", "주민등록초본"]]
    assert extract_deadlines(table, date(2026, 8, 5)) == []


def test_the_application_period_is_not_repeated():
    """지원서 접수는 이미 "지원기간"에 들어 있다."""
    table = [["구분", "일정", "내용"],
             ["", "7.22.(수)~8.6.(목) 15:00", "○ 입사지원서 접수"]]
    assert extract_deadlines(table, date(2026, 7, 22)) == []


def test_no_table_yields_nothing():
    assert extract_deadlines(None, date(2026, 8, 5)) == []
