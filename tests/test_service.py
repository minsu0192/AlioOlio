from datetime import date

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
    def list_postings(self, start_date=None):
        self.start_dates.append(start_date)
        return self.items
    def enrich(self, posting):
        posting.detail_text = "상세"
        return posting
    @staticmethod
    def fingerprint(posting):
        return str(posting.to_json_dict())


class FakeNotion:
    def bootstrap(self, parent):
        return {"filter_data_source_id": "filters", "posting_data_source_id": "postings",
                "filter_database_id": "fdb", "posting_database_id": "pdb"}
    def filter_rules(self, _):
        return [FilterRule(True, "고용형태", "정규직", "정규직")]
    def upsert_posting(self, _ds, posting, matched, page_id, delivered):
        return page_id or f"page-{posting.seq}"
    def application_requests(self, _):
        return []


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
