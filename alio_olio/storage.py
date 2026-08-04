from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .domain import Posting


class Storage:
    """SQLite 저장소.

    APScheduler는 예약 작업을 워커 스레드에서 돌린다. 기본 sqlite3 연결은 만든 스레드
    밖에서 쓰면 ProgrammingError를 던지므로, 연결을 스레드 간 공유하도록 열고 접근을
    잠금으로 직렬화한다. 08:00/17:00 동기화와 5분마다의 필터 갱신이 겹칠 수도 있다.
    """

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS postings (
                    seq INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    notion_page_id TEXT,
                    filter_match INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    seq INTEGER PRIMARY KEY,
                    delivered_at TEXT NOT NULL,
                    FOREIGN KEY(seq) REFERENCES postings(seq)
                );
                CREATE TABLE IF NOT EXISTS notification_queue (
                    seq INTEGER PRIMARY KEY,
                    queued_at TEXT NOT NULL,
                    FOREIGN KEY(seq) REFERENCES postings(seq)
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachment_extractions (
                    seq INTEGER PRIMARY KEY,
                    file_no TEXT NOT NULL,
                    extracted TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    FOREIGN KEY(seq) REFERENCES postings(seq)
                );
                """
            )
            self.connection.commit()

    def upsert(self, posting: Posting, fingerprint: str, matched: bool) -> tuple[bool, bool]:
        with self.lock:
            now = datetime.now(timezone.utc).isoformat()
            old = self.connection.execute(
                "SELECT fingerprint FROM postings WHERE seq = ?", (posting.seq,)
            ).fetchone()
            payload = json.dumps(posting.to_json_dict(), ensure_ascii=False, sort_keys=True)
            if old is None:
                self.connection.execute(
                    "INSERT INTO postings(seq,payload,fingerprint,filter_match,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                    (posting.seq, payload, fingerprint, int(matched), now, now),
                )
            else:
                self.connection.execute(
                    "UPDATE postings SET payload=?,fingerprint=?,filter_match=?,last_seen_at=? WHERE seq=?",
                    (payload, fingerprint, int(matched), now, posting.seq),
                )
            self.connection.commit()
            return old is None, old is not None and old["fingerprint"] != fingerprint

    def postings(self) -> list[tuple[Posting, sqlite3.Row]]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM postings ORDER BY seq DESC").fetchall()
            return [(Posting.from_json_dict(json.loads(row["payload"])), row) for row in rows]

    def posting(self, seq: int) -> Posting | None:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM postings WHERE seq=?", (seq,)).fetchone()
            return Posting.from_json_dict(json.loads(row["payload"])) if row else None

    def set_notion_page(self, seq: int, page_id: str) -> None:
        with self.lock:
            self.connection.execute("UPDATE postings SET notion_page_id=? WHERE seq=?", (page_id, seq))
            self.connection.commit()

    def delivered(self, seq: int) -> bool:
        with self.lock:
            return self.connection.execute("SELECT 1 FROM deliveries WHERE seq=?", (seq,)).fetchone() is not None

    def mark_delivered(self, seq: int) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO deliveries(seq,delivered_at) VALUES(?,?)",
                (seq, datetime.now(timezone.utc).isoformat()),
            )
            self.connection.execute("DELETE FROM notification_queue WHERE seq=?", (seq,))
            self.connection.commit()

    def enqueue_delivery(self, seq: int) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO notification_queue(seq,queued_at) VALUES(?,?)",
                (seq, datetime.now(timezone.utc).isoformat()),
            )
            self.connection.commit()

    def pending_deliveries(self) -> list[Posting]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT p.payload FROM notification_queue q JOIN postings p ON p.seq=q.seq ORDER BY q.queued_at, q.seq"
            ).fetchall()
            return [Posting.from_json_dict(json.loads(row["payload"])) for row in rows]

    def extraction(self, seq: int, file_no: str, version: int) -> dict | None:
        """같은 공고문을 두 번 내려받지 않기 위한 캐시.

        기관이 공고문을 교체하면 fileNo가 바뀌고, 추출 로직을 고치면 version이 오른다.
        어느 쪽이든 캐시를 무시하고 다시 뽑는다.
        """
        with self.lock:
            row = self.connection.execute(
                "SELECT extracted FROM attachment_extractions WHERE seq=? AND file_no=?", (seq, file_no)
            ).fetchone()
            if row is None:
                return None
            cached = json.loads(row["extracted"])
            return cached if cached.get("version") == version else None

    def set_extraction(self, seq: int, file_no: str, extracted: dict) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO attachment_extractions(seq,file_no,extracted,extracted_at) VALUES(?,?,?,?) "
                "ON CONFLICT(seq) DO UPDATE SET file_no=excluded.file_no, extracted=excluded.extracted, "
                "extracted_at=excluded.extracted_at",
                (seq, file_no, json.dumps(extracted, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
            self.connection.commit()

    def get_meta(self, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self.connection.commit()
