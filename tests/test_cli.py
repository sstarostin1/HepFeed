"""Tests for the CLI check command."""

from __future__ import annotations

from pathlib import Path

import pytest

from hepfeed.cli import main


def test_check_reports_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLZA_API_KEY", "test-key")
    exit_code = main(["check"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "POLZA_API_KEY" in out
    assert "[set    ]" in out
    assert "TELEGRAM_BOT_TOKEN" in out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "hepfeed" in out.lower()


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "hepfeed" in capsys.readouterr().out
