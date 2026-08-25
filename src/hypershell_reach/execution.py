from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Target


@dataclass(frozen=True)
class StreamResult:
    text: str
    bytes: int
    truncated: bool


async def _read_bounded(reader: asyncio.StreamReader, limit: int) -> StreamResult:
    retained = bytearray()
    total = 0
    while True:
        chunk = await reader.read(8192)
        if not chunk:
            break
        total += len(chunk)
        remaining = limit - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
    return StreamResult(
        text=retained.decode("utf-8", errors="replace"),
        bytes=total,
        truncated=total > limit,
    )


def build_ssh_argv(target: Target, connect_timeout_seconds: int, remote_command: str) -> list[str]:
    ssh = target.ssh
    if not ssh.host:
        raise ValueError("target SSH host is missing")
    if "\x00" in remote_command:
        raise ValueError("remote command must not contain NUL bytes")
    return [
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={ssh.known_hosts_file}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ControlMaster=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "VerifyHostKeyDNS=no",
        "-o",
        "RequestTTY=no",
        "-o",
        f"ConnectTimeout={connect_timeout_seconds}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        "-p",
        str(ssh.port),
        "-i",
        ssh.identity_file,
        f"{ssh.user}@{ssh.host}",
        remote_command,
    ]


def _redact(text: str, target_id: str, target: Target) -> str:
    ssh = target.ssh
    values = [
        ssh.identity_file,
        ssh.known_hosts_file,
        ssh.host or "",
        f"{ssh.user}@{ssh.host}" if ssh.host else "",
    ]
    result = text
    for value in sorted((value for value in values if value), key=len, reverse=True):
        result = result.replace(value, f"[{target_id}]")
    return result


def classify_status(*, timed_out: bool, exit_code: int | None) -> str:
    if timed_out:
        return "timeout"
    if exit_code == 0:
        return "succeeded"
    if exit_code == 255:
        return "transport_error"
    return "remote_error"


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def run_ssh(
    *,
    target_id: str,
    target: Target,
    remote_command: str,
    timeout_seconds: int,
    connect_timeout_seconds: int,
    max_output_bytes: int,
    stdin_text: str | None = None,
) -> dict[str, object]:
    ssh = target.ssh
    for path_name, path_value in (
        ("identity", ssh.identity_file),
        ("known_hosts", ssh.known_hosts_file),
    ):
        path = Path(path_value)
        if not path.is_file():
            raise RuntimeError(f"configured {path_name} file is unavailable for target {target_id}")

    argv = build_ssh_argv(target, connect_timeout_seconds, remote_command)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stdout_task = asyncio.create_task(_read_bounded(process.stdout, max_output_bytes))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, max_output_bytes))

    if stdin_text is not None:
        assert process.stdin is not None
        process.stdin.write(stdin_text.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        await _kill_process_group(process)
    except asyncio.CancelledError:
        await _kill_process_group(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise

    stdout = await stdout_task
    stderr = await stderr_task
    duration_ms = round((time.monotonic() - started) * 1000)
    exit_code = process.returncode

    return {
        "target": target_id,
        "status": classify_status(timed_out=timed_out, exit_code=exit_code),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": {
            "text": stdout.text,
            "bytes": stdout.bytes,
            "truncated": stdout.truncated,
        },
        "stderr": {
            "text": _redact(stderr.text, target_id, target),
            "bytes": stderr.bytes,
            "truncated": stderr.truncated,
        },
    }
