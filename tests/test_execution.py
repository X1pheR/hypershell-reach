from __future__ import annotations

import asyncio

import pytest

from hypershell_reach.config import Target
from hypershell_reach.execution import _read_bounded, _redact, build_ssh_argv, classify_status


def _target() -> Target:
    return Target.model_validate(
        {
            "display_name": "Example",
            "capabilities": ["linux"],
            "ssh": {
                "host": "203.0.113.10",
                "user": "operator",
                "identity_file": "/run/secrets/reach/key",
                "known_hosts_file": "/run/secrets/reach/known_hosts",
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
    text = "203.0.113.10 /run/secrets/reach/key /run/secrets/reach/known_hosts"
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


@pytest.mark.asyncio
async def test_cancellation_kills_ssh_process_group(tmp_path, monkeypatch) -> None:
    identity = tmp_path / "id_ed25519"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("test", encoding="utf-8")
    known_hosts.write_text("test", encoding="utf-8")
    target = Target.model_validate(
        {
            "display_name": "Example",
            "capabilities": ["linux"],
            "ssh": {
                "host": "203.0.113.10",
                "user": "operator",
                "identity_file": str(identity),
                "known_hosts_file": str(known_hosts),
            },
        }
    )

    class FakeProcess:
        pid = 4242
        returncode = None
        stdin = None

        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self._done = asyncio.Event()

        async def wait(self):
            await self._done.wait()
            return self.returncode

        def killed(self) -> None:
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._done.set()

    process = FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    kill_calls: list[tuple[int, int]] = []

    def fake_killpg(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))
        process.killed()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("hypershell_reach.execution.os.killpg", fake_killpg)

    task = asyncio.create_task(
        __import__("hypershell_reach.execution", fromlist=["run_ssh"]).run_ssh(
            target_id="example",
            target=target,
            remote_command="sleep 300",
            timeout_seconds=300,
            connect_timeout_seconds=10,
            max_output_bytes=1024,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert kill_calls == [(4242, __import__("signal").SIGKILL)]
    assert process.returncode == -9
