from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from .alio import AlioClient, process_section
from .attachments import to_tables, to_text
from .config import Settings
from .domain import Posting
from .deadlines import extract_deadlines
from .filters import matches
from .job_description import extract_profile
from .notion import NotionClient, _is_empty as _is_blank
from .questions import extract_areas, extract_questions, format_questions, pick_form
from .schedule import STAGES, pick_schedule_table, readable_evidence, resolve, stages_in_process
from .storage import Storage
from .telegram import TelegramClient

log = logging.getLogger(__name__)

# 추출 로직을 고치면 올린다. 저장된 캐시가 무효화되어 관심 공고를 다시 읽는다.
EXTRACTION_VERSION = 16

# 필터 갱신은 5분마다 돈다. 그때마다 첨부를 다시 확인하면 ALIO에 하루 천 번 넘게
# 요청하고 노션도 그만큼 건드린다. 이 간격 안에 이미 뽑아둔 공고는 건너뛴다.
EXTRACTION_COOLDOWN = timedelta(hours=6)

# 지원 현황의 "진행상태"가 이 값이면 제출을 마친 것으로 본다.
SUBMITTED = "완료"

# ALIO 공고 주소는 두 가지로 쓰인다. 웹은 ?seq=302968, 모바일 채용관은 ?idx=302968.
_ALIO_SEQ = re.compile(r"alio\.go\.kr/.*[?&](?:seq|idx)=(\d+)")


