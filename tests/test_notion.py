import json

import httpx

from datetime import date

from alio_olio.domain import Posting
from alio_olio.notion import NotionClient


def client(handler) -> NotionClient:
    return NotionClient("token", transport=httpx.MockTransport(handler))


def test_details_never_overwrite_values_already_in_notion():
    patches = []
    def handler(request):
        patches.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page"})

    current = {
        # 사용자가 노션에서 직접 고친 값
        "필기일정": {"type": "date", "date": {"start": "2026-09-06"}},
        "전형 메모": {"type": "rich_text", "rich_text": [{"plain_text": "직접 확인함"}]},
        # 아직 빈 칸
        "서류발표일": {"type": "date", "date": None},
        "직무기술서 링크": {"type": "url", "url": None},
    }
    written = client(handler).update_posting_details(
        "page", current,
        {"서류발표일": "2026-08-21", "필기일정": "2026-09-05"},
        job_description_url="https://alio/jd", memo="추출 근거",
    )
    # 필기일정은 사람이 고친 값이라 빠지고, 전형 메모는 추출 기록이라 매번 갱신된다.
    assert written == ["서류발표일", "전형 메모", "직무기술서 링크"]
    assert patches[0]["서류발표일"]["date"]["start"] == "2026-08-21"
    assert patches[0]["전형 메모"]["rich_text"][0]["text"]["content"] == "추출 근거"


def test_no_request_is_sent_when_nothing_is_empty():
    def handler(request):
        raise AssertionError(f"불필요한 요청: {request.method} {request.url}")

    current = {"서류발표일": {"type": "date", "date": {"start": "2026-08-21"}}}
    assert client(handler).update_posting_details("page", current, {"서류발표일": "2026-08-21"}) == []


def test_schedule_events_skip_types_that_already_exist():
    created = []
    def handler(request):
        if request.url.path.endswith("/query"):
            existing = [{"properties": {"유형": {"select": {"name": "필기"}}}}]
            return httpx.Response(200, json={"results": existing, "has_more": False})
        created.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "event"})

    count = client(handler).ensure_schedule_events(
        "schedule", "page-1", "한국부동산원",
        {"필기": ("예정", "2026-09-05"), "필기발표": ("미정", None)},
    )
    assert count == 1
    assert [p["유형"]["select"]["name"] for p in created] == ["필기발표"]
    event = created[0]
    assert event["확정상태"]["select"]["name"] == "미정"
    assert "일정일" not in event  # 날짜 미정인 행에 가짜 날짜를 넣지 않는다
    assert event["공고"]["relation"] == [{"id": "page-1"}]
    assert event["일정명"]["title"][0]["text"]["content"] == "한국부동산원 필기발표"


def test_interest_filter_targets_the_checkbox():
    seen = {}
    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": [], "has_more": False})

    client(handler).interest_postings("postings")
    assert seen["filter"] == {"property": "관심", "checkbox": {"equals": True}}


def test_undecided_event_gets_filled_when_the_date_is_found_later():
    patches = []
    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"has_more": False, "results": [{
                "id": "event-1",
                "properties": {"유형": {"select": {"name": "서류발표"}},
                               "확정상태": {"select": {"name": "미정"}},
                               "일정일": {"type": "date", "date": None}},
            }]})
        patches.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "event-1"})

    count = client(handler).ensure_schedule_events(
        "schedule", "page-1", "시청자미디어재단", {"서류발표": ("예정", "2026-08-28")})
    assert count == 1
    assert patches[0]["일정일"]["date"]["start"] == "2026-08-28"
    assert patches[0]["확정상태"]["select"]["name"] == "예정"


def test_event_the_user_already_dated_is_left_alone():
    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"has_more": False, "results": [{
                "id": "event-1",
                "properties": {"유형": {"select": {"name": "필기"}},
                               "확정상태": {"select": {"name": "미정"}},
                               "일정일": {"type": "date", "date": {"start": "2026-09-06"}}},
            }]})
        raise AssertionError("사람이 넣은 날짜를 덮어썼습니다")

    assert client(handler).ensure_schedule_events(
        "schedule", "page-1", "한국부동산원", {"필기": ("예정", "2026-09-05")}) == 0


def test_an_unchanged_memo_is_not_rewritten():
    """전형 메모는 추출 결과가 바뀌면 갱신하지만, 같은 내용을 매번 다시 쓰지는 않는다.

    필터 갱신이 5분마다 돌기 때문에, 이 검사가 없으면 하루 천 번 넘게 노션 페이지를
    건드려 "최종 편집" 시각만 계속 바뀐다.
    """
    def handler(request):
        raise AssertionError(f"불필요한 요청: {request.method} {request.url}")

    current = {"전형 메모": {"type": "rich_text",
                          "rich_text": [{"plain_text": "추출 경로: 표 / 읽은 표 3개"}]}}
    assert client(handler).update_posting_details(
        "page", current, {}, memo="추출 경로: 표 / 읽은 표 3개") == []


def test_a_changed_memo_is_written():
    patches = []
    def handler(request):
        patches.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page"})

    current = {"전형 메모": {"type": "rich_text", "rich_text": [{"plain_text": "예전 근거"}]}}
    assert client(handler).update_posting_details("page", current, {}, memo="새 근거") == ["전형 메모"]
    assert patches[0]["전형 메모"]["rich_text"][0]["text"]["content"] == "새 근거"


def test_posting_gets_a_category_so_the_calendar_can_colour_it():
    """캘린더 카드 색은 "구분" 선택 속성에서 나온다. 비면 색 없이 표시된다."""
    created = []
    def handler(request):
        created.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page"})

    posting = Posting(1, "기관", "공고", date(2026, 8, 1), date(2026, 8, 20), date(2026, 8, 1),
                      employment_types=["무기계약직", "비정규직", "청년인턴(체험형)"])
    client(handler).upsert_posting("ds", posting, True, None, False)
    assert created[0]["구분"]["select"]["name"] == "체험형인턴"


def test_expired_postings_are_marked_closed_once():
    """상태는 ALIO가 주는 값이라 한 번 등록되면 마감돼도 "진행중"으로 남는다.

    마감일이 지났는지는 우리가 아는 사실이므로 직접 고친다. 이미 "마감"인 공고는
    조회 조건에서 빠지므로 같은 페이지를 다시 쓰지 않는다.
    """
    queries, patches = [], []
    def handler(request):
        if request.url.path.endswith("/query"):
            queries.append(json.loads(request.content)["filter"])
            return httpx.Response(200, json={"has_more": False, "results": [
                {"id": "page-1", "properties": {"ALIO ID": {"number": 101}}},
                {"id": "page-2", "properties": {"ALIO ID": {"number": 102}}},
            ]})
        patches.append(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page-1"})

    assert client(handler).close_expired_postings("postings", {101}) == 1
    # 노션 날짜 필터를 쓰면 안 된다. "지원기간"은 기간 속성이라 before/after가 기간의
    # 시작과 비교되어, 접수 시작일만 지난 진행 중 공고까지 전부 마감 처리된다.
    assert queries[0] == {"property": "상태", "select": {"equals": "진행중"}}
    assert patches[0]["상태"]["select"]["name"] == "마감"
    assert len(patches) == 1  # 마감일이 안 지난 102는 건드리지 않는다
