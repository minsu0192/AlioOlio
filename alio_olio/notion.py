from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .domain import FilterRule, Posting


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
}

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
    return {"multi_select": [{"name": item[:100]} for item in values[:100]]}


class NotionClient:
    def __init__(self, token: str, version: str = "2026-03-11", transport=None):
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1", timeout=30, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"},
        )

    def request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(5):
            response = self.client.request(method, path, **kwargs)
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
        filters = {"and": [
            {"property": "필터 일치", "checkbox": {"equals": True}},
            {"property": "캘린더 표시", "checkbox": {"equals": True}},
        ]}
        views = [
            ("지원 캘린더", "calendar", filters),
            ("관심 공고", "table", {"property": "필터 일치", "checkbox": {"equals": True}}),
            ("제외한 공고", "table", {"property": "캘린더 표시", "checkbox": {"equals": False}}),
        ]
        for name, kind, view_filter in views:
            self.request("POST", "/views", json={
                "database_id": database_id, "data_source_id": data_source_id,
                "name": name, "type": kind, "filter": view_filter,
            })

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
        properties: dict[str, Any] = {
            "공고명": {"title": _text(posting.title)}, "기관": {"rich_text": _text(posting.organization)},
            "지원기간": {"date": {"start": posting.start_date.isoformat(), "end": posting.end_date.isoformat()}},
            "필터 일치": {"checkbox": matched}, "상태": {"select": {"name": posting.status or "진행중"}},
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

    def application_requests(self, posting_data_source_id: str) -> list[dict]:
        return self.query(posting_data_source_id, {
            "filter": {"and": [
                {"property": "지원 관리 등록", "checkbox": {"equals": True}},
                {"property": "지원 현황 링크", "url": {"is_empty": True}},
            ]}
        })

    def ensure_application(self, application_data_source_id: str, posting: Posting) -> str:
        existing = self.query(application_data_source_id, {
            "filter": {"property": "공고링크", "url": {"equals": posting.url}}, "page_size": 1,
        })
        if existing:
            return existing[0]["url"]
        page = self.request("POST", "/pages", json={
            "parent": {"type": "data_source_id", "data_source_id": application_data_source_id},
            "properties": {
                "회사명": {"title": _text(posting.organization)},
                "공고링크": {"url": posting.url},
                "마감일": {"date": {"start": posting.end_date.isoformat()}},
                "유형": {"select": {"name": "공공기관"}},
                "진행상태": {"status": {"name": "시작 전"}},
                "결과": {"select": {"name": "미제출"}},
                "직무": {"rich_text": _text(", ".join(posting.work_areas or posting.ncs))},
            },
        })
        return page["url"]

    def set_application_link(self, posting_page_id: str, application_url: str) -> None:
        self.request("PATCH", f"/pages/{posting_page_id}", json={
            "properties": {"지원 현황 링크": {"url": application_url}}
        })
