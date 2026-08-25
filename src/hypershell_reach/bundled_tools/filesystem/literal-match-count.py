#!/usr/bin/env python3
# ---
# id: filesystem.literal-match-count
# name: Count Literal File Matches
# description: Assert the exact number of literal byte-sequence matches in a file without modifying either file.
# domain: filesystem
# interpreter: python3
# requires: [linux]
# mutating: false
# idempotent: true
# timeout_seconds: 60
# arguments:
#   - name: file
#     type: string
#     required: true
#     pattern: '^/[^\r\n\t]+$'
#     max_length: 4096
#   - name: needle_file
#     type: string
#     required: true
#     pattern: '^/[^\r\n\t]+$'
#     max_length: 4096
#   - name: expected
#     type: integer
#     required: true
#     minimum: 0
#     maximum: 1000000
# ---
"""Assert an exact literal byte-sequence count without changing files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_NEEDLE_BYTES = 1024 * 1024


def _read_bounded(path: Path, maximum: int, label: str) -> tuple[bytes | None, str | None]:
    if not path.is_absolute():
        return None, f"{label} path must be absolute"
    try:
        if not path.is_file():
            return None, f"{label} is not a regular file"
        size = path.stat().st_size
        if size > maximum:
            return None, f"{label} exceeds {maximum} bytes"
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"{label} is unreadable: {type(exc).__name__}"


def count_matches(file: Path, needle_file: Path, expected: int) -> tuple[dict[str, object], int]:
    target, error = _read_bounded(file, MAX_FILE_BYTES, "file")
    if error is not None or target is None:
        return {"tool": "filesystem.literal-match-count", "status": "error", "error": error}, 2

    needle, error = _read_bounded(needle_file, MAX_NEEDLE_BYTES, "needle file")
    if error is not None or needle is None:
        return {"tool": "filesystem.literal-match-count", "status": "error", "error": error}, 2
    if not needle:
        return {
            "tool": "filesystem.literal-match-count",
            "status": "error",
            "error": "needle file must not be empty",
        }, 2

    actual = target.count(needle)
    passed = actual == expected
    return {
        "tool": "filesystem.literal-match-count",
        "status": "passed" if passed else "failed",
        "file": str(file),
        "expected": expected,
        "actual": actual,
    }, 0 if passed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--needle-file", required=True)
    parser.add_argument("--expected", required=True, type=int)
    args = parser.parse_args()
    if args.expected < 0:
        parser.error("--expected must be zero or greater")
    return args


def main() -> int:
    args = parse_args()
    payload, exit_code = count_matches(Path(args.file), Path(args.needle_file), args.expected)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
