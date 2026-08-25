#!/usr/bin/env python3
# ---
# id: filesystem.compare-modes
# name: Compare File Modes
# description: Compare current filesystem mode/type state against a bounded v1 snapshot contract without modifying files.
# domain: filesystem
# interpreter: python3
# requires: [linux]
# mutating: false
# idempotent: true
# timeout_seconds: 120
# arguments:
#   - name: snapshot
#     type: string
#     required: true
#     pattern: '^/[^\r\n\t]+$'
#     max_length: 4096
# ---
"""Compare filesystem mode/type state against a v1 snapshot contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys

HEADER = "# agent-tooling-file-modes-v1"
MAX_SNAPSHOT_BYTES = 131_072
MAX_ENTRIES = 256


def path_type(path: Path) -> str:
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    if path.exists():
        return "other"
    return "missing"


def mode_string(path: Path) -> str:
    return f"{stat.S_IMODE(path.stat().st_mode):o}"


def load_snapshot(path: Path) -> tuple[list[tuple[str, str, str]] | None, str | None]:
    if not path.is_absolute():
        return None, "snapshot path must be absolute"
    if not path.is_file():
        return None, "snapshot is not a readable file"
    try:
        size = path.stat().st_size
        if size > MAX_SNAPSHOT_BYTES:
            return None, f"snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes"
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"snapshot is unreadable: {type(exc).__name__}"
    if not lines or lines[0] != HEADER:
        return None, "unsupported snapshot format"

    records: list[tuple[str, str, str]] = []
    for line_number, line in enumerate(lines[1:], start=2):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            return None, f"invalid snapshot row at line {line_number}"
        expected_mode, expected_type, target = parts
        if expected_type not in {"file", "directory", "other"}:
            return None, f"invalid snapshot type at line {line_number}"
        if not target.startswith("/") or "\r" in target or "\n" in target:
            return None, f"invalid snapshot path at line {line_number}"
        records.append((expected_mode, expected_type, target))
        if len(records) > MAX_ENTRIES:
            return None, f"snapshot exceeds {MAX_ENTRIES} entries"
    return records, None


def compare(snapshot: Path) -> tuple[dict[str, object], int]:
    records, error = load_snapshot(snapshot)
    if error is not None or records is None:
        return {"tool": "filesystem.compare-modes", "status": "error", "error": error}, 2

    details: list[dict[str, str]] = []
    for expected_mode, expected_type, target in records:
        path = Path(target)
        actual_type = path_type(path)
        if actual_type == "missing":
            actual_mode = "missing"
        else:
            try:
                actual_mode = mode_string(path)
            except OSError:
                actual_mode = "unreadable"
        if actual_mode != expected_mode or actual_type != expected_type:
            details.append(
                {
                    "path": target,
                    "expected_mode": expected_mode,
                    "actual_mode": actual_mode,
                    "expected_type": expected_type,
                    "actual_type": actual_type,
                }
            )

    payload: dict[str, object] = {
        "tool": "filesystem.compare-modes",
        "operation": "compare",
        "snapshot": str(snapshot),
        "checked": len(records),
        "mismatches": len(details),
        "details": details,
    }
    return payload, 0 if not details else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload, exit_code = compare(Path(args.snapshot))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
