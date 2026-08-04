from __future__ import annotations

import re

from .attachments import Attachment

# 자소서 문항은 공고문이 아니라 입사지원서(또는 별도 자기소개서 양식)에 들어 있다.
# 이름에 "자기소개서"가 박힌 첨부가 있으면 그게 가장 정확하다.
_FORM_NAME = re.compile(r"자기소개서|자소서")

# 문항은 "~기술해 주십시오." 로 끝난다. 기관마다 어미가 조금씩 다르다.
# "주시기 바랍니다"는 서식 작성 안내문에도 쓰이므로 기술·서술·설명일 때만 인정한다.
# "기재"는 아예 뺐다 — "정확히 기재하여 주시기 바랍니다"류 안내문이 대량으로 딸려온다.
_ASK = (r"(?:(?:기술|서술|설명|작성)(?:해|하여|하)?\s*주?\s*(?:십시오|세요|시오)"
        r"|(?:기술|서술|설명)(?:해|하여)?\s*주시기\s*바랍니다)")
_ASKED = re.compile(_ASK + r"\.?$")

# 전남대학교병원처럼 "[라벨] 물어보는 내용 (최소 100자, 최대 500자)" 형태로 적는 곳도 있다.
# 자수 제한 괄호가 붙어 있어야 문항으로 인정한다 — 없으면 목차나 평가 기준과 구분되지 않는다.
_BRACKET_QUESTION = re.compile(
    r"\[([^\]]{2,30})\]\s*([^\[\]]{5,200}?)\s*(\(\s*(?:최소|최대)[^)]{0,40}자[^)]*\))")

# 문항 앞에 붙는 글머리와 자수 제한 표시. 문항 본문만 남긴다.
_LEAD = re.compile(r"^(?:\(\s*[\d,]+\s*자[^)]*\)|[•·▪◦\-*※]|\s)+")
# 문항 앞에 붙는 괄호 주의문("(출신학교명을 나타내는 용어 사용 금지)")
_LEAD_NOTE = re.compile(r"^\([^)]{5,60}(?:금지|제외|불가|주의)[^)]*\)\s*")
# 서식 잔해를 가려내는 표시들. 입사지원서 본문을 통째로 읽으면 앞 칸 내용이 딸려온다.
_CHECKBOXES = ("□", "■", "▢", "☐", "○", "●")
_CIRCLED = re.compile(r"[\u2460-\u2473]")
_HEADING = re.compile(r"^(?:자기소개서|자소서|직무능력\s*소개서|경력\s*및\s*경험\s*기술서|경험/경력기술서)\s*[:：]?\s*")
_SUB_NUMBER = re.compile(r"(?<![0-9])(\d+\s*-\s*\d+)\s*\.")

# 한 문항은 한 문장이다. 표의 여러 칸이 이어붙으면 다음 표식이 남는다 —
# ")(": 옆 칸과 맞붙은 자리, "*": 서식 각주. 앞머리 정리 뒤에도 남아 있으면 문항이 아니다.
_NOISE = re.compile(r"\)\s*\(|\*")

# 문항이 아니라 서류 작성·증빙 안내인 문장. "~작성하시오"로 끝나도 자소서 문항은 아니다.
_INSTRUCTION = re.compile(r"증빙|경력증명서|건강보험자격|발급")

# 문항 뒤에 붙는 안내문. 문항 자체가 아니므로 잘라낸다.
_NOTE = re.compile(r"[☞※▶].*", re.DOTALL)
_LIMIT = re.compile(r"\(\s*띄어쓰기[^)]*\)|\(\s*\d[^)]*자\s*이내\s*\)")

# 굵은 글씨를 글자 단위로 두 번 찍는 PDF가 있다("본본인인이이"). 공백은 겹치지 않으므로
# 한글만 접으면 문장이 읽을 수 있게 복원된다.
_CHAR_DOUBLED = re.compile(r"([가-힣])\1")
_PRIVATE_USE = re.compile("[\uf000-\uf8ff]")

MAX_QUESTIONS = 20


def pick_form(attachments: dict[str, list[Attachment]]) -> Attachment | None:
    """자소서 문항이 들어 있을 첨부를 고른다.

    이름에 "자기소개서"가 있으면 어느 분류에 있든 우선한다. 없으면 입사지원서를 쓴다.
    """
    for group in ("application", "etc", "notice"):
        for item in attachments.get(group, []):
            if _FORM_NAME.search(item.name):
                return item
    return next(iter(attachments.get("application", [])), None)


def normalize_cell(cell: str) -> str:
    """셀 글자를 사람이 읽을 수 있게 되돌린다. 공백은 하나로 줄이되 없애지는 않는다."""
    text = _PRIVATE_USE.sub("", cell or "")
    hangul = sum(1 for char in text if "가" <= char <= "힣")
    if hangul >= 6 and len(_CHAR_DOUBLED.findall(text)) * 2 > hangul * 0.6:
        text = _CHAR_DOUBLED.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", text).strip()


