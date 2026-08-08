from alio_olio.attachments import Attachment
from alio_olio.questions import (extract_areas, extract_questions, format_questions,
                                 merge_continuations,
                                 normalize_cell, pick_form)

# 한국수력원자력 자기소개서 양식의 실제 셀. 굵은 글씨가 글자마다 두 번 찍혀 있다.
DOUBLED_MAIN = (
    " 한한국국수수력력원원자자력력의의 핵핵심심 가가치치와와 역역할할이이 무무엇엇이이며며, "
    "그그에에 기기여여할할 수수 있있는는 방방안안에에 대대하하여여\n기기술술해해 주주십십시시오오."
)
DOUBLED_SUBS = (
    "2-1. 한한국국수수력력원원자자력력의의 핵핵심심 가가치치를를 가가장장 잘잘 반반영영하하고고 있있다다고고 "
    "생생각각하하는는 사사업업 한한 가가지지를를 선선택택하하고고, 그그렇렇게게 판판단단한한 이이유유를를 "
    "구구체체적적으으로로 기기술술해해 주주십십시시오오. (띄띄어어쓰쓰기기 제제외외 500자자 이이내내)\n"
    "☞☞ 개개인인 식식별별정정보보 절절대대 노노출출 금금지지"
)


def test_doubled_characters_are_restored():
    assert normalize_cell(DOUBLED_MAIN) == (
        "한국수력원자력의 핵심 가치와 역할이 무엇이며, 그에 기여할 수 있는 방안에 대하여 기술해 주십시오."
    )


def test_normal_repeated_syllables_survive():
    # 겹글자가 정상인 한국어도 있다. 셀 대부분이 겹칠 때만 접어야 한다.
    assert normalize_cell("간간이 들르는 곳입니다") == "간간이 들르는 곳입니다"


def test_main_and_sub_questions_are_collected_in_order():
    questions = extract_questions([[[DOUBLED_MAIN]], [[DOUBLED_SUBS]]])
    assert questions[0].startswith("한국수력원자력의 핵심 가치와 역할이")
    assert questions[1].startswith("2-1. 한국수력원자력의 핵심 가치를 가장 잘 반영")
    assert "500자 이내" in questions[1]
    assert "개인 식별정보" not in questions[1]  # 문항 뒤 안내문은 잘라낸다


def test_form_filling_instructions_are_not_questions():
    """"기재하여 주시기 바랍니다"는 서식 작성 안내지 자소서 문항이 아니다."""
    noise = [
        "* 모든 연락은 본인이 기재한 연락처로 취해지므로 정확히 기재하여 주시기 바랍니다.",
        "* 자세한 경력 및 경험 사항은 경력 및 경험기술서에 작성해주시기 바랍니다.",
        "지원자격 공통사항 해당 여부",
    ]
    assert extract_questions([], "\n".join(noise)) == []


def test_questions_outside_tables_are_found_in_the_body():
    body = (
        "자 기 소 개 서• 한국부동산원에 입사하게 된다면 수행하고 싶은 사업 혹은 직무를 선택하고, "
        "그 이유를 기술하시오. (1,000자 제한) • 팀이나 단체에 소속되어 공동의 목표를 달성하기 위해 "
        "협업하는 과정에서 갈등이 발생하였을 때 해결하고자 노력한 경험을 기술해 주십시오."
    )
    questions = extract_questions([], body)
    assert len(questions) == 2
    assert questions[0].startswith("한국부동산원에 입사하게 된다면")
    assert questions[1].startswith("팀이나 단체에 소속되어")


def test_a_dedicated_cover_letter_form_wins_over_the_application():
    attachments = {
        "application": [Attachment("1", "입사지원서.pdf")],
        "etc": [Attachment("2", "자기소개서 양식.pdf")],
        "notice": [Attachment("3", "공고문.pdf")],
        "job_description": [],
    }
    assert pick_form(attachments).file_no == "2"


def test_the_application_is_used_when_there_is_no_cover_letter_form():
    attachments = {"application": [Attachment("1", "입사지원서.pdf")], "etc": [],
                   "notice": [], "job_description": []}
    assert pick_form(attachments).file_no == "1"
    assert pick_form({"application": [], "etc": [], "notice": [], "job_description": []}) is None