def alio_seq(url: str | None) -> int | None:
    """공고 주소에서 ALIO 공고 번호를 뽑는다. ALIO 주소가 아니면 None."""
    match = _ALIO_SEQ.search(url or "")
    return int(match.group(1)) if match else None


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

    def pending_submissions(self, today: date | None = None) -> list[tuple[Posting, int]]:
        """관심 공고 중 마감이 코앞인데 아직 제출하지 않은 것. (공고, 남은 날) 목록."""
        resources = self.bootstrap()
        today = today or date.today()
        target = self.settings.notion_application_data_source_id
        submitted: set[str] = set()
        if target:
            for row in self.notion.applications(target):
                status = (row["properties"].get("진행상태", {}).get("status") or {}).get("name")
                if status != SUBMITTED:
                    continue
                for item in row["properties"].get("ALIO 공고", {}).get("relation", []):
                    submitted.add(item["id"].replace("-", ""))

        by_seq = {posting.seq: posting for posting, _ in self.storage.postings()}
        due: list[tuple[Posting, int]] = []
        for page in self.notion.interest_postings(resources["posting_data_source_id"]):
            seq = page["properties"].get("ALIO ID", {}).get("number")
            posting = by_seq.get(int(seq)) if seq is not None else None
            if posting is None or page["id"].replace("-", "") in submitted:
                continue
            left = (posting.end_date - today).days
            if 0 <= left <= self.settings.reminder_days:
                due.append((posting, left))
        return sorted(due, key=lambda item: item[1])

    def remind_submissions(self, today: date | None = None) -> int:
        """마감이 다가온 관심 공고를 텔레그램으로 알린다. 예약 시각마다 한 번씩 보낸다."""
        today = today or date.today()
        sent = 0
        for posting, left in self.pending_submissions(today):
            self.telegram.send_reminder(posting, left)
            sent += 1
        log.info("제출 리마인더 %d건 발송", sent)
        return sent

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
            profile=extraction.get("profile", {}),
            questions=extraction.get("questions", ""),
            memo=self._memo_for(page, dates, extraction["memo"], extraction.get("areas", [])),
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
        events += self.notion.ensure_deadline_events(
            schedule_ds, page["id"], posting.organization, extraction.get("deadlines", []))
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
        # 전형일정표에는 시험 날짜만 있는 게 아니다. 자기소개서 제출, 증빙서류 등록처럼
        # 놓치면 탈락하는 기한도 같은 표에 들어 있다.
        due = extract_deadlines(pick_schedule_table(tables), posting.start_date)
        result = {
            "version": EXTRACTION_VERSION,
            "stages": {field: {"date": hit.day.isoformat(), "evidence": hit.evidence}
                       for field, hit in hits.items()},
            "areas": extract_areas(text) if text else [],
            "deadlines": [{"label": d.label, "day": d.day.isoformat(),
                           "start": d.start.isoformat() if d.start else None} for d in due],
            "job_description_url": self._first_url(attachments["job_description"]),
            "profile": self._profile(attachments),
            "questions": self._questions(attachments),
            "memo": self._memo(posting, notice, text, tables, hits, attachments),
        }
        self.storage.set_extraction(posting.seq, file_no, result)
        return result

    def _profile(self, attachments: dict) -> dict[str, str]:
        """직무기술서에서 주요 업무·필요 지식·기술·직무 핵심역량을 뽑는다."""
        document = next(iter(attachments["job_description"]), None)
        if document is None:
            return {}
        data = self.alio.download(document)
        found = extract_profile(to_tables(document, data), to_text(document, data))
        log.info("직무기술서에서 %d개 항목 (%s)", len(found), document.name)
        return found

    def _questions(self, attachments: dict, notice_text: str | None = None) -> str:
        """자소서 문항은 보통 입사지원서·자기소개서 양식에 들어 있다.

        양식을 아예 안 올리는 기관도 있다. 근로복지공단은 공고문 안에 문항 주제만
        늘어놓으므로("조직이해/지원동기, … 각 문항별 500자 이내") 그것이라도 건진다.
        """
        form = pick_form(attachments)
        if form is not None:
            data = self.alio.download(form)
            found = extract_questions(to_tables(form, data), to_text(form, data))
            if found:
                log.info("자소서 문항 %d개 (%s)", len(found), form.name)
                return format_questions(found)
        topics = extract_topics(notice_text) if notice_text else []
        if topics:
            log.info("자소서 문항 주제 %d개 (공고문)", len(topics))
        return format_questions(topics)

    def _first_url(self, attachments: list) -> str:
        first = next(iter(attachments), None)
        return first.url(self.settings.alio_base_url) if first else ""

    def _memo_for(self, page: dict, dates: dict[str, str], memo: str,
                  areas: list[str] | None = None) -> str:
        """공고문에서 읽은 메모에, 지금 노션에서 비어 있는 단계를 덧붙인다.

        비었는지는 노션 현재 상태를 봐야 안다. 추출 결과만 보고 적으면 손으로 채워
        넣은 날짜까지 "직접 채워야 한다"고 하게 된다.
        """
        properties = page.get("properties", {})
        empty = [field for field in STAGES
                 if field not in dates and _is_blank(properties.get(field))]
        lines = []
        if empty:
            lines.append("직접 채워야 하는 단계: " + ", ".join(empty))
        if areas:
            # 문항이 아니라 "자기소개서로 무엇을 보는지"다. 문항 칸에 넣으면 문항인 줄
            # 알고 그대로 쓰게 되므로 여기에만, 무엇인지 밝혀서 적는다.
            lines.append("자기소개서 평가 영역(공고문 기준, 문항 아님): " + ", ".join(areas)
                         + " — 실제 문항은 입사지원 사이트에서 확인")
        return "\n".join(lines + [memo]).strip()

    def _memo(self, posting: Posting, notice, text: str | None, tables: list,
              hits: dict, attachments: dict) -> str:
        # 손댈 거리만 남긴다. 뽑은 날짜는 날짜 칸에 이미 들어 있고, 어느 경로로
        # 읽었는지·표를 몇 개 읽었는지는 만드는 사람 사정이지 보는 사람 정보가 아니다.
        # 근거 문장도 넣지 않는다. 공백을 모두 지운 원문 조각이라 읽을 수 없고
        # ("...합격자발표:’26.9.11.(금)"), 굵은 글씨를 두 번 찍는 PDF는 "토토"처럼
        # 겹쳐 나온다. 확인이 필요하면 아래 공고문 링크를 연다(근거는 로그에 남는다).
        lines = []
        if notice is None:
            lines.append("공고문 첨부가 없습니다. 전형 일정을 직접 입력해야 합니다.")
        elif text is None and not tables:
            lines.append(f"공고문({notice.extension or '형식 불명'})에서 글자를 읽지 못했습니다. "
                         "스캔 이미지 공고문이면 직접 입력해야 합니다.")
        # 뽑은 날짜마다 원문 문장을 붙인다. 이 도구는 조용히 틀릴 수 있고(제출 기한을
        # 시험일로 읽는 식), 틀린 값은 빈칸과 달리 눈에 띄지 않는다. 근거가 옆에 있으면
        # 몇 초 만에 대조된다. 원문에서 못 찾으면 붙이지 않는다 — 엉뚱한 문장을
        # 보여주는 것이 근거가 없는 것보다 나쁘다.
        if text:
            for field, hit in hits.items():
                snippet = readable_evidence(text, hit)
                lines.append(f"{field} {hit.day}" + (f"  ←  {snippet}" if snippet else ""))
        # 무엇이 비었는지는 노션 현재 상태를 봐야 알 수 있으므로 여기서 적지 않는다.
        # 손으로 채워 넣은 날짜까지 "직접 채워야 한다"고 하면 거짓말이 된다.
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
            application_url = self.notion.ensure_application(target, by_seq[int(prop)], page["id"])
            self.notion.set_application_link(page["id"], application_url)
        self.link_applications(resources)

    def link_applications(self, resources: dict[str, str]) -> int:
        """지원 현황 행을 같은 공고에 이어 붙인다.

        손으로 만든 행도 공고 링크만 있으면 이어진다. 기관명으로 맞추면 회차가 다른
        공고나 이름이 비슷한 다른 기관(국민건강보험공단 ↔ 일산병원)에 붙으므로 쓰지
        않는다. ALIO 주소에 들어 있는 공고 번호만 믿는다.
        """
        target = self.settings.notion_application_data_source_id
        if not target:
            return 0
        # 아직 안 이어진 행부터 추린다. 이어 붙일 게 없으면 공고 148건을 훑지 않는다.
        pending = [(row, alio_seq(row["properties"].get("공고링크", {}).get("url")))
                   for row in self.notion.applications(target)
                   if not row["properties"].get("ALIO 공고", {}).get("relation")]
        pending = [(row, seq) for row, seq in pending if seq is not None]
        if not pending:
            return 0
        pages_by_seq = {}
        for page in self.notion.query(resources["posting_data_source_id"],
                                      {"filter": {"or": [{"property": "ALIO ID",
                                                          "number": {"equals": seq}}
                                                         for _row, seq in pending]}}):
            seq = page["properties"].get("ALIO ID", {}).get("number")
            if seq is not None:
                pages_by_seq[int(seq)] = page["id"]
        linked = 0
        for row, seq in pending:
            if seq in pages_by_seq:
                linked += int(self.notion.link_application(row, pages_by_seq[seq]))
        if linked:
            log.info("지원 현황 %d건을 공고에 연결했습니다", linked)
        return linked
