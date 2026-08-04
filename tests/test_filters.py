from datetime import date

from alio_olio.domain import FilterRule, Posting
from alio_olio.filters import matches


def posting(**overrides):
    values = dict(
        seq=1, organization="법률구조공단", title="전문직 채용", start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 20), registered_date=date(2026, 8, 1),
        employment_types=["정규직"], work_areas=["전문직"], locations=["서울"],
        detail_text="변호사 자격 소지자 우대",
    )
    values.update(overrides)
    return Posting(**values)


def test_groups_are_and_and_options_inside_group_are_or():
    rules = [
        FilterRule(True, "고용형태", "정규직", "정규직"),
        FilterRule(True, "고용형태", "비정규직", "비정규직"),
        FilterRule(True, "근무지", "서울", "서울"),
    ]
    assert matches(posting(), rules)
    assert not matches(posting(locations=["부산"]), rules)


def test_include_keyword_searches_the_whole_posting():
    include = FilterRule(True, "포함 키워드", "변호사", "변호사", "include")
    assert matches(posting(), [include])
    assert not matches(posting(detail_text="회계사"), [include])


def test_exclude_keyword_only_looks_at_the_title():
    """제외 키워드는 제목만 본다.

    상세 본문에는 "장애인 의무고용", "보훈대상자 가점" 같은 문구가 거의 모든 공고에
    들어 있어, 본문까지 검사하면 정상 공고가 무더기로 탈락한다.
    """
    exclude = FilterRule(True, "제외 키워드", "대체", "대체인력", "exclude")
    assert not matches(posting(title="육아휴직 대체인력 채용"), [exclude])
    assert matches(posting(detail_text="대체인력 지정 여부: 아니오"), [exclude])


def test_an_exclude_word_inside_the_organisation_name_is_ignored():
    """기관명에 든 단어로 제외되면 안 된다.

    "한국장애인고용공단"의 공고가 제외 키워드 "장애" 때문에 통째로 사라졌었다.
    반대로 "청년인턴(장애)"처럼 제목에 붙은 전형 구분은 계속 걸러야 한다.
    """
    exclude = FilterRule(True, "제외 키워드", "장애", "장애", "exclude")
    keeps = posting(organization="한국장애인고용공단",
                    title="[한국장애인고용공단 광주지역본부] 2026년 기간제근로자(체험형 청년인턴) 채용")
    drops = posting(organization="학교법인한국폴리텍",
                    title="한국폴리텍대학 대전캠퍼스 청년인턴(장애) 채용 공고")
    assert matches(keeps, [exclude])
    assert not matches(drops, [exclude])
