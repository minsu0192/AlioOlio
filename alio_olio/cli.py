from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
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


log = logging.getLogger(__name__)


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


RESTART_EXIT_CODE = 3  # launchd가 다시 띄우도록 0이 아닌 값으로 끝낸다


def source_fingerprint() -> str:
    """패키지 소스의 지문. 파일 내용이 바뀌면 값이 달라진다."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def restart_if_source_changed(previous: str) -> None:
    """코드가 바뀌었으면 스스로 끝낸다. launchd가 새 코드로 다시 띄운다.

    상시 실행이라 고친 코드가 저절로 반영되지 않는다. 실제로 `구분`을 채우는 수정을
    커밋하고도 한참 동안 옛 코드가 돌아 새 공고에 색이 빠졌다.
    """
    if source_fingerprint() != previous:
        log.info("코드가 바뀌었습니다. 새 버전으로 다시 시작합니다.")
        # 이 함수는 스케줄러의 워커 스레드에서 돈다. sys.exit는 그 스레드에서만
        # 예외를 던지고 프로세스는 계속 살아 있으므로 직접 끝낸다. 동기화가 중간에
        # 끊겨도 last_successful_sync를 안 남겼으니 다음 실행이 그 구간을 다시 받는다.
        logging.shutdown()
        os._exit(RESTART_EXIT_CODE)


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    # 예약 실행일 때만 파일로도 남긴다. 터미널에서 한 번 돌릴 때는 화면이면 충분하다.
    log_path = str(Path(settings.database_path).parent / "scheduler.log") if args.command == "run" else None
    configure_logging(log_path)
    # 지문은 프로세스가 뜨자마자 뜬다. 첫 동기화가 끝난 뒤에 뜨면 그 몇 분 사이의
    # 코드 변경이 기준값에 섞여 들어가 영영 감지되지 않는다.
    fingerprint = source_fingerprint()
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
        scheduler.add_job(service.sync,
                          CronTrigger(hour=settings.sync_hours, minute=0, timezone=settings.timezone),
                          id="alio_sync", replace_existing=True, coalesce=True, misfire_grace_time=None)
        # 동기화 한 번이 몇 분씩 걸린다. 기본 유예는 1초라 그 사이에 걸린 주기 작업이
        # 통째로 건너뛰어졌다("Run time of job ... was missed by 0:10:59"). 늦더라도
        # 한 번은 돌게 유예를 넉넉히 준다.
        scheduler.add_job(lambda: restart_if_source_changed(fingerprint),
                          IntervalTrigger(minutes=2), id="source_watch",
                          replace_existing=True, coalesce=True, max_instances=1,
                          misfire_grace_time=600)
        scheduler.add_job(service.refresh_filters,
                          IntervalTrigger(minutes=settings.filter_refresh_minutes, timezone=settings.timezone),
                          id="filter_refresh", replace_existing=True, coalesce=True, max_instances=1,
                          misfire_grace_time=600)
        scheduler.add_job(service.remind_submissions,
                          CronTrigger(hour=settings.reminder_hours, minute=0, timezone=settings.timezone),
                          id="submission_reminder", replace_existing=True, coalesce=True, max_instances=1,
                          misfire_grace_time=1800)
        log.info("동기화 %s시 정각, 필터 갱신 %d분마다, 제출 리마인더 %s시 (마감 D-%d부터)",
                 settings.sync_hours, settings.filter_refresh_minutes,
                 settings.reminder_hours, settings.reminder_days)
        scheduler.start()


if __name__ == "__main__":
    main()
