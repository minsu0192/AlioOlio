from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .domain import FilterRule, Posting, categorize

log = logging.getLogger(__name__)


FILTER_SEEDS = [
    ("고용형태", "정규직", "정규직", "official", True),
    ("고용형태", "무기계약직", "무기계약직", "official", False),
    ("고용형태", "계약직/비정규직", "비정규직", "official", True),
    ("고용형태", "체험형 인턴", "청년인턴(체험형)", "official", True),
    ("고용형태", "채용형 인턴", "청년인턴(채용형)", "official", False),
    ("근무분야", "전문직", "전문직", "official", True),
    ("포함 키워드", "변호사", "변호사", "include", False),
    ("제외 키워드", "예시: 육아휴직 대체", "육아휴직 대체", "exclude", False),
]

POSTING_PROPERTIES = {
    "공고명": {"title": {}},
    "기관": {"rich_text": {}},
    "지원기간": {"date": {}},
    "캘린더 표시": {"checkbox": {}},
    "필터 일치": {"checkbox": {}},
    "상태": {"select": {"options": [{"name": "진행중", "color": "green"}, {"name": "마감", "color": "gray"}]}},
    "고용형태": {"multi_select": {}}, "근무분야": {"multi_select": {}},
    "NCS": {"multi_select": {}}, "근무지": {"multi_select": {}}, "학력": {"multi_select": {}},
    "채용구분": {"multi_select": {}}, "채용인원": {"number": {"format": "number"}},
    "ALIO 링크": {"url": {}}, "ALIO ID": {"number": {"format": "number"}},
    "최초등록일": {"date": {}}, "마지막 확인": {"date": {}}, "알림완료": {"checkbox": {}},
    "지원 관리 등록": {"checkbox": {}}, "지원 현황 링크": {"url": {}},
    # 공고문에서 뽑아 채우는 값. 사람이 직접 고치는 칸이기도 하므로 비어 있을 때만 쓴다.
    "관심": {"checkbox": {}},
    "서류발표일": {"date": {}}, "필기일정": {"date": {}}, "필기발표일": {"date": {}},
    "면접일정": {"date": {}}, "최종발표일": {"date": {}},
    "자소서 문항": {"rich_text": {}}, "전형 메모": {"rich_text": {}},
    "직무기술서 링크": {"url": {}},
}

# 공고문에서 추출해 채우는 날짜 속성. 사용자가 손으로 넣은 값은 덮지 않는다.
DETAIL_DATE_PROPERTIES = ("서류발표일", "필기일정", "필기발표일", "면접일정", "최종발표일")

FILTER_PROPERTIES = {
    "필터명": {"title": {}}, "사용": {"checkbox": {}},
    "분류": {"select": {}}, "값": {"rich_text": {}},
    "규칙": {"select": {"options": [
        {"name": "official", "color": "blue"}, {"name": "include", "color": "green"},
        {"name": "exclude", "color": "red"},
    ]}},
}


def _text(value: str) -> list[dict]:
    return [{"type": "text", "text": {"content": value[:2000]}}] if value else []


def _multi(values: list[str]) -> dict:
    """다중 선택 값. 노션은 옵션 이름에 쉼표를 허용하지 않아 가운뎃점으로 바꾼다.

    ALIO는 "기타(기능노무직(취사원, 보조원))"처럼 괄호 안에서 쉼표를 쓴다. 쉼표째로
    보내면 400이 나고, 쉼표로 쪼개면 "보조원))" 같은 조각이 선택지로 남는다.
    필터는 저장된 원래 값으로 판정하므로 여기서만 바꿔 준다.
    """
    return {"multi_select": [{"name": item.replace(",", " ·")[:100]} for item in values[:100]]}


def _plain_text(prop: dict | None) -> str:
    return "".join(part.get("plain_text", "") for part in (prop or {}).get("rich_text", []))


def _is_empty(prop: dict | None) -> bool:
    """조회로 받은 속성이 비어 있는지. 채워져 있으면 추출값으로 덮지 않는다."""
    if not prop:
        return True
    kind = prop.get("type")
    if kind:
        return not prop.get(kind)
    return not (prop.get("date") or prop.get("rich_text") or prop.get("url"))


