from pathlib import Path

from alio_olio import cli


def test_fingerprint_changes_when_a_source_file_changes(tmp_path, monkeypatch):
    """상시 실행이라 고친 코드가 저절로 반영되지 않는다. 지문이 바뀌면 다시 뜬다."""
    before = cli.source_fingerprint()
    assert before == cli.source_fingerprint()  # 같은 소스면 같은 값

    target = Path(cli.__file__)
    original = target.read_bytes()
    try:
        target.write_bytes(original + "\n# 소스가 바뀐 상황\n".encode())
        assert cli.source_fingerprint() != before
    finally:
        target.write_bytes(original)
    assert cli.source_fingerprint() == before


def test_unchanged_source_does_not_restart():
    cli.restart_if_source_changed(cli.source_fingerprint())  # 아무 일도 일어나지 않는다


def test_changed_source_ends_the_process(monkeypatch):
    """워커 스레드에서 도는 함수라 sys.exit로는 프로세스가 안 죽는다."""
    ended = []
    monkeypatch.setattr(cli.os, "_exit", lambda code: ended.append(code))
    cli.restart_if_source_changed("다른 지문")
    assert ended == [cli.RESTART_EXIT_CODE]
    assert cli.RESTART_EXIT_CODE != 0  # launchd는 0이 아닐 때만 다시 띄운다
