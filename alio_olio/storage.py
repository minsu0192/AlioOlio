from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .domain import Posting


class Storage:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.migrate()

    def migrate(self) -> None:
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
            """
        )
        self.connection.commit()

    def upsert(self, posting: Posting, fingerprint: str, matched: bool) -> tuple[bool, bool]:
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
        rows = self.connection.execute("SELECT * FROM postings ORDER BY seq DESC").fetchall()
        return [(Posting.from_json_dict(json.loads(row["payload"])), row) for row in rows]

    def posting(self, seq: int) -> Posting | None:
        row = self.connection.execute("SELECT payload FROM postings WHERE seq=?", (seq,)).fetchone()
        return Posting.from_json_dict(json.loads(row["payload"])) if row else None

    def set_notion_page(self, seq: int, page_id: str) -> None:
        self.connection.execute("UPDATE postings SET notion_page_id=? WHERE seq=?", (page_id, seq))
        self.connection.commit()

    def delivered(self, seq: int) -> bool:
        return self.connection.execute("SELECT 1 FROM deliveries WHERE seq=?", (seq,)).fetchone() is not None

    def mark_delivered(self, seq: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO deliveries(seq,delivered_at) VALUES(?,?)",
            (seq, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.execute("DELETE FROM notification_queue WHERE seq=?", (seq,))
        self.connection.commit()

    def enqueue_delivery(self, seq: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO notification_queue(seq,queued_at) VALUES(?,?)",
            (seq, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def pending_deliveries(self) -> list[Posting]:
        rows = self.connection.execute(
            "SELECT p.payload FROM notification_queue q JOIN postings p ON p.seq=q.seq ORDER BY q.queued_at, q.seq"
        ).fetchall()
        return [Posting.from_json_dict(json.loads(row["payload"])) for row in rows]

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.connection.commit()
