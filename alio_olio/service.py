from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from .alio import AlioClient, process_section
from .attachments import to_tables, to_text
from .config import Settings
from .domain import Posting
from .filters import matches
from .notion import NotionClient
from .questions import extract_questions, format_questions, pick_form
from .schedule import STAGES, resolve, stages_in_process
from .storage import Storage
from .telegram import TelegramClient

log = logging.getLogger(__name__)

# 추출 로직을 고치면 올린다. 저장된 캐시가 무효화되어 관심 공고를 다시 읽는다.
EXTRACTION_VERSION = 4

# 필터 갱신은 5분마다 돈다. 그때마다 첨부를 다시 확인하면 ALIO에 하루 천 번 넘게
# 요청하고 노션도 그만큼 건드린다. 이 간격 안에 이미 뽑아둔 공고는 건너뛴다.
EXTRACTION_COOLDOWN = timedelta(hours=6)


class SyncService:
    def __init__(self, settings: Settings, storage: Storage | None = None,
                 alio: AlioClient | None = None, notion: NotionClient | None = None,
                 telegram: TelegramClient | None = None):
        self.settings = settings
        self.storage = storage or Storage(settings.database_path)
        self.alio = alio or AlioClient(settings.alio_base_url)
        self.notion = notion or NotionClient(settings.notion_token, settings.notion_api_version)
        self.telegram = telegram or TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)

    def bootstrap(self) -> dict[str, str]:
        if self.storage.get_meta("notion_resources"):
            return json.loads(self.storage.get_meta("notion_resources") or "{}")
        resources = self.notion.bootstrap(self.settings.notion_parent_page_id)
        self.storage.set_meta("notion_resources", json.dumps(resources))
        return resources

    def sync(self) -> dict[str, int]:
        resources = self.bootstrap()
        rules = self.notion.filter_rules(resources["filter_data_source_id"])
        last_success = self.storage.get_meta("last_successful_sync")
        baseline = last_success is None
        start = None
        if last_success:
            # Two-day overlap makes boundary times and delayed ALIO registration harmless.
            start = datetime.fromisoformat(last_success).date() - timedelta(days=2)
        listed = self.alio.list_postings(start_date=start)
        stats = {"seen": len(listed), "new": 0, "matched": 0, "notified": 0, "applications": 0}
        for posting in listed:
            if baseline and posting.end_date < date.today():
                continue
            cached = self.storage.posting(posting.seq)
            if cached:
                posting.detail_text = cached.detail_text
                posting.work_areas = cached.work_areas
                posting.ncs = cached.ncs
                posting.education = cached.education
                posting.replacement = cached.replacement
            else:
                posting = self.alio.enrich(posting)
            matched = matches(posting, rules)
            is_new, _changed = self.storage.upsert(posting, self.alio.fingerprint(posting), matched)
            stats["new"] += int(is_new)
            stats["matched"] += int(matched)
            row = self.storage.row(posting.seq)
            if matched or row["notion_page_id"]:
                page_id = self.notion.upsert_posting(
                    resources["posting_data_source_id"], posting, matched,
                    row["notion_page_id"], self.storage.delivered(posting.seq),
                )
                self.storage.set_notion_page(posting.seq, page_id)
            if is_new and matched and not baseline and not self.storage.delivered(posting.seq):
                self.storage.enqueue_delivery(posting.seq)

        # A failed send stays queued. The sync timestamp is deliberately written only
        # after the full queue succeeds, so downtime/failure ranges are fetched again.
        for posting in self.storage.pending_deliveries():
            self.telegram.send_posting(posting)
            self.storage.mark_delivered(posting.seq)
            stats["notified"] += 1

        self._sync_application_requests(resources)
        today = date.today()
        expired = {item.seq for item, row in self.storage.postings()
                   if item.end_date < today and row["notion_page_id"]}
        stats["closed"] = self.notion.close_expired_postings(
            resources["posting_data_source_id"], expired)
        stats["events"] = self._safe_enrich()
        self.storage.set_meta("last_successful_sync", datetime.now(timezone.utc).isoformat())
        self.storage.set_meta("last_sync_stats", json.dumps(stats, ensure_ascii=False))
        return stats

    def refresh_filters(self) -> int:
        resources = self.bootstrap()
        rules = self.notion.filter_rules(resources["filter_data_source_id"])
        changed = 0
        for posting, row in self.storage.postings():
            matched = matches(posting, rules)
            if matched != bool(row["filter_match"]):
                self.storage.upsert(posting, row["fingerprint"], matched)
                if row["notion_page_id"] or matched:
                    page_id = self.notion.upsert_posting(
                        resources["posting_data_source_id"], posting, matched,
                        row["notion_page_id"], self.storage.delivered(posting.seq),
                    )
                    self.storage.set_notion_page(posting.seq, page_id)
                changed += 1
        self._sync_application_requests(resources)
        self._safe_enrich()
        return changed

    def _safe_enrich(self) -> int:
        """공고문 추출은 부가 기능이다. 실패해도 sync 성공 기록을 막아선 안 된다."""
        try:
            return self.enrich_interests()["events"]
        except Exception as error:
            log.warning("관심 공고 전형 일정 추출을 건너뜁니다: %s", error)
            return 0

    def enrich_interests(self) -> dict[str, int]:
        """관심 체크한 공고의 공고문을 받아 전형 일정을 노션에 채운다.

        첨부 다운로드/파싱은 언제든 실패할 수 있고 부가 기능이므로, 한 건이 깨져도
        나머지를 계속 처리하고 sync 전체를 실패시키지 않는다.
        """
        resources = self.bootstrap()
        stats = {"interests": 0, "extracted": 0, "events": 0}
        by_seq = {posting.seq: posting for posting, _ in self.storage.postings()}
        for page in self.notion.interest_postings(resources["posting_data_source_id"]):
            raw_seq = page["properties"].get("ALIO ID", {}).get("number")
            if raw_seq is None or int(raw_seq) not in by_seq:
                continue
            stats["interests"] += 1
            try:
                dates, events = self._enrich_posting(page, by_seq[int(raw_seq)])
            except Exception as error:  # 첨부 서버 오류, 손상된 PDF 등
                log.warning("관심 공고 %s 전형 일정 추출 실패: %s", int(raw_seq), error)
                continue
            stats["extracted"] += len(dates)
            stats["events"] += events
        return stats

    def _enrich_posting(self, page: dict, posting: Posting) -> tuple[dict[str, str], int]:
        extraction = self._extraction(posting)
        dates = {field: value["date"] for field, value in extraction["stages"].items()}
        written = self.notion.update_posting_details(
            page["id"], page["properties"], dates,
            job_description_url=extraction["job_description_url"],
            questions=extraction.get("questions", ""),
            memo=extraction["memo"],
        )
        log.info("공고 %s: 날짜 %d개 추출, 노션 %d개 기록", posting.seq, len(dates), len(written))

        schedule_ds = self.settings.notion_schedule_data_source_id
        if not schedule_ds:
            return dates, 0
        # 날짜를 못 뽑은 단계도 '미정'으로 남겨야 캘린더에서 직접 채워 넣을 수 있다.
        expected = stages_in_process(process_section(posting.detail_text))
        stages = {
            STAGES[field]: ("예정", extraction["stages"][field]["date"]) if field in extraction["stages"]
            else ("미정", None)
            for field in STAGES
            if field in extraction["stages"] or field in expected
        }
        events = self.notion.ensure_schedule_events(schedule_ds, page["id"], posting.organization, stages)
        return dates, events

    def _extraction(self, posting: Posting) -> dict:
        # 최근에 뽑아둔 게 있으면 fileNo를 알아보려고 ALIO에 묻지도 않는다.
        fresh = self.storage.extraction(posting.seq, EXTRACTION_VERSION, max_age=EXTRACTION_COOLDOWN)
        if fresh is not None:
            return fresh
        attachments = self.alio.attachments(posting.seq)
        notice = next(iter(attachments["notice"]), None)
        file_no = notice.file_no if notice else "none"
        cached = self.storage.extraction(posting.seq, EXTRACTION_VERSION, file_no=file_no)
        if cached is not None:
            return cached

        data = self.alio.download(notice) if notice else b""
        text = to_text(notice, data) if notice else None
        tables = to_tables(notice, data) if notice else []
        hits = resolve(text, tables, posting.start_date)
        result = {
            "version": EXTRACTION_VERSION,
            "stages": {field: {"date": hit.day.isoformat(), "evidence": hit.evidence}
                       for field, hit in hits.items()},
            "job_description_url": self._first_url(attachments["job_description"]),
            "questions": self._questions(attachments),
            "memo": self._memo(posting, notice, text, tables, hits, attachments),
        }
        self.storage.set_extraction(posting.seq, file_no, result)
        return result

    def _questions(self, attachments: dict) -> str:
        """자소서 문항은 공고문이 아니라 입사지원서·자기소개서 양식에 들어 있다."""
        form = pick_form(attachments)
        if form is None:
            return ""
        data = self.alio.download(form)
        found = extract_questions(to_tables(form, data), to_text(form, data))
        log.info("자소서 문항 %d개 (%s)", len(found), form.name)
        return format_questions(found)

    def _first_url(self, attachments: list) -> str:
        first = next(iter(attachments), None)
        return first.url(self.settings.alio_base_url) if first else ""

    def _memo(self, posting: Posting, notice, text: str | None, tables: list,
              hits: dict, attachments: dict) -> str:
        lines = []
        if notice is None:
            lines.append("공고문 첨부 없음 — 전형 일정을 직접 입력해야 합니다.")
        elif text is None and not tables:
            lines.append(f"공고문({notice.extension or '형식 불명'})에서 글자를 읽지 못했습니다. "
                         "스캔 이미지 공고문이면 직접 입력해야 합니다.")
        else:
            lines.append(f"추출 경로: {'표' if tables else '본문'} / 읽은 표 {len(tables)}개")
        lines += [f"{field}: {hit.day} ← {hit.evidence}" for field, hit in hits.items()]
        # 문항을 못 뽑았을 때 원본을 바로 열어볼 수 있도록 첨부 링크는 항상 남긴다.
        for label, key in (("공고문", "notice"), ("입사지원서", "application"), ("기타", "etc")):
            for item in attachments[key][:2]:
                lines.append(f"[{label}] {item.name} {item.url(self.settings.alio_base_url)}")
        return "\n".join(lines)

    def _sync_application_requests(self, resources: dict[str, str]) -> None:
        target = self.settings.notion_application_data_source_id
        if not target:
            return
        by_seq = {posting.seq: posting for posting, _ in self.storage.postings()}
        for page in self.notion.application_requests(resources["posting_data_source_id"]):
            prop = page["properties"].get("ALIO ID", {}).get("number")
            if prop is None or int(prop) not in by_seq:
                continue
            application_url = self.notion.ensure_application(target, by_seq[int(prop)])
            self.notion.set_application_link(page["id"], application_url)
