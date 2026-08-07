from alio_olio.job_description import extract_profile

# 한국수력원자력 직무설명자료의 실제 셀. 굵은 글씨가 글자마다 두 번 찍혀 있고
# 라벨이 첫 칸에 온다.
KHNP = [[
    ["주주요요사사업업", "원원자자력력, 수수력력 발발전전소소 건건설설 및및 운운영영", ""],
    ["직직무무수수행행 내내용용", "○○ (경경영영기기획획) 경경영영목목표표를를 달달성성하하는는 업업무무", ""],
    ["필필요요지지식식", "○○ (공공통통) 업업무무 관관련련 법법률률 체체계계 이이해해", ""],
    ["직직업업기기초초 능능력력", "의의사사소소통통능능력력, 수수리리능능력력", ""],
]]

# 근로복지공단은 첫 칸에 세부 직무명이 오고 라벨이 두 번째 칸에 온다.
COMWEL = [[
    ["고객관리", "직무수행내용", "◦고객이 신청한 민원에 대해 서비스를 제공하는 업무"],
    ["", "필요기술", "◦고객 유형별 대응 능력 ◦통계 프로그램 활용 능력"],
    ["", "직무수행태도", "◦고객 특성 이해 ◦개인정보 관리 책임감"],
]]


def test_labels_in_the_first_column_with_doubled_characters():
    profile = extract_profile(KHNP)
    assert profile["주요 업무"].startswith("원자력, 수력 발전소 건설 및 운영")
    assert "경영목표를 달성하는 업무" in profile["주요 업무"]
    assert profile["필요 지식·기술"] == "(공통) 업무 관련 법률 체계 이해"
    assert profile["직무 핵심역량"] == "의사소통능력, 수리능력"


def test_labels_in_the_second_column():
    profile = extract_profile(COMWEL)
    assert profile["주요 업무"] == "고객이 신청한 민원에 대해 서비스를 제공하는 업무"
    assert profile["필요 지식·기술"] == "고객 유형별 대응 능력 · 통계 프로그램 활용 능력"


def test_the_attitude_row_is_not_mistaken_for_the_duties_row():
    """"직무수행태도"가 "직무수행"으로 시작한다고 주요 업무에 들어가면 안 된다."""
    assert "고객 특성 이해" not in extract_profile(COMWEL).get("주요 업무", "")
    assert "직무수행태도" not in extract_profile(COMWEL)


def test_a_very_long_field_is_cut_with_a_pointer_to_the_original():
    """한수원은 채용분야가 열 개가 넘어 필요지식만 1만 자가 넘는다."""
    rows = [["필요지식", f"{n}번 분야 " + "가나다라마바사아자차카타파하 " * 20] for n in range(20)]
    value = extract_profile([rows])["필요 지식·기술"]
    assert len(value) < 1600
    assert value.endswith("직무기술서 링크 참고)")


def test_empty_tables_yield_nothing():
    assert extract_profile([]) == {}
    assert extract_profile([[["필요지식", "○"]]]) == {}


def test_bullet_marks_are_normalised_without_eating_english():
    """기관마다 ○ ◦ ㅇ o 를 섞어 쓴다. 다만 영어 단어 속 o는 건드리면 안 된다."""
    rows = [["필요지식", "ㅇ 도로교통법 o 차량점검 방법 ○ ○ 안전운전 · Word, Excel 활용"]]
    value = extract_profile([rows])["필요 지식·기술"]
    assert value == "도로교통법 · 차량점검 방법 · 안전운전 · Word, Excel 활용"


def test_label_variants_across_agencies():
    """같은 칸을 기관마다 다르게 부르고, 앞에 기관명을 붙이기도 한다."""
    rows = [["한국부동산원주요사업", "부동산 시장 안정과 질서 유지를 위한 조사 업무"],
            ["직업공통능력", "의사소통능력, 문제해결능력"]]
    profile = extract_profile([rows])
    assert profile["주요 업무"] == "부동산 시장 안정과 질서 유지를 위한 조사 업무"
    assert profile["직무 핵심역량"] == "의사소통능력, 문제해결능력"


# 한국해양교통안전공단 직무기술서(HWP)의 실제 본문. 표 정보가 없어 라벨과 값이
# 한 줄로 이어져 나오고, 항목 이름은 띄어 쓴다.
KOMSA_TEXT = (
    "채용 분야 행정직 (일반행정) 분류 체계 대분류 02. 경영·회계·사무 "
    "공단 주요 사업 소개 ○ 해양교통안전에 관한 교육·계몽·방송 및 홍보 "
    "직무 수행 내용 ○ (사무행정) 사무행정, 사무환경 조성, 문서관리 업무 "
    "능력 단위 ○ (사무행정) 01.문서작성, 02.문서관리 "
    "자격 기준 ○ 공고문 참고 "
    "필요 지식 ○ (사무행정) 문서의 체계, 자료정리 분류, 문서관리규정 "
    "필요 기술 ○ (사무행정) 자료검색 능력, 업무용 프로그램 활용능력 "
    "직무 수행 태도 ○ 꼼꼼함, 세밀하게 자료를 분석하려는 노력 "
    "직업 공통 능력 ○ 의사소통능력, 문제해결능력, 디지털능력 "
    "참고 사이트 ○ 국가직무능력표준 홈페이지"
)


def test_a_document_without_tables_is_read_from_its_body():
    """HWP v5는 표 정보를 남기지 않는다. 라벨 사이를 잘라 읽는다."""
    profile = extract_profile([], KOMSA_TEXT)
    assert profile["주요 업무"] == (
        "해양교통안전에 관한 교육 · 계몽 · 방송 및 홍보\n"
        "(사무행정) 사무행정, 사무환경 조성, 문서관리 업무")
    assert profile["직무 핵심역량"] == "의사소통능력, 문제해결능력, 디지털능력"


def test_the_next_label_ends_the_value():
    """"능력 단위", "자격 기준"을 모르면 그 내용까지 앞 항목에 딸려 들어간다."""
    profile = extract_profile([], KOMSA_TEXT)
    assert "01.문서작성" not in profile["주요 업무"]
    assert "공고문 참고" not in profile["주요 업무"]
    assert "꼼꼼함" not in profile["필요 지식·기술"]


def test_tables_win_when_both_are_available():
    tables = [[["필요지식", "표에서 읽은 값입니다"]]]
    assert extract_profile(tables, KOMSA_TEXT)["필요 지식·기술"] == "표에서 읽은 값입니다"
