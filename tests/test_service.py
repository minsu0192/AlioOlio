from datetime import date, datetime, timedelta, timezone

from alio_olio import service as service_module
from alio_olio.attachments import Attachment
from alio_olio.config import Settings
from alio_olio.domain import FilterRule, Posting
from alio_olio.service import SyncService
from alio_olio.storage import Storage


def item(seq: int) -> Posting:
    return Posting(seq, "기관", f"공고 {seq}", date.today(), date.today(), date.today(),
                   employment_types=["정규직"], url=f"https://alio/{seq}")


class FakeAlio:
    def __init__(self):
        self.items = [item(1)]
        self.start_dates = []
        self.downloads = []
        self.notice = Attachment("77", "공고문.pdf")
        self.notice_text = "서류전형합격자발표 ’26.8.21.(금) 필기시험 ’26.9.5.(토)"
    def list_postings(self, start_date=None):
        self.start_dates.append(start_date)
        return self.items
    def enrich(self, posting):
        posting.detail_text = "전형절차 방법 서류전형→필기전형→면접전형→최종합격 전형단계별 채용정보"
        return posting
    def attachments(self, seq):
        return {"notice": [self.notice], "application": [Attachment("79", "입사지원서.pdf")],
                "etc": [], "job_description": [Attachment("78", "직무기술서.pdf")]}
    def download(self, attachment):
        self.downloads.append(attachment.file_no)
        return b"%PDF-fake"
    @staticmethod
    def fingerprint(posting):
        return str(posting.to_json_dict())


class FakeNotion:
    def __init__(self, interests=None):
        self.interests = interests or []
        self.detail_updates = []
        self.questions = []
        self.events = []
    def bootstrap(self, parent):
        return {"filter_data_source_id": "filters", "posting_data_source_id": "postings",
                "filter_database_id": "fdb", "posting_database_id": "pdb"}
    def filter_rules(self, _):
        return [FilterRule(True, "고용형태", "정규직", "정규직")]
    def upsert_posting(self, _ds, posting, matched, page_id, delivered):
        return page_id or f"page-{posting.seq}"
    def application_requests(self, _):
        return []
    def interest_postings(self, _):
        return self.interests
    def update_posting_details(self, page_id, current, dates, job_description_url="",
                               questions="", memo=""):
        self.detail_updates.append((page_id, dates, job_description_url, memo))
        self.questions.append(questions)
        return sorted(dates)
    def ensure_schedule_events(self, _ds, page_id, organization, stages):
        self.events.append((page_id, stages))
        return len(stages)


class FakeTelegram:
    def __init__(self):
        self.sent = []
    def send_posting(self, posting):
        self.sent.append(posting.seq)


def test_baseline_then_catchup_notifies_once(tmp_path):
    settings = Settings("n", "p", "t", "c", database_path=str(tmp_path / "db"))
    alio, telegram = FakeAlio(), FakeTelegram()
    service = SyncService(settings, Storage(settings.database_path), alio, FakeNotion(), telegram)
    assert service.sync()["notified"] == 0
    alio.items = [item(1), item(2)]
    assert service.sync()["notified"] == 1
    assert telegram.sent == [2]
    assert service.sync()["notified"] == 0
    assert telegram.sent == [2]
    assert alio.start_dates[0] is None
    assert alio.start_dates[1] is not None


def interest_page(seq: int, page_id: str = "page-1", **props) -> dict:
    return {"id": page_id, "properties": {"ALIO ID": {"number": seq}, **props}}


def build(tmp_path, interests, monkeypatch):
    settings = Settings("n", "p", "t", "c", database_path=str(tmp_path / "db"),
                        notion_schedule_data_source_id="schedule")
    alio, notion = FakeAlio(), FakeNotion(interests)
    monkeypatch.setattr(service_module, "to_text", lambda attachment, data: alio.notice_text)
    service = SyncService(settings, Storage(settings.database_path), alio, notion, FakeTelegram())
    return service, alio, notion


def test_enrich_extracts_dates_and_seeds_undecided_stages(tmp_path, monkeypatch):
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()

    _page, dates, jd_url, memo = notion.detail_updates[-1]
    assert dates == {"서류발표일": "2026-08-21", "필기일정": "2026-09-05"}
    assert jd_url.endswith("fileNo=78")
    assert "공고문.pdf" in memo

    # 전형절차에 있는 단계는 날짜를 못 뽑아도 "미정"으로 남겨 직접 채울 수 있게 한다.
    _page, stages = notion.events[-1]
    assert stages["서류발표"] == ("예정", "2026-08-21")
    assert stages["필기"] == ("예정", "2026-09-05")
    assert stages["필기발표"] == ("미정", None)
    assert stages["면접"] == ("미정", None)


def test_recent_extraction_skips_alio_entirely(tmp_path, monkeypatch):
    """필터 갱신은 5분마다 돈다. 그때마다 첨부를 다시 확인하면 ALIO를 하루 천 번 넘게
    두드리므로, 쿨다운 안에서는 상세페이지 조회조차 하지 않는다."""
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()  # 공고문(77)과 자소서 문항용 입사지원서(79)를 한 번씩 받는다
    assert alio.downloads == ["77", "79"]

    looked_up = []
    original = alio.attachments
    alio.attachments = lambda seq: (looked_up.append(seq), original(seq))[1]
    service.enrich_interests()
    assert alio.downloads == ["77", "79"]
    assert looked_up == []  # 상세페이지도 안 본다


def test_a_replaced_notice_is_re_read_once_the_cooldown_passes(tmp_path, monkeypatch):
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()
    _age_extractions(service, service_module.EXTRACTION_COOLDOWN + timedelta(hours=1))

    # 기관이 공고문을 교체하면 fileNo가 바뀌므로 다시 받는다.
    alio.notice = Attachment("99", "공고문.pdf")
    service.enrich_interests()
    assert alio.downloads == ["77", "79", "99", "79"]


def test_an_unchanged_notice_is_not_downloaded_again_after_the_cooldown(tmp_path, monkeypatch):
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()
    _age_extractions(service, service_module.EXTRACTION_COOLDOWN + timedelta(hours=1))

    service.enrich_interests()  # 상세페이지는 보지만 같은 fileNo라 받지는 않는다
    assert alio.downloads == ["77", "79"]


def _age_extractions(service, age):
    stale = (datetime.now(timezone.utc) - age).isoformat()
    service.storage.connection.execute("UPDATE attachment_extractions SET extracted_at=?", (stale,))
    service.storage.connection.commit()


def test_attachment_failure_does_not_break_sync(tmp_path, monkeypatch):
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    def boom(seq):
        raise RuntimeError("ALIO 첨부 서버 오류")
    alio.attachments = boom
    assert service.sync()["seen"] == 1
    assert notion.detail_updates == []
    assert service.storage.get_meta("last_successful_sync") is not None
