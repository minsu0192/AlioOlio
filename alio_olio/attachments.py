from __future__ import annotations

import io
import logging
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass

from bs4 import BeautifulSoup

from . import ocr

log = logging.getLogger(__name__)

# ALIO 상세페이지가 쓰는 첨부 라벨 → 내부 키.
# "기타 첨부파일"은 자소서 양식이 여기 숨는 경우가 많아 함께 보관한다.
CATEGORIES = {
    "공고문": "notice",
    "입사지원서": "application",
    "직무기술서": "job_description",
    "기타 첨부파일": "etc",
}

_TEXT_EXTENSIONS = {".pdf"}
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")

# 글자가 이보다 적으면 스캔 이미지로 보고 OCR로 넘긴다.
_MIN_TEXT = 200
# 자소서 문항은 앞쪽에 몰려 있다. 전체를 OCR하면 느리기만 하다.
_MAX_OCR_PAGES = 12


@dataclass(frozen=True)
class Attachment:
    file_no: str
    name: str

    @property
    def extension(self) -> str:
        match = re.search(r"(\.[A-Za-z0-9]{2,5})$", self.name.strip())
        return match.group(1).lower() if match else ""

    def url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/download/download.json?fileNo={self.file_no}"


def parse_attachments(html: str) -> dict[str, list[Attachment]]:
    """상세페이지의 <li><span>라벨</span><a href=...fileNo=N>파일명</a></li> 묶음을 읽는다."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, list[Attachment]] = {key: [] for key in CATEGORIES.values()}
    for item in soup.find_all("li"):
        label_tag = item.find("span")
        if label_tag is None:
            continue
        key = CATEGORIES.get(label_tag.get_text(strip=True))
        if key is None:
            continue
        for link in item.select("a[href*=fileNo]"):
            match = re.search(r"fileNo=(\d+)", link.get("href", ""))
            name = link.get_text(" ", strip=True)
            if match and name:
                found[key].append(Attachment(match.group(1), name))
    return found


def to_text(attachment: Attachment, data: bytes) -> str | None:
    """첨부 바이트에서 텍스트를 뽑는다. 지원하지 않는 형식이면 None.

    호출부는 None을 실패가 아니라 "수동 입력 필요"로 취급해야 한다.
    """
    extension = attachment.extension
    if extension == ".pdf":
        return _pdf_text(data)
    if extension == ".hwp":
        return _hwp_text(data)
    if extension == ".zip":
        return _zip_text(data)
    return None


def to_tables(attachment: Attachment, data: bytes) -> list[list[list[str]]]:
    """PDF에서 표를 구조 그대로 뽑는다. 표가 없거나 지원 형식이 아니면 빈 리스트.

    공고문의 전형일정은 대부분 표이고, 발표일이 "합격자발표: 9.2.(수)"처럼 단계명 없이
    적힌다. 셀 경계가 있어야 그 값이 어느 단계의 것인지 알 수 있다.
    """
    if attachment.extension == ".zip":
        data = _zip_pdf_bytes(data) or b""
    elif attachment.extension != ".pdf":
        return []
    if not data:
        return []
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [[[cell or "" for cell in row] for row in table]
                    for page in pdf.pages for table in page.extract_tables()]
    except Exception as error:  # 손상된 PDF, pdfminer 내부 오류
        log.warning("PDF 표 추출 실패: %s", error)
        return []


def _pdf_text(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:  # 손상되거나 암호화된 PDF
        log.warning("PDF 텍스트 추출 실패: %s", error)
        return None
    # 스캔 이미지 PDF는 페이지 수만 많고 글자가 거의 없다. 이때만 OCR로 넘긴다.
    if len(text.strip()) > _MIN_TEXT:
        return text
    return _ocr_pdf_images(reader)


def _ocr_pdf_images(reader) -> str | None:
    """스캔 PDF는 페이지마다 이미지 한 장이 통째로 들어 있다. 그 이미지를 읽는다."""
    pages = []
    for page in reader.pages[:_MAX_OCR_PAGES]:
        try:
            images = list(page.images)
        except Exception:  # 지원하지 않는 필터로 압축된 이미지
            continue
        for embedded in images[:2]:
            recognized = ocr.image_text(embedded.data)
            if recognized:
                pages.append(recognized)
    text = "\n".join(pages)
    return text if len(text.strip()) > _MIN_TEXT else None


def _zip_text(data: bytes) -> str | None:
    inner = _zip_pdf_bytes(data)
    if inner:
        return _pdf_text(inner)
    # 자기소개서를 화면 캡처 PNG로만 올리는 기관도 있다(시청자미디어재단, 한국고용노동교육원).
    return _zip_image_text(data)


def _zip_image_text(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = sorted(n for n in archive.namelist()
                           if n.lower().endswith(_IMAGE_EXTENSIONS))
            pages = [ocr.image_text(archive.read(name)) for name in names[:_MAX_OCR_PAGES]]
    except (zipfile.BadZipFile, KeyError) as error:
        log.warning("ZIP 이미지 처리 실패: %s", error)
        return None
    text = "\n".join(page for page in pages if page)
    return text if len(text.strip()) > _MIN_TEXT else None


def _zip_pdf_bytes(data: bytes) -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(tuple(_TEXT_EXTENSIONS))]
            return archive.read(names[0]) if names else None
    except (zipfile.BadZipFile, KeyError) as error:
        log.warning("ZIP 첨부 처리 실패: %s", error)
        return None


# HWP v5는 OLE 복합 문서다. 본문은 BodyText/Section* 스트림에 레코드로 들어 있고,
# 글자는 HWPTAG_PARA_TEXT 레코드에 UTF-16LE로 담긴다.
_HWPTAG_PARA_TEXT = 67


def _hwp_text(data: bytes) -> str | None:
    try:
        import olefile

        if not olefile.isOleFile(io.BytesIO(data)):
            return None  # .hwpx(zip+xml)나 손상 파일
        ole = olefile.OleFileIO(io.BytesIO(data))
        compressed = bool(ole.openstream("FileHeader").read()[36] & 1)
        chunks = []
        for entry in ole.listdir():
            if entry[0] != "BodyText":
                continue
            stream = ole.openstream(entry).read()
            chunks.append(_hwp_section_text(zlib.decompress(stream, -15) if compressed else stream))
    except Exception as error:  # 예상 밖 구조, 압축 해제 실패
        log.warning("HWP 텍스트 추출 실패: %s", error)
        return None
    return _clean_hwp(" ".join(chunks))


# 본문에 표·구역·그리기 개체가 끼면 제어문자 1자 + 정보 6자 + 제어문자 1자, 총 8자가 박힌다.
# 가운데 6자를 그냥 두면 "氠瑢"(UTF-16 바이트를 되돌리면 "tbl") 같은 한자가 문장에 섞인다.
_HWP_INLINE_CONTROL = re.compile(r"[\x01-\x09\x0b\x0c\x0e-\x12].{6}[\x01-\x09\x0b\x0c\x0e-\x12]",
                                 re.DOTALL)


def _clean_hwp(text: str) -> str | None:
    text = _HWP_INLINE_CONTROL.sub(" ", text)
    text = re.sub(r"[\x00-\x1f]", " ", text)
    return text if len(text.strip()) > _MIN_TEXT else None


def _hwp_section_text(section: str | bytes) -> str:
    parts, offset = [], 0
    while offset < len(section) - 4:
        header = struct.unpack("<I", section[offset:offset + 4])[0]
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        offset += 4
        if size == 0xFFF:  # 4095를 넘는 레코드는 길이가 별도 4바이트로 따라온다
            size = struct.unpack("<I", section[offset:offset + 4])[0]
            offset += 4
        if tag == _HWPTAG_PARA_TEXT:
            parts.append(section[offset:offset + size].decode("utf-16le", "ignore"))
        offset += size
    return " ".join(parts)
