from datetime import date
from pathlib import Path

from alio_olio.schedule import (ScheduleHit, extract_schedule, normalize, readable_evidence,
                                resolve, stages_in_process)

FIXTURES = Path(__file__).parent / "fixtures"


def hits(name: str, reference: date) -> dict[str, str]:
    found = extract_schedule((FIXTURES / name).read_text(encoding="utf-8"), reference)
    return {field: hit.day.isoformat() for field, hit in found.items()}


def test_standard_schedule_table_is_fully_extracted():
    # 한국해양교통안전공단: "서류심사합격자발표’26.8.26.(수)필기시험’26.9.5.(토)…" 정형 표
    assert hits("302926_해양교통안전공단.txt", date(2026, 7, 22)) == {
        "서류발표일": "2026-08-26",
        "필기일정": "2026-09-05",
        "필기발표일": "2026-09-09",
        "면접일정": "2026-09-14",
        "최종발표일": "2026-10-01",
    }


def test_year_is_inferred_when_omitted():
    # 한국부동산원은 "8.21(금)"처럼 연도를 생략한다. 필기발표는 공고에 없으므로 나오면 안 된다.
    assert hits("303139_한국부동산원.txt", date(2026, 7, 29)) == {
        "서류발표일": "2026-08-21",
        "필기일정": "2026-09-05",
        "면접일정": "2026-09-29",
        "최종발표일": "2026-11-06",
    }


def test_repeated_day_digits_survive_deduplication():
    # 굵은 글씨 중복 제거를 숫자에까지 적용하면 "’26.9.9."가 "’26.9."로 무너진다.
    text = "서류심사합격자발표 ’26.8.26.(수) 필기시험 ’26.9.5.(토) 필기시험합격자발표 ’26.9.9.(수)"
    assert extract_schedule(text, date(2026, 7, 1))["필기발표일"].day == date(2026, 9, 9)


def test_deduplication_only_applies_to_heavily_doubled_text():
    # 한수원 공고문처럼 굵은 글씨 구간이 통째로 두 번 찍힌 문서만 접는다.
    words = ["전형절차", "필기시험", "면접전형", "서류심사", "합격발표", "지원자격"] * 5
    doubled = " ".join(f"{word}{word} 항목" for word in words)
    assert "전형절차전형절차" not in normalize(doubled)
    # 몇 군데만 겹치는 정상 문서는 건드리지 않는다.
    assert normalize("전형절차전형절차 나머지") == "전형절차전형절차나머지"


def test_nonstandard_layouts_yield_nothing_rather_than_wrong_dates():
    # 시청자미디어재단은 표가 둘(채용형인턴/무기계약직)이고 합격자발표가 별도 컬럼이라
    # 라벨과 날짜가 붙지 않는다. 한국수력원자력은 단계를 "1차전형/2차전형"이라 부른다.
    # 억지로 추측하느니 비워 두고 "미정"으로 넘기는 편이 안전하다.
    assert hits("303396_시청자미디어재단.txt", date(2026, 8, 3)) == {}
    assert hits("302968_한국수력원자력.txt", date(2026, 7, 22)) == {"최종발표일": "2026-12-22"}


def test_stages_follow_the_declared_process():
    written = stages_in_process("서류전형(30배수)→필기전형(10배수)→면접전형→최종합격")
    assert written == {"서류발표일", "필기일정", "필기발표일", "면접일정", "최종발표일"}
    interview_only = stages_in_process("ㅇ 1단계 (서류전형) ㅇ 2단계 (면접전형)")
    assert "필기일정" not in interview_only
    assert {"서류발표일", "면접일정", "최종발표일"} <= interview_only


