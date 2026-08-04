import io
import zipfile

from alio_olio.attachments import Attachment, parse_attachments, to_text

# ALIO 상세페이지의 실제 첨부 마크업 구조.
HTML = """
<div class="list2"><ul>
  <li><span>공고문</span><div class="add-file-area">
    <a href="/download/download.json?fileNo=3054821" target="_blank">2026년 채용 공고.pdf</a>
    <button class="preView" v-on:click="previewAjax('3054821','x','N');"></button>
  </div></li>
  <li><span>입사지원서</span><div class="add-file-area">
    <a href="/download/download.json?fileNo=3054823">입사지원서.hwp</a>
  </div></li>
  <li><span>직무기술서</span><div class="add-file-area">
    <a href="/download/download.json?fileNo=3054822">Job Description.pdf</a>
  </div></li>
  <li><span>기타 첨부파일</span><div class="add-file-area">
    <a href="/download/download.json?fileNo=3054824">자기소개서 양식.pdf</a>
    <a href="/download/download.json?fileNo=3054825">개인정보 동의서.hwp</a>
  </div></li>
  <li><span>미첨부 사유</span>공고문 참고</li>
</ul></div>
"""


def test_attachments_are_grouped_by_label():
    found = parse_attachments(HTML)
    assert [(a.file_no, a.name) for a in found["notice"]] == [("3054821", "2026년 채용 공고.pdf")]
    assert found["job_description"][0].file_no == "3054822"
    assert found["application"][0].extension == ".hwp"
    assert len(found["etc"]) == 2


def test_missing_categories_are_empty_not_absent():
    found = parse_attachments("<ul><li><span>공고문</span></li></ul>")
    assert found["notice"] == []
    assert found["job_description"] == []


def test_download_url_is_absolute():
    assert Attachment("42", "a.pdf").url("https://www.alio.go.kr/") == \
        "https://www.alio.go.kr/download/download.json?fileNo=42"


def test_unsupported_formats_return_none_instead_of_raising():
    # HWP는 파서가 없다. 호출부는 이를 실패가 아니라 "수동 입력 필요"로 다뤄야 한다.
    assert to_text(Attachment("1", "공고문.hwp"), b"\xd0\xcf\x11\xe0") is None
    assert to_text(Attachment("1", "공고문.pdf"), b"not a pdf") is None


def test_ocr_is_optional_and_never_raises(monkeypatch):
    """OCR을 쓸 수 없는 환경(리눅스 도커 등)에서도 이미지 첨부는 조용히 넘어가야 한다."""
    from alio_olio import attachments as module

    monkeypatch.setattr(module.ocr, "image_text", lambda data: None)
    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as archive:
        archive.writestr("자기소개서.png", b"\x89PNG not really")
    assert to_text(Attachment("1", "입사지원서.zip"), empty_zip.getvalue()) is None


def test_images_in_a_zip_are_read_when_ocr_works(monkeypatch):
    from alio_olio import attachments as module

    monkeypatch.setattr(module.ocr, "image_text",
                        lambda data: "지원 동기를 구체적으로 기술해 주십시오. " * 12)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("자기소개서.png", b"\x89PNG")
    text = to_text(Attachment("1", "입사지원서.zip"), buffer.getvalue())
    assert text and "기술해 주십시오" in text


def test_hwp_inline_object_controls_do_not_leak_into_the_text():
    """HWP 본문에 표·그리기 개체가 끼면 제어문자 8자 묶음이 박힌다.

    가운데 6자를 지우지 않으면 UTF-16 바이트가 한자로 읽혀 "氠瑢"(=tbl)처럼 문장에 섞인다.
    """
    from alio_olio.attachments import _clean_hwp

    # 제어문자 1자 + 정보 6자("氠瑢" 포함) + 제어문자 1자
    polluted = "앞 문장입니다 \x0b氠瑢\x00\x00\x00\x00\x0b 뒤 문장입니다 " + "가" * 200
    cleaned = _clean_hwp(polluted)
    assert "氠" not in cleaned and "瑢" not in cleaned
    assert "앞 문장입니다" in cleaned and "뒤 문장입니다" in cleaned
