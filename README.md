# AlioOlio

ALIO 채용공고를 오전 8시와 오후 5시(한국시간)에 확인해 Notion 캘린더로 정리하고, 조건에 맞는 신규 공고를 텔레그램으로 보내는 개인용 Python 서비스입니다.

## 주요 동작

- ALIO ID를 고유키로 사용하고 SQLite에 텔레그램 성공 기록을 남겨 중복 알림을 방지합니다.
- 프로그램 시작 시 즉시 보충 수집합니다. PC가 꺼져 있던 동안 등록된 공고도 마지막 성공 시각 이후 범위를 다시 조회해 알립니다.
- 첫 실행은 현재 접수 중인 공고만 Notion에 기준 데이터로 등록하고 기존 공고 알림은 보내지 않습니다.
- Notion `필터 설정` DB의 체크박스로 공식 분류와 포함/제외 키워드를 관리합니다.
- Notion `채용공고` DB에서 `캘린더 표시`를 해제하면 캘린더에서 즉시 사라집니다.
- `지원 관리 등록`을 체크하면 기존 `지원 현황` DB에 회사명, 직무, 마감일, 링크를 복사합니다. 공고 링크가 이미 있으면 다시 만들지 않습니다.

## 1. Notion 준비

1. [Notion integrations](https://www.notion.so/profile/integrations)에서 내부 통합을 만들고 읽기·삽입·수정 권한을 켭니다.
2. `민수 공기업 취업 준비 (2026)` 페이지의 연결 메뉴에서 이 통합을 초대합니다. 하위 `지원 현황`에도 접근 가능한지 확인합니다.
3. `.env.example`을 `.env`로 복사하고 `NOTION_TOKEN`을 입력합니다. 페이지와 기존 지원 현황 ID는 제공된 문서 구조에 맞춰 예시값이 들어 있습니다.

프로그램은 상위 페이지 아래에 다음을 자동 생성합니다.

- `ALIO 필터 설정`: 활성화 체크박스, 분류, 값, 공식/포함/제외 규칙
- `ALIO 채용공고`: 지원기간, 캘린더 표시, 필터 일치, 공고 정보, 지원 현황 연결
- 지원 캘린더, 관심 공고, 제외한 공고 보기

초기화를 다시 실행해 중복 DB가 생기지 않도록 생성 결과 ID는 SQLite에 보관합니다.

## 2. Telegram 준비

1. Telegram의 `@BotFather`에서 `/newbot`으로 봇을 만들고 토큰을 `.env`에 입력합니다.
2. 생성한 봇에게 메시지를 하나 보냅니다.
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `chat.id` 값을 `TELEGRAM_CHAT_ID`에 입력합니다.

## 3. 실행

Docker가 설치된 macOS/Linux에서:

```bash
cp .env.example .env
docker compose run --rm alio-olio alio-olio bootstrap
docker compose up -d
```

로그 확인:

```bash
docker compose logs -f alio-olio
```

수동 동기화:

```bash
docker compose run --rm alio-olio alio-olio sync
```

Docker 없이 실행하려면 Python 3.11 이상에서:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
alio-olio bootstrap
alio-olio run
```

`run`은 시작 직후 한 번 동기화한 다음 매일 08:00/17:00에 ALIO를 조회합니다. 필터 변경은 5분마다 로컬 저장 공고에 재적용합니다.

## 데이터와 장애 처리

- `data/alio_olio.db`를 백업하면 수집 이력과 중복 방지 상태를 보존할 수 있습니다.
- Telegram 전송이 실패하면 성공 기록을 남기지 않으므로 다음 동기화에서 다시 시도할 수 있습니다.
- ALIO나 Notion 호출이 실패하면 `last_successful_sync`를 갱신하지 않아 다음 실행이 누락 범위를 다시 가져옵니다.
- Notion의 `캘린더 표시`, `지원 관리 등록`은 사용자 관리 속성이며 자동 갱신이 덮어쓰지 않습니다.

## 테스트

```bash
pip install -e '.[test]'
pytest
```
