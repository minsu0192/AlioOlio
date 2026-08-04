from __future__ import annotations

import argparse
import json
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .service import SyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ALIO → Notion → Telegram synchronizer")
    parser.add_argument("command", choices=["bootstrap", "sync", "enrich", "run"])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    service = SyncService(settings)
    if args.command == "bootstrap":
        print(json.dumps(service.bootstrap(), ensure_ascii=False, indent=2))
    elif args.command == "sync":
        print(json.dumps(service.sync(), ensure_ascii=False, indent=2))
    elif args.command == "enrich":
        print(json.dumps(service.enrich_interests(), ensure_ascii=False, indent=2))
    else:
        # Catch up immediately after downtime; normal deduplication makes this safe.
        service.sync()
        scheduler = BlockingScheduler(timezone=settings.timezone)
        scheduler.add_job(service.sync, CronTrigger(hour="8,17", minute=0, timezone=settings.timezone),
                          id="alio_sync", replace_existing=True, coalesce=True, misfire_grace_time=None)
        scheduler.add_job(service.refresh_filters, IntervalTrigger(minutes=5, timezone=settings.timezone),
                          id="filter_refresh", replace_existing=True, coalesce=True, max_instances=1)
        scheduler.start()


if __name__ == "__main__":
    main()
