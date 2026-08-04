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


def test_storage_works_from_another_thread(tmp_path):
    """APScheduler는 예약 작업을 워커 스레드에서 돌린다.

    기본 sqlite3 연결은 만든 스레드 밖에서 쓰면 ProgrammingError를 던져서,
    08:00/17:00 동기화와 5분마다의 필터 갱신이 매번 죽었다.
    """
    import threading

    storage = Storage(str(tmp_path / "db"))
    storage.set_meta("notion_resources", "{}")
    failures = []

    def work(index: int):
        try:
            storage.set_meta(f"key-{index}", str(index))
            assert storage.get_meta("notion_resources") == "{}"
            storage.postings()
        except Exception as error:
            failures.append(error)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert storage.get_meta("key-5") == "5"
