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
        self.deadlines = []
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
    def ensure_deadline_events(self, _ds, page_id, organization, deadlines):
        self.deadlines.append((page_id, deadlines))
        return len(deadlines)


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.reminded = []
        self.uncertain = []
        self.questions_sent = []
    def send_posting(self, posting):
        self.sent.append(posting.seq)
    def send_reminder(self, posting, days_left):
        self.reminded.append((posting.seq, days_left))
    def send_uncertain(self, posting, items):
        self.uncertain.append((posting.seq, items))
    def send_questions(self, posting, questions):
        self.questions_sent.append((posting.seq, questions))


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


def test_the_notice_application_period_replaces_the_alio_one(tmp_path, monkeypatch):
    """ALIO가 주는 지원기간이 공고기간일 때가 있다(근로복지공단: 공고 8.5.~, 접수 8.12.~).

    지원자에게 필요한 것은 실제로 지원서를 낼 수 있는 기간이므로 그것으로 덮는다.
    한 번 고친 뒤 다음 동기화가 ALIO 값으로 되돌리지 않아야 한다.
    """
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    alio.notice_text = ("원서접수 • 공고기간: ’26. 8. 5.(수) ∼ ’26. 8. 19.(수)"
                        " • 접수기간: ’26. 8. 12.(수) ∼ ’26. 8. 19.(수) 18:00:00까지")
    alio.items = [Posting(1, "기관", "공고 1", date(2026, 8, 5), date(2026, 8, 19),
                          date(2026, 8, 5), employment_types=["정규직"], url="https://alio/1")]
    service.sync()
    assert notion.periods[-1] == ["2026-08-12", "2026-08-19"]

    # 다음 동기화에서 ALIO가 다시 공고기간을 줘도 되돌아가지 않는다.
    service.sync()
    fresh = service.storage.posting(1)
    assert fresh.start_date == date(2026, 8, 12)


def test_a_weakly_supported_date_is_warned_once(tmp_path, monkeypatch):
    """근거가 약한 값은 캘린더에 올리되 알린다. 같은 값을 두 번 알리지는 않는다."""
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    # 표가 없어 본문에서만, 그것도 절 위치로만 판단하는 형태
    alio.notice_text = ("서류전형•선발방법: 자기소개서 평가 •합격자발표: ’26.9.11.(금) "
                        "필기전형•일시: ’26.9.19.(토) •합격자발표: ’26.10.2.(금)")
    service.sync()
    assert service.telegram.uncertain, "근거가 약한 값을 알리지 않았습니다"
    fields = {field for _p, items in service.telegram.uncertain for field, _d, _r in items}
    assert "필기발표일" in fields

    service.telegram.uncertain.clear()
    service.enrich_interests()
    assert service.telegram.uncertain == [], "같은 값을 다시 알렸습니다"


def test_a_form_added_after_the_opening_day_is_picked_up(tmp_path, monkeypatch):
    """지원서 양식은 공고가 뜬 뒤 추가되기도 한다.

    캐시를 공고문 파일 하나로만 잡으면, 공고문이 그대로인 한 새로 올라온 양식을
    영영 보지 못한다. 첨부 구성이 바뀌면 다시 읽어야 한다.
    """
    service, alio, _notion = build(tmp_path, [interest_page(1)], monkeypatch)
    base = {"notice": [alio.notice], "application": [], "etc": [], "job_description": []}
    alio.attachments = lambda seq: base
    service.sync()
    assert alio.downloads == ["77"]

    # 접수가 시작되며 자기소개서 양식이 붙었다. 공고문(77)은 그대로다.
    base["etc"] = [Attachment("88", "자기소개서 양식.pdf")]
    _age_extractions(service, service_module.EXTRACTION_COOLDOWN + timedelta(hours=1))
    service.enrich_interests()
    assert "88" in alio.downloads, "새로 올라온 양식을 읽지 않았습니다"


def test_the_opening_day_forces_one_extra_look(tmp_path, monkeypatch):
    """지원서 양식은 접수가 열리는 날 붙기도 한다. 그날은 쿨다운을 무시하고 한 번 더 본다."""
    settings = Settings("n", "p", "t", "c", database_path=str(tmp_path / "db"),
                        notion_schedule_data_source_id="schedule")
    alio, notion = FakeAlio(), FakeNotion([interest_page(1)])
    today = date.today()
    alio.items = [Posting(1, "기관", "공고 1", today, today + timedelta(days=10), today,
                          employment_types=["정규직"], url="https://alio/1")]
    base = {"notice": [alio.notice], "application": [], "etc": [], "job_description": []}
    alio.attachments = lambda seq: base
    monkeypatch.setattr(service_module, "to_text", lambda attachment, data: alio.notice_text)
    service = SyncService(settings, Storage(settings.database_path), alio, notion, FakeTelegram())
    service.sync()

    # 첫 동기화가 접수 시작일의 한 번을 쓴다.
    assert service.storage.get_meta(f"opened:1:{today.isoformat()}") == "1"

    looked = []
    original = alio.attachments
    alio.attachments = lambda seq: (looked.append(seq), original(seq))[1]
    service.enrich_interests()  # 같은 날 두 번째부터는 쿨다운을 따른다
    assert looked == []


def test_newly_posted_questions_are_announced(tmp_path, monkeypatch):
    """문항이 뒤늦게 올라오면 노션에 채우고 텔레그램으로 알린다."""
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    alio.notice_text = ("자기소개서 1. 지원하게 된 동기와 입사 후 포부를 "
                        "구체적으로 기술해 주십시오. (500자 이내)")
    service.sync()
    assert service.telegram.questions_sent, "문항이 올라왔는데 알리지 않았습니다"

    # 노션에 이미 들어간 뒤에는 다시 알리지 않는다.
    service.telegram.questions_sent.clear()
    filled = interest_page(1, **{"자소서 문항": {"type": "rich_text",
                                             "rich_text": [{"plain_text": "1. 지원 동기"}]}})
    notion.interests = [filled]
    service.enrich_interests()
    assert service.telegram.questions_sent == []


def test_the_calendar_is_not_queried_when_nothing_changed(tmp_path, monkeypatch):
    """필터 갱신이 15분마다 이 경로를 탄다. 바뀐 게 없으면 물어보지도 않는다."""
    service, alio, notion = build(tmp_path, [interest_page(1)], monkeypatch)
    service.sync()
    seeded = len(notion.events)
    assert seeded, "처음에는 캘린더에 올려야 한다"

    service.enrich_interests()
    assert len(notion.events) == seeded, "바뀐 것이 없는데 캘린더를 다시 건드렸습니다"


def test_control_characters_do_not_cause_endless_rewrites():
    """PDF에서 뽑은 글자에는 널 바이트가 섞인다. 노션은 저장할 때 버리므로 그대로
    보내면 보낸 값과 읽은 값이 영원히 달라 매번 다시 쓰게 된다."""
    from alio_olio.notion import _text
    assert _text("가나\x00다\x01라")[0]["text"]["content"] == "가나다라"
    assert _text("\x00") == []
