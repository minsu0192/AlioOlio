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
        self.applications_rows = []
        self.detail_updates = []
        self.questions = []
        self.profiles = []
        self.periods = []
        self.events = []
        self.closed = set()
    def bootstrap(self, parent):
        return {"filter_data_source_id": "filters", "posting_data_source_id": "postings",
                "filter_database_id": "fdb", "posting_database_id": "pdb"}
    def filter_rules(self, _):
        return [FilterRule(True, "고용형태", "정규직", "정규직")]
    def upsert_posting(self, _ds, posting, matched, page_id, delivered):
        return page_id or f"page-{posting.seq}"
    def application_requests(self, _):
        return []
    def applications(self, _):
        return self.applications_rows
    def query(self, _ds, payload=None):
        return []
    def interest_postings(self, _):
        return self.interests
    def close_expired_postings(self, _ds, expired_seqs):
        self.closed = set(expired_seqs)
        return len(self.closed)
    def update_posting_details(self, page_id, current, dates, job_description_url="",
                               questions="", memo="", profile=None, application_period=None):
        self.detail_updates.append((page_id, dates, job_description_url, memo))
        self.questions.append(questions)
        self.profiles.append(profile or {})
        self.periods.append(application_period)
        return sorted(dates)
    def ensure_schedule_events(self, _ds, page_id, organization, stages):
        self.events.append((page_id, stages))
        return len(stages)


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.reminded = []
    def send_posting(self, posting):
        self.sent.append(posting.seq)
    def send_reminder(self, posting, days_left):
        self.reminded.append((posting.seq, days_left))


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
    service.sync()  # 공고문(77), 직무기술서(78), 자소서 문항용 입사지원서(79)
    assert alio.downloads == ["77", "78", "79"]

    looked_up = []
    original = alio.attachments
    alio.attachments = lambda seq: (looked_up.append(seq), original(seq))[1]
    service.enrich_interests()
    assert alio.downloads == ["77", "78", "79"]
    assert looked_up == []  # 상세페이지도 안 본다


def test_a_replaced_notice_is_re_read_once_the_cooldown_passes(tmp_path, monkeypatch):
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()
    _age_extractions(service, service_module.EXTRACTION_COOLDOWN + timedelta(hours=1))

    # 기관이 공고문을 교체하면 fileNo가 바뀌므로 다시 받는다.
    alio.notice = Attachment("99", "공고문.pdf")
    service.enrich_interests()
    assert alio.downloads == ["77", "78", "79", "99", "78", "79"]


def test_an_unchanged_notice_is_not_downloaded_again_after_the_cooldown(tmp_path, monkeypatch):
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()
    _age_extractions(service, service_module.EXTRACTION_COOLDOWN + timedelta(hours=1))

    service.enrich_interests()  # 상세페이지는 보지만 같은 fileNo라 받지는 않는다
    assert alio.downloads == ["77", "78", "79"]


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


def application_row(page_id: str, status: str, posting_page_id: str = "", url: str = "") -> dict:
    return {"id": page_id, "url": f"https://notion.so/{page_id}", "properties": {
        "진행상태": {"type": "status", "status": {"name": status}},
        "ALIO 공고": {"type": "relation",
                     "relation": [{"id": posting_page_id}] if posting_page_id else []},
        "공고링크": {"type": "url", "url": url},
    }}


def test_alio_seq_is_read_from_both_url_shapes():
    assert service_module.alio_seq("https://www.alio.go.kr/information/informationRecruitDtl.do?seq=302968") == 302968
    assert service_module.alio_seq("https://job.alio.go.kr/mobile2021/recruit/recruitView.do?idx=303139") == 303139
    # 기관 자체 채용 페이지는 공고 번호가 없다. 이름이 비슷하다고 이으면 안 된다.
    assert service_module.alio_seq("https://www.nhis.or.kr/nhis/together/wbhaea02700m01.do?articleNo=11") is None
    assert service_module.alio_seq(None) is None


def _reminder_service(tmp_path, monkeypatch, end_date, status=None):
    settings = Settings("n", "p", "t", "c", database_path=str(tmp_path / "db"),
                        notion_application_data_source_id="apps")
    alio, notion = FakeAlio(), FakeNotion([interest_page(1)])
    alio.items = [Posting(1, "기관", "공고 1", date.today(), end_date, date.today(),
                          employment_types=["정규직"], url="https://alio/1")]
    notion.applications_rows = ([application_row("app-1", status, "page-1")] if status else [])
    monkeypatch.setattr(service_module, "to_text", lambda attachment, data: alio.notice_text)
    service = SyncService(settings, Storage(settings.database_path), alio, notion, FakeTelegram())
    service.sync()
    return service


def test_a_deadline_within_three_days_is_reminded(tmp_path, monkeypatch):
    service = _reminder_service(tmp_path, monkeypatch, date.today() + timedelta(days=3))
    assert [left for _posting, left in service.pending_submissions()] == [3]
    assert service.remind_submissions() == 1
    assert service.telegram.reminded == [(1, 3)]


def test_a_submitted_application_is_not_reminded(tmp_path, monkeypatch):
    """지원 현황이 "완료"면 이미 낸 것이므로 찌르지 않는다."""
    service = _reminder_service(tmp_path, monkeypatch, date.today() + timedelta(days=2), "완료")
    assert service.pending_submissions() == []


def test_an_application_still_in_progress_is_reminded(tmp_path, monkeypatch):
    service = _reminder_service(tmp_path, monkeypatch, date.today() + timedelta(days=1), "진행 중")
    assert [left for _posting, left in service.pending_submissions()] == [1]


def test_deadlines_further_out_and_already_past_are_left_alone(tmp_path, monkeypatch):
    far = _reminder_service(tmp_path, monkeypatch, date.today() + timedelta(days=4))
    assert far.pending_submissions() == []
    over = _reminder_service(tmp_path, monkeypatch, date.today() - timedelta(days=1))
    assert over.pending_submissions() == []


def test_evaluation_areas_never_land_in_the_questions_field(tmp_path, monkeypatch):
    """공고문의 평가 영역을 문항 칸에 넣으면 문항인 줄 알고 그대로 쓰게 된다.

    근로복지공단에서 실제로 그랬다. 양식이 없으면 문항 칸은 비워 두고, 평가 영역은
    무엇인지 밝혀 전형 메모에만 적는다.
    """
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    alio.notice_text = ("서류전형 평가기준 자기소개서(적/부) - 조직이해/지원동기, 직무이해/자기개발, "
                        "직업윤리 (각 문항별 500자 이내 작성)")
    alio.attachments = lambda seq: {"notice": [alio.notice], "application": [],
                                    "etc": [], "job_description": []}
    service.sync()
    assert notion.questions[-1] == ""
    memo = notion.detail_updates[-1][3]
    assert "평가 영역" in memo and "문항 아님" in memo
    assert "조직이해/지원동기" in memo
