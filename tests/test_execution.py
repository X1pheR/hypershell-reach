from __future__ import annotations

import asyncio

import pytest

from hats_mcp.config import Target
from hats_mcp.execution import _read_bounded, _redact, build_ssh_argv, classify_status


def _target() -> Target:
    return Target.model_validate(
        {
            "display_name": "Example",
            "capabilities": ["linux"],
            "ssh": {
                "host": "203.0.113.10",
                "user": "operator",
                "identity_file": "/run/secrets/hats/key",
                "known_hosts_file": "/run/secrets/hats/known_hosts",
            },
        }
    )


def test_ssh_argv_enforces_transport_boundary() -> None:
    argv = build_ssh_argv(_target(), 10, "hostname")
    joined = " ".join(argv)
    assert argv[0] == "/usr/bin/ssh"
    assert argv[1:3] == ["-F", "/dev/null"]
    for expected in (
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "ForwardAgent=no",
        "ClearAllForwardings=yes",
        "PermitLocalCommand=no",
        "ProxyCommand=none",
        "RequestTTY=no",
    ):
        assert expected in joined
    assert argv[-2:] == ["operator@203.0.113.10", "hostname"]


def test_remote_command_rejects_nul_bytes() -> None:
    with pytest.raises(ValueError, match="NUL"):
        build_ssh_argv(_target(), 10, "printf 'a\x00b'")


def test_error_redaction_hides_host_and_paths() -> None:
    target = _target()
    text = "203.0.113.10 /run/secrets/hats/key /run/secrets/hats/known_hosts"
    redacted = _redact(text, "example", target)
    assert "203.0.113.10" not in redacted
    assert "/run/secrets" not in redacted
    assert "[example]" in redacted


@pytest.mark.asyncio
async def test_bounded_reader_reports_truncation() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"abcdefgh")
    reader.feed_eof()
    result = await _read_bounded(reader, 5)
    assert result.text == "abcde"
    assert result.bytes == 8
    assert result.truncated is True


@pytest.mark.parametrize(
    ("timed_out", "exit_code", "expected"),
    [
        (True, -9, "timeout"),
        (False, 0, "succeeded"),
        (False, 255, "transport_error"),
        (False, 1, "remote_error"),
        (False, None, "remote_error"),
    ],
)
def test_status_classification(timed_out: bool, exit_code: int | None, expected: str) -> None:
    assert classify_status(timed_out=timed_out, exit_code=exit_code) == expected
