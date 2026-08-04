from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 공고문·입사지원서를 이미지로만 올리는 기관이 적지 않다(스캔 PDF, 화면 캡처 PNG).
# macOS에 내장된 Vision 프레임워크는 한국어 인식이 좋고 오프라인에서 무료로 돌아간다.
# API 키가 필요 없고 파일이 밖으로 나가지 않는다. macOS가 아니면 조용히 포기한다.
_LANGUAGES = ["ko-KR", "en-US"]
_ACCURATE = 0

_unavailable_logged = False


def available() -> bool:
    return _vision() is not None


def image_text(data: bytes) -> str | None:
    """이미지 바이트에서 글자를 읽는다. OCR을 쓸 수 없으면 None."""
    vision = _vision()
    if vision is None:
        return None
    Vision, Quartz = vision
    try:
        source = Quartz.CGImageSourceCreateWithData(_cfdata(Quartz, data), None)
        if source is None or Quartz.CGImageSourceGetCount(source) == 0:
            return None
        image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(_LANGUAGES)
        request.setRecognitionLevel_(_ACCURATE)
        request.setUsesLanguageCorrection_(True)
        performed, error = handler.performRequests_error_([request], None)
        if not performed:
            log.warning("OCR 실패: %s", error)
            return None
        lines = [box.topCandidates_(1)[0].string() for box in (request.results() or [])]
    except Exception as error:  # 손상된 이미지, 프레임워크 호출 실패
        log.warning("OCR 실패: %s", error)
        return None
    return "\n".join(lines) or None


def _cfdata(Quartz, data: bytes):
    return Quartz.CFDataCreate(None, data, len(data))


def _vision():
    global _unavailable_logged
    try:
        import Quartz
        import Vision

        return Vision, Quartz
    except ImportError:
        if not _unavailable_logged:
            log.info("OCR을 쓸 수 없습니다(macOS + pyobjc-framework-Vision 필요). "
                     "이미지 공고문은 직접 입력해야 합니다.")
            _unavailable_logged = True
        return None
