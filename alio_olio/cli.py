from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .service import SyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ALIO → Notion → Telegram synchronizer")
    parser.add_argument("command", choices=["bootstrap", "sync", "enrich", "run"])
    return parser


def configure_logging(log_path: str | None = None) -> None:
    """상시 실행을 견디는 로깅.

    httpx는 요청마다, pdfminer는 글꼴마다 한 줄씩 남긴다. 동기화 한 번에 수백 줄이라
    데몬으로 며칠만 돌려도 로그가 쓸모없어지므로 경고 이상만 남긴다.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3,
                                            encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "pdfminer", "PIL", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    # 예약 실행일 때만 파일로도 남긴다. 터미널에서 한 번 돌릴 때는 화면이면 충분하다.
    log_path = str(Path(settings.database_path).parent / "scheduler.log") if args.command == "run" else None
    configure_logging(log_path)
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
