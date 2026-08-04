from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    notion_token: str
    notion_parent_page_id: str
    telegram_bot_token: str
    telegram_chat_id: str
    notion_application_data_source_id: str = ""
    notion_schedule_data_source_id: str = ""
    database_path: str = "data/alio_olio.db"
    timezone: str = "Asia/Seoul"
    notion_api_version: str = "2026-03-11"
    alio_base_url: str = "https://www.alio.go.kr"

    @classmethod
    def from_env(cls, require_integrations: bool = True) -> "Settings":
        load_dotenv()
        values = {
            "notion_token": os.getenv("NOTION_TOKEN", ""),
            "notion_parent_page_id": os.getenv("NOTION_PARENT_PAGE_ID", ""),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        }
        if require_integrations:
            missing = [k.upper() for k, v in values.items() if not v]
            if missing:
                raise ValueError("Missing environment variables: " + ", ".join(missing))
        return cls(
            **values,
            notion_application_data_source_id=os.getenv("NOTION_APPLICATION_DATA_SOURCE_ID", ""),
            notion_schedule_data_source_id=os.getenv("NOTION_SCHEDULE_DATA_SOURCE_ID", ""),
            database_path=os.getenv("DATABASE_PATH", "data/alio_olio.db"),
            timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
            notion_api_version=os.getenv("NOTION_API_VERSION", "2026-03-11"),
            alio_base_url=os.getenv("ALIO_BASE_URL", "https://www.alio.go.kr"),
        )