def test_bracket_labelled_questions_with_a_length_limit():
    """전남대학교병원은 "[라벨] 물어보는 내용 (최소 N자)" 형태로만 적는다."""
    body = ("자 기 소 개 서 [지원동기] 전남대학교병원에 지원한 동기 (최소 100자, 최대 500자) "
            "[직무전문성] 지원분야 관련 본인의 직무역량을 구체적으로 기술함 (최소 100자, 최대 500자)")
    questions = extract_questions([], body)
    assert questions == [
        "[지원동기] 전남대학교병원에 지원한 동기 (최소 100자, 최대 500자)",
        "[직무전문성] 지원분야 관련 본인의 직무역량을 구체적으로 기술함 (최소 100자, 최대 500자)",
    ]


def test_bracket_form_needs_a_length_limit_to_count():
    # 자수 제한이 없으면 목차나 평가 기준과 구분되지 않는다.
    assert extract_questions([], "[전형절차] 서류전형 후 필기전형을 시행합니다") == []


def test_seorul_haejusigi_barapnida_is_a_question():
    """한국보훈복지의료공단은 "~서술해 주시기 바랍니다"로 끝난다."""
    body = ("[조직이해능력] 공공기관으로서 공단이 수행하는 역할과 가치에 대해 본인의 관점에서 "
            "설명하고, 본인의 역량을 어떻게 발휘할 수 있을지 구체적으로 서술해 주시기 바랍니다.")
    assert len(extract_questions([], body)) == 1


def test_the_same_question_is_not_listed_twice():
    """자수 제한이 붙은 판과 안 붙은 판이 같이 잡히면 긴 쪽만 남긴다."""
    body = ("[경험/경력기술서] 직무와 관련된 경험을 구체적으로 기술해주십시오 다시 "
            "[경험/경력기술서] 직무와 관련된 경험을 구체적으로 기술해주십시오 (최대 1,000자 입력가능)")
    questions = extract_questions([], body)
    assert len(questions) == 1
    assert questions[0].endswith("(최대 1,000자 입력가능)")


def test_leading_notes_and_headings_are_trimmed():
    body = ("(출신학교명을 나타내는 용어 사용 금지) [전문성] 지원 직무와 관련된 본인의 차별화된 "
            "역량은 무엇이며 어떻게 활용할 수 있을지 구체적으로 기술해 주십시오.")
    assert extract_questions([], body)[0].startswith("[전문성] 지원 직무와")


def test_glued_table_cells_are_not_mistaken_for_questions():
    """입사지원서를 통째로 읽으면 표의 여러 칸이 이어붙어 나온다.

    ")(" 는 옆 칸과 맞붙은 자리, "*" 는 서식 각주다. 둘 다 한 문장에는 나오지 않는다.
    """
    glued = ("여부)(장애대상 여부)(취업지원대상 여부)교육 및 자격사항 구분 교과목명 이수시간 "
             "직무관련 자격사항 자격증 등록번호 발생기관 취득일자에 대해 기술해 주십시오")
    footnote = ("기타 * 금전적 보상(有): 경력사항, 금전적 보상(無): 경험사항 "
                "* 기업명, 기관명 사용불가에 대하여 기술해 주십시오")
    assert extract_questions([], glued) == []
    assert extract_questions([], footnote) == []


def test_checkbox_runs_before_a_question_are_dropped():
    """체크박스 줄이 문항 앞에 붙어 나오면 떼어낸다.

    마지막 체크박스의 라벨 한 단어는 남을 수 있다(어디까지가 라벨인지 알 방법이 없다).
    문항 본문이 온전하고 체크박스가 사라지는 것까지가 보장 범위다.
    """
    body = ("□기타 □ 경험 □ 경력 □대기업 □중소기업 □공공기관 "
            "경험 혹은 경력사항란에 작성한 내용에 대해 상세히 기술해 주시기 바랍니다")
    questions = extract_questions([], body)
    assert len(questions) == 1
    assert "□" not in questions[0]
    assert questions[0].endswith("경험 혹은 경력사항란에 작성한 내용에 대해 상세히 기술해 주시기 바랍니다")


def test_circled_numbers_are_kept_as_question_numbers():
    body = ("자기소개서 … 각 항목 400자 이내 ① 조직의 구성원으로서 목표달성을 위해 "
            "노력한 경험에 대해 기술하시오")
    assert extract_questions([], body) == [
        "① 조직의 구성원으로서 목표달성을 위해 노력한 경험에 대해 기술하시오"
    ]