def test_a_long_prose_cell_is_not_treated_as_a_date_header():
    """산문이 담긴 긴 칸에 "일정"이 우연히 들어 있으면 전형방법 표를 일정표로 고른다.

    강원대학교병원 공고문에서 실제로 그랬다. 헤더 칸은 "구 분", "일 정"처럼 짧다.
    """
    from alio_olio.schedule import pick_schedule_table

    prose = "◽면접은 직종별 응시인원에 따라 다대다 또는 다대일 방식으로 진행하며 일정은 추후 공지"
    misleading = [[["", prose]],
                  [["합격자 등록 및 임용", "◼임용후보자 등록 8.20. 최종합격자 8.24. 서류 8.13. 면접 8.19."]]]
    assert pick_schedule_table(misleading) is None

    proper = [["구 분", "일 정", "비 고"],
              ["서류전형 합격자 발표", "8. 13.(목)", ""],
              ["면접전형", "8. 19.(수)", ""],
              ["최종합격자 발표", "8. 21.(금)", ""]]
    assert pick_schedule_table([proper]) is proper


def test_an_announcement_without_a_stage_name_is_tied_to_its_section():
    """"필기전형 •일시: … •합격자발표: 10.2." 처럼 발표 줄에 전형 이름이 없는 양식.

    절 머리말에 묶어 읽는다. 설명문에 나오는 "서류전형 합격자에 한하여" 같은 말은
    항목 기호가 없으므로 절 머리말로 세지 않는다.
    """
    body = ("서류전형•선발방법: 자기소개서 평가 •합격자발표: ’26.9.11.(금) "
            "필기전형•일시: ’26.9.19.(토) •장소: 서류전형 합격자에 한하여 별도 안내 "
            "•합격자발표: ’26.10.2.(금)")
    found = resolve(body, [], date(2026, 8, 5))
    assert found["서류발표일"].day == date(2026, 9, 11)
    assert found["필기일정"].day == date(2026, 9, 19)
    assert found["필기발표일"].day == date(2026, 10, 2)


def test_a_submission_window_opening_at_an_announcement_is_not_a_date():
    """"필기합격자발표시 ∼ 10.14."는 증빙서류 등록 기간이지 발표일이 아니다."""
    body = ("필기전형•일시: ’26.9.19.(토) •합격자발표: ’26.10.2.(금) "
            "증빙서류 사전등록 •일시: ’26.10.2.(금) 필기합격자발표시 ∼ ’26.10.14.(수)")
    assert resolve(body, [], date(2026, 8, 5))["필기발표일"].day == date(2026, 10, 2)


def test_a_submission_deadline_is_not_the_stage_date():
    """지금까지 잘못 읽은 날짜는 모두 "지원자가 그때까지 해야 하는 기한"이었다.

    한수원은 8.31 추가정보 제출 마감을 필기일로, 근로복지공단은 증빙서류 등록
    기간의 끝(10.14)을 필기 발표일로 읽었다. 둘 다 그럴듯해서 눈에 띄지 않았다.
    """
    khnp = "필기시험 응시 대상자는 8.31.(월) 11:00까지 추가정보 제출 필기시험 9.5.(토)"
    assert resolve(khnp, [], date(2026, 7, 22))["필기일정"].day == date(2026, 9, 5)

    comwel = "필기전형•일시: 9.19.(토) •합격자발표: ’26.10.2.(금) 필기합격자발표시 ∼ ’26.10.14.(수)"
    assert resolve(comwel, [], date(2026, 8, 5))["필기발표일"].day == date(2026, 10, 2)


def test_a_deadline_does_not_hide_the_real_date_behind_it():
    """기한을 만나면 버리고 다음 후보를 본다. 거기서 멈추면 진짜 일정을 놓친다."""
    body = "면접전형 서류 제출 9.1.(화)까지 면접전형 시행 9.10.(목)"
    assert resolve(body, [], date(2026, 8, 1))["면접일정"].day == date(2026, 9, 10)


def test_readable_evidence_is_taken_from_the_spaced_original():
    """추출용 근거는 공백을 지운 판이라 못 읽는다. 원문에서 다시 찾아 준다."""
    original = "○ 필기전형 합격자발표 : ’26. 10. 2.(금) (채용홈페이지 및 개인 SMS)"
    hit = resolve(original, [], date(2026, 8, 5))["필기발표일"]
    snippet = readable_evidence(original, hit)
    assert "’26. 10. 2.(금)" in snippet
    assert "  " not in snippet


def test_no_evidence_rather_than_the_wrong_sentence():
    assert readable_evidence("아무 상관 없는 문장", ScheduleHit(date(2026, 9, 5), "필기시험9.5")) is None