def extract_questions(tables: list[list[list[str]]], text: str | None = None) -> list[str]:
    """입사지원서에서 자소서 문항을 순서대로 뽑는다.

    양식이 표로 짜인 경우(한수원)는 큰 문항이 한 셀에, 하위 문항(1-1, 1-2)이 다음 셀에
    들어 있다. 표 밖 본문에 문항을 늘어놓는 기관(한국부동산원)도 있어 둘 다 훑는다.
    """
    found: list[str] = []
    for table in tables:
        for row in table:
            for cell in row:
                _add(found, _cell_questions(normalize_cell(cell)))
    if text and not found:
        flat = normalize_cell(text)
        _add(found, _text_questions(flat))
        _add(found, _bracket_questions(flat))
    return found[:MAX_QUESTIONS]


def _add(found: list[str], questions: list[str]) -> None:
    """같은 문항이 자수 제한 유무만 다르게 두 번 잡히는 일이 잦다. 긴 쪽만 남긴다."""
    for question in questions:
        key = _key(question)
        for index, existing in enumerate(found):
            other = _key(existing)
            if key.startswith(other) or other.startswith(key):
                if len(key) > len(other):
                    found[index] = question
                break
        else:
            found.append(question)


def _key(question: str) -> str:
    return re.sub(r"[^가-힣0-9a-zA-Z]", "", question)


# 본문에서는 문장 끝의 "~기술해 주십시오"를 기준으로 앞쪽을 문항으로 잘라낸다.
# 너무 길게 잡으면 앞 문단까지 딸려오므로 길이를 제한한다.
_TEXT_QUESTION = re.compile(r"[^.?!\n]{15,240}?" + _ASK + r"[.]?")


def _text_questions(text: str) -> list[str]:
    questions = []
    for match in _TEXT_QUESTION.finditer(text):
        candidate = _strip_lead(_NOTE.sub("", match.group(0)))
        if candidate.startswith("*") or not _is_question(candidate):
            continue  # "*"로 시작하면 서식 작성 안내문이다
        questions.append(candidate)
    return questions


def _bracket_questions(text: str) -> list[str]:
    """"[지원동기] 전남대학교병원에 지원한 동기 (최소 100자, 최대 500자)" 형태를 읽는다."""
    questions = []
    for label, body, limit in _BRACKET_QUESTION.findall(text):
        body = body.strip(" .")
        if len(body) > 8:
            questions.append(f"[{label.strip()}] {body} {limit.strip()}")
    return questions


def _strip_lead(text: str) -> str:
    """문항 앞에 딸려온 표 제목·글머리·서식 잔해를 떼어낸다.

    입사지원서를 통째로 읽으면 문항 앞에 앞 칸의 내용이 붙어 나온다.
    "□기타 □경험 □경력 …", "여부)(장애대상 여부)… 직무 외", "자기소개서 … 각 항목 400자 이내 ①"
    같은 앞머리가 실제로 관측됐다.
    """
    # 체크박스는 서식이지 문항이 아니다. 마지막 체크박스 뒤부터가 본문이다.
    box = max(text.rfind(mark) for mark in _CHECKBOXES)
    if box >= 0 and len(text) - box > 15:
        text = text[box + 1:]
    # 동그라미 번호는 문항 번호이므로 잘라내지 말고 거기서부터 남긴다.
    circled = _CIRCLED.search(text[:80])
    if circled and circled.start() > 0:
        text = text[circled.start():]
    for mark in ("•", "▪", "◦"):
        position = text.find(mark, 0, 40)
        if position >= 0:
            text = text[position + 1:]
    bracket = text.find("[", 0, 60)
    if bracket > 0:
        text = text[bracket:]
    text = _LEAD_NOTE.sub("", text)
    return _HEADING.sub("", _LEAD.sub("", text)).strip(" .…")


def _cell_questions(text: str) -> list[str]:
    body = _NOTE.sub("", text).strip()
    if not body:
        return []
    parts = _SUB_NUMBER.split(body)
    if len(parts) == 1:
        # 하위 번호가 없는 셀이면 통째로 하나의 문항인지 본다.
        return [body] if _is_question(body) else []
    questions = []
    for number, chunk in zip(parts[1::2], parts[2::2]):
        chunk = _NOTE.sub("", chunk).strip(" .")
        if _is_question(chunk):
            questions.append(f"{number.replace(' ', '')}. {chunk}")
    return questions


def _is_question(text: str) -> bool:
    stripped = _LIMIT.sub("", text).strip(" .")
    if len(stripped) <= 12 or _NOISE.search(stripped) or _INSTRUCTION.search(stripped):
        return False
    return bool(_ASKED.search(stripped))


def format_questions(questions: list[str]) -> str:
    return "\n".join(questions)