def test_document_handling_instructions_are_not_questions():
    """"~작성하시오"로 끝나도 증빙 서류 안내는 자소서 문항이 아니다."""
    assert extract_questions([], "경력증명서 등으로 추후 증빙 가능한 사항만 작성하시오") == []
    assert extract_questions(
        [], "건강보험자격득실확인서 발급이 가능한 경우에만 해당 내용을 기술해 주십시오") == []


def test_numbering_makes_each_question_visible():
    """줄글로 이어 붙이면 어디서 문항이 끊기는지 안 보인다."""
    assert format_questions(["[조직이해] 지원 동기를 기술해 주십시오",
                             "[전문역량] 보유 역량을 기술해 주십시오"]) == (
        "1. [조직이해] 지원 동기를 기술해 주십시오\n"
        "\n"
        "2. [전문역량] 보유 역량을 기술해 주십시오")


def test_own_numbering_survives_under_its_parent():
    """한수원처럼 대문항 아래 1-1, 1-2로 갈리는 양식은 그 번호를 살려 들여쓴다."""
    assert format_questions(["관련 경험을 기술해 주십시오",
                             "1-1. 언제, 어디서 하셨는지 기술해 주십시오",
                             "1-2. 개발한 역량을 기술해 주십시오",
                             "핵심 가치를 기술해 주십시오"]) == (
        "1. 관련 경험을 기술해 주십시오\n"
        "    1-1. 언제, 어디서 하셨는지 기술해 주십시오\n"
        "    1-2. 개발한 역량을 기술해 주십시오\n"
        "\n"
        "2. 핵심 가치를 기술해 주십시오")


def test_questions_that_all_carry_numbers_are_only_spaced_out():
    assert format_questions(["① 지원 동기를 기술해 주십시오",
                             "② 보유 역량을 기술해 주십시오"]) == (
        "① 지원 동기를 기술해 주십시오\n\n② 보유 역량을 기술해 주십시오")


def test_a_question_split_by_a_line_break_is_put_back_together():
    """"또한 ~"은 새 문항을 여는 말이 아니라 앞 문항의 뒷부분이다. 번호를 매기면
    있지도 않은 문항이 하나 더 생겨 버린다."""
    assert merge_continuations([
        "수행하고 싶은 직무를 선택하고 그 이유를 기술하시오",
        "또한 해당 직무에서 어떻게 기여하고 싶은지 기술해 주십시오",
        "핵심 역량은 무엇인지 서술해 주십시오",
    ]) == [
        "수행하고 싶은 직무를 선택하고 그 이유를 기술하시오 또한 해당 직무에서 어떻게 기여하고 싶은지 기술해 주십시오",
        "핵심 역량은 무엇인지 서술해 주십시오",
    ]


def test_evaluation_areas_are_read_but_are_not_questions():
    """근로복지공단 공고문의 "서류전형 평가기준"에 적힌 값이다.

    자기소개서로 무엇을 보는지이지 물어보는 문장이 아니다. 실제 문항은 입사지원
    사이트에 있다. 한때 이 값을 문항 칸에 넣었다가, 비워 두어야 할 자리에 옆에
    있던 다른 정보를 채운 것이라 되돌렸다.
    """
    body = ("4. 서류전형 평가기준 자기소개서(적/부) □ NCS직업기초능력을 반영한 자기소개서 적ㆍ부 평가 "
            "- 조직이해/지원동기, 직무이해/자기개발, 의사소통능력/대인관계능력, "
            "문제해결능력/자원관리능력, 직업윤리 (각 문항별 500자 이내 작성)")
    assert extract_areas(body) == [
        "조직이해/지원동기 (500자 이내)",
        "직무이해/자기개발 (500자 이내)",
        "의사소통능력/대인관계능력 (500자 이내)",
        "문제해결능력/자원관리능력 (500자 이내)",
        "직업윤리 (500자 이내)",
    ]


def test_a_list_with_a_length_limit_is_ignored_without_the_cover_letter_context():
    """자기소개서 이야기가 없으면 그냥 평가 항목 나열이다."""
    assert extract_areas("평가항목: 교육사항, 자격사항, 경력사항 (각 문항별 500자 이내 작성)") == []
