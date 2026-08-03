from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from .domain import Posting, split_values


class AlioClient:
    def __init__(self, base_url: str = "https://www.alio.go.kr", transport=None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=30,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": "AlioOlio/0.1 (+personal recruitment monitor)"},
        )

    def list_postings(self, lookback_days: int = 240, start_date: date | None = None) -> list[Posting]:
        today = date.today()
        payload = {
            "detailcodeValueArr": [], "locationValueArr": [], "worktypeValueArr": [],
            "eduValueArr": [], "areaValueArr": [], "carrerValueArr": [],
            "apba_type": "", "apba_id": "", "replacement": "", "title": "",
            "s_date": (start_date or (today - timedelta(days=lookback_days))).strftime("%Y.%m.%d"),
            "e_date": today.strftime("%Y.%m.%d"), "ing": "", "order": "IDATE", "pageNo": 1,
        }
        results: list[Posting] = []
        while True:
            response = self.client.post("/information/getRecruitList.json", json=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("status") != "success":
                raise RuntimeError(f"ALIO returned failure: {body}")
            data = body["data"]
            results.extend(self._from_list_item(item) for item in data.get("recruitList", []))
            if payload["pageNo"] >= int(data["page"]["totalPage"]):
                break
            payload["pageNo"] += 1
        return results

    def enrich(self, posting: Posting) -> Posting:
        response = self.client.get("/mobile/information/informationRecruitDtl.do", params={"seq": posting.seq})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = " ".join(soup.stripped_strings)
        posting.detail_text = re.sub(r"\s+", " ", text)
        labels = {
            "표준직무(NCS)": "ncs", "고용형태": "employment_types", "학력정보": "education",
            "채용구분": "career_types", "근무지": "locations", "근무분야": "work_areas",
        }
        for label, field in labels.items():
            match = re.search(re.escape(label) + r"\s+(.+?)(?=\s+(?:" + "|".join(map(re.escape, labels)) + r"|채용인원|우대조건|응시자격)\s+)", posting.detail_text)
            if match:
                setattr(posting, field, split_values(match.group(1)))
        return posting

    @staticmethod
    def fingerprint(posting: Posting) -> str:
        value = json.dumps(posting.to_json_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()

    def _from_list_item(self, item: dict) -> Posting:
        parse = lambda value: date.fromisoformat(value.replace(".", "-"))
        return Posting(
            seq=int(item["seq"]), organization=item.get("pname", ""), title=item.get("title", ""),
            start_date=parse(item["termStart"]), end_date=parse(item["termEnd"]),
            registered_date=parse(item.get("frstDate") or item["termStart"]), status=item.get("ing", ""),
            headcount=int(item["person"]) if str(item.get("person", "")).isdigit() else None,
            employment_types=split_values(item.get("workTypeNa")), career_types=split_values(item.get("carrerNa")),
            locations=split_values(item.get("locationNa")),
            url=f"{self.base_url}/information/informationRecruitDtl.do?seq={item['seq']}",
        )
