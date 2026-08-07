from __future__ import annotations

import httpx

from .domain import Posting


class TelegramClient:
    def __init__(self, token: str, chat_id: str, transport=None):
        self.chat_id = chat_id
        self.client = httpx.Client(base_url=f"https://api.telegram.org/bot{token}", timeout=20, transport=transport)

    def send_reminder(self, posting: Posting, days_left: int) -> None:
        when = "오늘 마감" if days_left == 0 else f"마감 D-{days_left}"
        lines = [
            f"⏰ {when} — 아직 제출하지 않았습니다",
            f"기관: {posting.organization}", f"제목: {posting.title}",
            f"마감: {posting.end_date:%Y-%m-%d}", posting.url,
        ]
        self._send("\n".join(lines))

    def send_posting(self, posting: Posting) -> None:
        lines = [
            "🔔 ALIO 새 채용공고", f"기관: {posting.organization}", f"제목: {posting.title}",
            f"지원기간: {posting.start_date:%Y-%m-%d} ~ {posting.end_date:%Y-%m-%d}",
            f"고용형태: {', '.join(posting.employment_types) or '-'}",
            f"근무지: {', '.join(posting.locations) or '-'}",
            f"채용인원: {posting.headcount if posting.headcount is not None else '-'}명", posting.url,
        ]
        self._send("\n".join(lines))

    def _send(self, text: str) -> None:
        response = self.client.post("/sendMessage", json={
            "chat_id": self.chat_id, "text": text, "disable_web_page_preview": True,
        })
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError(f"Telegram rejected message: {response.text}")
