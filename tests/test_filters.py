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


def test_include_and_exclude_keywords():
    include = FilterRule(True, "포함 키워드", "변호사", "변호사", "include")
    exclude = FilterRule(True, "제외 키워드", "대체", "대체인력", "exclude")
    assert matches(posting(), [include, exclude])
    assert not matches(posting(detail_text="변호사 대체인력"), [include, exclude])
    assert not matches(posting(detail_text="회계사"), [include])