class NotionClient:
    def __init__(self, token: str, version: str = "2026-03-11", transport=None):
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1", timeout=30, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"},
        )

    def request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(5):
            try:
                response = self.client.request(method, path, **kwargs)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError,
                    httpx.ReadTimeout) as error:
                # 응답을 받다 연결이 끊기는 일이 있다. 여기서 포기하면 그 시각
                # 동기화가 통째로 날아가므로 상태 코드 재시도와 같이 취급한다.
                if attempt == 4:
                    raise
                log.warning("노션 연결이 끊겨 다시 시도합니다 (%s): %s", path, error)
                time.sleep(2 ** attempt)
                continue
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response.json()
            if attempt == 4:
                response.raise_for_status()
            time.sleep(float(response.headers.get("Retry-After", 2 ** attempt)))
        raise RuntimeError("unreachable")

    def bootstrap(self, parent_page_id: str) -> dict[str, str]:
        filter_db = self._create_database(parent_page_id, "ALIO 필터 설정", FILTER_PROPERTIES)
        posting_db = self._create_database(parent_page_id, "ALIO 채용공고", POSTING_PROPERTIES)
        filter_ds = filter_db["data_sources"][0]["id"]
        posting_ds = posting_db["data_sources"][0]["id"]
        for category, name, value, rule, enabled in FILTER_SEEDS:
            self.request("POST", "/pages", json={
                "parent": {"type": "data_source_id", "data_source_id": filter_ds},
                "properties": {
                    "필터명": {"title": _text(name)}, "사용": {"checkbox": enabled},
                    "분류": {"select": {"name": category}}, "값": {"rich_text": _text(value)},
                    "규칙": {"select": {"name": rule}},
                },
            })
        self._create_views(posting_db["id"], posting_ds)
        return {
            "filter_database_id": filter_db["id"], "filter_data_source_id": filter_ds,
            "posting_database_id": posting_db["id"], "posting_data_source_id": posting_ds,
        }

    def _create_database(self, parent: str, title: str, properties: dict) -> dict:
        return self.request("POST", "/databases", json={
            "parent": {"type": "page_id", "page_id": parent}, "title": _text(title),
            "is_inline": True, "initial_data_source": {"properties": properties},
        })

    def _create_views(self, database_id: str, data_source_id: str) -> None:
        # 마감된 공고는 캘린더에서 뺀다. 기록은 "관심 공고" 표에 그대로 남는다.
        filters = {"and": [
            {"property": "필터 일치", "checkbox": {"equals": True}},
            {"property": "캘린더 표시", "checkbox": {"equals": True}},
            {"property": "상태", "select": {"equals": "진행중"}},
        ]}
        # 표에는 마감이 가까운 순으로 세운다. 정렬이 없으면 만들어진 순서대로 나와
        # 어느 공고가 급한지 보이지 않는다. 칸도 필요한 것만 편다(기본은 35개 전부).
        listed = ["공고명", "기관", "구분", "관심", "지원기간", "상태", "서류발표일", "필기일정",
                  "필기발표일", "면접일정", "최종발표일", "근무지", "근무분야", "학력", "채용인원",
                  "ALIO 링크"]
        views = [
            ("지원 캘린더", "calendar", filters, None, None),
            ("관심 공고", "table", {"property": "필터 일치", "checkbox": {"equals": True}},
             [{"property": "지원기간", "direction": "ascending"}], listed),
            ("제외한 공고", "table", {"property": "캘린더 표시", "checkbox": {"equals": False}},
             [{"property": "지원기간", "direction": "descending"}],
             ["공고명", "기관", "구분", "지원기간", "상태", "고용형태", "근무지", "근무분야", "ALIO 링크"]),
        ]
        for name, kind, view_filter, sorts, columns in views:
            body = {"database_id": database_id, "data_source_id": data_source_id,
                    "name": name, "type": kind, "filter": view_filter}
            if sorts:
                body["sorts"] = sorts
            if columns:
                body["display_properties"] = columns
            self.request("POST", "/views", json=body)

    def query(self, data_source_id: str, payload: dict | None = None) -> list[dict]:
        body = dict(payload or {})
        results: list[dict] = []
        while True:
            response = self.request("POST", f"/data_sources/{data_source_id}/query", json=body)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            body["start_cursor"] = response["next_cursor"]

    def filter_rules(self, data_source_id: str) -> list[FilterRule]:
        rules = []
        for page in self.query(data_source_id):
            p = page["properties"]
            title = "".join(x.get("plain_text", "") for x in p["필터명"].get("title", []))
            value = "".join(x.get("plain_text", "") for x in p["값"].get("rich_text", []))
            rules.append(FilterRule(
                enabled=p["사용"].get("checkbox", False), category=(p["분류"].get("select") or {}).get("name", ""),
                name=title, value=value, rule=(p["규칙"].get("select") or {}).get("name", "official"),
            ))
        return rules

    def upsert_posting(self, data_source_id: str, posting: Posting, matched: bool,
                       page_id: str | None, delivered: bool) -> str:
        # 캘린더 카드는 "기관 · 직군"만 간결하게 보여준다(연도/차수 등은 생략).
        # 전체 공고 제목은 ALIO 링크에서 확인할 수 있다.
        roles = [w for w in posting.work_areas if w in ("사무직", "행정직")] or posting.work_areas[:1]
        card_label = posting.organization or posting.title
        if posting.organization and roles:
            card_label = f"{posting.organization} · {'/'.join(roles)}"
        properties: dict[str, Any] = {
            "공고명": {"title": _text(card_label)}, "기관": {"rich_text": _text(posting.organization)},
            "지원기간": {"date": {"start": posting.start_date.isoformat(), "end": posting.end_date.isoformat()}},
            "필터 일치": {"checkbox": matched}, "상태": {"select": {"name": posting.status or "진행중"}},
            # 캘린더 카드 색이 여기서 나온다. 비어 있으면 색 없이 표시된다.
            "구분": {"select": {"name": categorize(posting.employment_types)}},
            "고용형태": _multi(posting.employment_types), "근무분야": _multi(posting.work_areas),
            "NCS": _multi(posting.ncs), "근무지": _multi(posting.locations), "학력": _multi(posting.education),
            "채용구분": _multi(posting.career_types), "채용인원": {"number": posting.headcount},
            "ALIO 링크": {"url": posting.url}, "ALIO ID": {"number": posting.seq},
            "최초등록일": {"date": {"start": posting.registered_date.isoformat()}},
            "마지막 확인": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "알림완료": {"checkbox": delivered},
        }
        if page_id:
            self.request("PATCH", f"/pages/{page_id}", json={"properties": properties})
            return page_id
        properties["캘린더 표시"] = {"checkbox": True}
        page = self.request("POST", "/pages", json={
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        })
        return page["id"]

    def close_expired_postings(self, posting_data_source_id: str, expired_seqs: set[int]) -> int:
        """마감일이 지난 공고를 "마감"으로 표시한다.

        상태는 ALIO가 목록에 실어 주는 값이라, 한 번 등록된 공고는 마감돼도 계속
        "진행중"으로 남는다. 마감 여부는 호출부가 로컬 마감일로 판정해 넘긴다 —
        노션의 날짜 필터로 거르면 안 된다. `지원기간`은 기간 속성이고 before/after는
        기간의 **시작**과 비교하므로, 접수 시작일만 지나면 전부 걸려 버린다.
        """
        closed = 0
        for page in self.query(posting_data_source_id, {
            "filter": {"property": "상태", "select": {"equals": "진행중"}}
        }):
            seq = page["properties"].get("ALIO ID", {}).get("number")
            if seq is None or int(seq) not in expired_seqs:
                continue
            self.request("PATCH", f"/pages/{page['id']}", json={
                "properties": {"상태": {"select": {"name": "마감"}}}
            })
            closed += 1
        return closed

    def interest_postings(self, posting_data_source_id: str) -> list[dict]:
        return self.query(posting_data_source_id, {
            "filter": {"property": "관심", "checkbox": {"equals": True}}
        })

    def update_posting_details(self, page_id: str, current: dict, dates: dict[str, str],
                               job_description_url: str = "", questions: str = "",
                               memo: str = "", profile: dict[str, str] | None = None,
                               application_period: list[str] | None = None) -> list[str]:
        """추출 결과를 페이지에 쓰되, 이미 값이 있는 속성은 건드리지 않는다.

        `current`는 조회로 받은 properties 원본이다. 사용자가 노션에서 직접 고친 날짜를
        다음 동기화가 되돌려 놓으면 이 기능은 쓸모가 없어지므로, 빈 칸 채우기만 한다.
        """
        properties: dict[str, Any] = {}
        for name, value in dates.items():
            if _is_empty(current.get(name)):
                properties[name] = {"date": {"start": value}}
        if job_description_url and _is_empty(current.get("직무기술서 링크")):
            properties["직무기술서 링크"] = {"url": job_description_url}
        # ALIO의 지원기간이 공고기간일 때가 있다(근로복지공단: 공고 8.5.~, 접수 8.12.~).
        # 지원자에게 필요한 것은 실제로 지원서를 낼 수 있는 기간이므로 그대로 덮는다.
        if application_period:
            listed = (current.get("지원기간", {}) or {}).get("date") or {}
            if [listed.get("start"), listed.get("end")] != application_period:
                properties["지원기간"] = {"date": {"start": application_period[0],
                                                "end": application_period[1]}}
        if questions and _is_empty(current.get("자소서 문항")):
            properties["자소서 문항"] = {"rich_text": _text(questions)}
        # 직무기술서에서 뽑은 값. 직접 정리해 둔 내용을 덮지 않도록 빈 칸만 채운다.
        for name, value in (profile or {}).items():
            if value and _is_empty(current.get(name)):
                properties[name] = {"rich_text": _text(value)}
        # 전형 메모는 사람이 쓰는 칸이 아니라 무엇을 어디서 뽑았는지 남기는 기록이므로
        # 추출 결과가 바뀌면 따라 갱신한다. 다만 내용이 같은데 매번 쓰면 노션의 편집
        # 시각만 계속 바뀌고 API 호출도 낭비된다.
        if memo and _plain_text(current.get("전형 메모")) != _text(memo)[0]["text"]["content"]:
            properties["전형 메모"] = {"rich_text": _text(memo)}
        if properties:
            self.request("PATCH", f"/pages/{page_id}", json={"properties": properties})
        return sorted(properties)

    def ensure_schedule_events(self, schedule_data_source_id: str, posting_page_id: str,
                               organization: str, stages: dict[str, tuple[str, str | None]]) -> int:
        """'관심 전형 일정' DB에 단계별 행을 만든다. 이미 있는 유형은 건너뛴다.

        stages: {유형: (확정상태, 날짜 또는 None)}
        """
        existing = {}
        for page in self.query(schedule_data_source_id, {
            "filter": {"property": "공고", "relation": {"contains": posting_page_id}}
        }):
            name = (page["properties"].get("유형", {}).get("select") or {}).get("name")
            if name:
                existing[name] = page
        created = 0
        for stage, (status, day) in stages.items():
            if stage in existing:
                # 처음엔 날짜를 몰라 "미정"으로 만들어 둔 행이라도, 나중에 공고문에서
                # 날짜를 읽어내면 채워 준다. 사람이 손으로 넣은 값은 건드리지 않는다.
                created += int(self._fill_undecided(existing[stage], day))
                continue
            properties: dict[str, Any] = {
                "일정명": {"title": _text(f"{organization} {stage}")},
                "유형": {"select": {"name": stage}},
                "확정상태": {"select": {"name": status}},
                "기관": {"rich_text": _text(organization)},
                "공고": {"relation": [{"id": posting_page_id}]},
            }
            if day:
                properties["일정일"] = {"date": {"start": day}}
            self.request("POST", "/pages", json={
                "parent": {"type": "data_source_id", "data_source_id": schedule_data_source_id},
                "properties": properties,
            })
            created += 1
        return created

    def ensure_deadline_events(self, schedule_data_source_id: str, posting_page_id: str,
                               organization: str, deadlines: list[dict]) -> int:
        """제출·등록 기한을 캘린더에 올린다. 한 공고에 여럿일 수 있어 이름으로 구분한다.

        시험을 보는 날이 아니라 지원자가 직접 해야 하는 일이라 유형을 "제출기한"으로
        따로 둔다. 놓치면 그대로 탈락하는 날짜다.
        """
        existing = {self._title_of(page) for page in self.query(schedule_data_source_id, {
            "filter": {"property": "공고", "relation": {"contains": posting_page_id}}
        })}
        created = 0
        for item in deadlines:
            title = f"{organization} {item['label']}"
            if title in existing:
                continue
            span = {"start": item["day"]} if not item.get("start") else {
                "start": item["start"], "end": item["day"]}
            self.request("POST", "/pages", json={
                "parent": {"type": "data_source_id", "data_source_id": schedule_data_source_id},
                "properties": {
                    "일정명": {"title": _text(title)},
                    "유형": {"select": {"name": "제출기한"}},
                    "확정상태": {"select": {"name": "예정"}},
                    "기관": {"rich_text": _text(organization)},
                    "공고": {"relation": [{"id": posting_page_id}]},
                    "일정일": {"date": span},
                },
            })
            created += 1
        return created

    @staticmethod
    def _title_of(page: dict) -> str:
        return "".join(part.get("plain_text", "")
                       for part in page["properties"].get("일정명", {}).get("title", []))

    def _fill_undecided(self, page: dict, day: str | None) -> bool:
        properties = page.get("properties", {})
        status = (properties.get("확정상태", {}).get("select") or {}).get("name")
        if not day or status != "미정" or not _is_empty(properties.get("일정일")):
            return False
        self.request("PATCH", f"/pages/{page['id']}", json={"properties": {
            "일정일": {"date": {"start": day}},
            "확정상태": {"select": {"name": "예정"}},
        }})
        return True

    def application_requests(self, posting_data_source_id: str) -> list[dict]:
        return self.query(posting_data_source_id, {
            "filter": {"and": [
                {"property": "지원 관리 등록", "checkbox": {"equals": True}},
                {"property": "지원 현황 링크", "url": {"is_empty": True}},
            ]}
        })

    def ensure_application(self, application_data_source_id: str, posting: Posting,
                           posting_page_id: str = "") -> str:
        existing = self.query(application_data_source_id, {
            "filter": {"property": "공고링크", "url": {"equals": posting.url}}, "page_size": 1,
        })
        if existing:
            if posting_page_id:
                self.link_application(existing[0], posting_page_id)
            return existing[0]["url"]
        properties: dict[str, Any] = {
            "회사명": {"title": _text(posting.organization)},
            "공고링크": {"url": posting.url},
            "마감일": {"date": {"start": posting.end_date.isoformat()}},
            "유형": {"select": {"name": "공공기관"}},
            "진행상태": {"status": {"name": "시작 전"}},
            "결과": {"select": {"name": "미제출"}},
            "직무": {"rich_text": _text(", ".join(posting.work_areas or posting.ncs))},
        }
        if posting_page_id:
            properties["ALIO 공고"] = {"relation": [{"id": posting_page_id}]}
        page = self.request("POST", "/pages", json={
            "parent": {"type": "data_source_id", "data_source_id": application_data_source_id},
            "properties": properties,
        })
        return page["url"]

    def link_application(self, application_page: dict, posting_page_id: str) -> bool:
        """지원 현황 행을 공고 쪽에 이어 붙인다. 이미 이어져 있으면 건드리지 않는다."""
        linked = application_page["properties"].get("ALIO 공고", {}).get("relation", [])
        if any(item["id"].replace("-", "") == posting_page_id.replace("-", "") for item in linked):
            return False
        self.request("PATCH", f"/pages/{application_page['id']}", json={"properties": {
            "ALIO 공고": {"relation": linked + [{"id": posting_page_id}]}
        }})
        return True

    def applications(self, application_data_source_id: str) -> list[dict]:
        return self.query(application_data_source_id)

    def set_application_link(self, posting_page_id: str, application_url: str) -> None:
        self.request("PATCH", f"/pages/{posting_page_id}", json={
            "properties": {"지원 현황 링크": {"url": application_url}}
        })
