from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from .alio import AlioClient
from .config import Settings
from .filters import matches
from .notion import NotionClient
from .storage import Storage
from .telegram import TelegramClient

log = logging.getLogger(__name__)


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
            row = next(row for item, row in self.storage.postings() if item.seq == posting.seq)
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
        return changed

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
