"""Unit tests for Stixis CLI subcommands, exit codes, and subprocess entrypoint."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from stixis.cli import (
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    EXIT_VALIDATION_ERROR,
)
from stixis.cli import (
    main as cli_main,
)


def test_cli_inspect_and_check_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["inspect", "font-style"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert "font-style (property)" in captured.out
    assert "syntax:" in captured.out

    rc = cli_main(["inspect", "@font-face", "--json"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    payload = json.loads(captured.out)
    assert payload["kind"] == "at-rule"
    assert payload["name"] == "@font-face"
    assert "unicode-range" in payload["descriptors"]
    assert "font-stretch" in payload["descriptors"]

    rc = cli_main(["inspect", "<font-src>"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert "<font-src> (type)" in captured.out

    rc = cli_main(["inspect", "unknown-nonexistent-target"])
    captured = capsys.readouterr()
    assert rc == EXIT_NOT_FOUND
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1

    rc = cli_main(["check", "font-weight", "100 900", "--at-rule", "@font-face"])
    captured = capsys.readouterr()
    assert rc == EXIT_OK
    assert captured.out.strip() == "100 900"

    rc = cli_main(["check", "font-weight", "2000"])
    captured = capsys.readouterr()
    assert rc == EXIT_VALIDATION_ERROR
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1

    rc = cli_main(["check", "unknown-prop", "10px"])
    captured = capsys.readouterr()
    assert rc == EXIT_NOT_FOUND
    assert len(captured.err.strip().splitlines()) == 1

    rc = cli_main(["check", "@font-face", "foo"])
    captured = capsys.readouterr()
    assert rc == EXIT_NOT_FOUND
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1

    rc = cli_main(["check"])
    captured = capsys.readouterr()
    assert rc == EXIT_USAGE_ERROR
    assert len(captured.err.strip().splitlines()) == 1


def test_cli_subprocess_entrypoint() -> None:
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "stixis.cli",
            "check",
            "unicode-range",
            "u+3040-309f",
            "--at-rule",
            "@font-face",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "U+3040-309F"
