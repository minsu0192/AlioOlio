from datetime import date

from alio_olio.domain import Posting
from alio_olio.storage import Storage


def test_upsert_and_delivery_are_idempotent(tmp_path):
    storage = Storage(str(tmp_path / "db.sqlite"))
    item = Posting(10, "기관", "제목", date(2026, 8, 1), date(2026, 8, 10), date(2026, 8, 1))
    assert storage.upsert(item, "a", True) == (True, False)
    assert storage.upsert(item, "a", True) == (False, False)
    assert storage.upsert(item, "b", True) == (False, True)
    assert not storage.delivered(10)
    storage.enqueue_delivery(10)
    storage.enqueue_delivery(10)
    assert [item.seq for item in storage.pending_deliveries()] == [10]
    storage.mark_delivered(10)
    storage.mark_delivered(10)
    assert storage.delivered(10)
    assert storage.pending_deliveries() == []
