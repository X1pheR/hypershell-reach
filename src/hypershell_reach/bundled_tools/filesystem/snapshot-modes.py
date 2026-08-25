#!/usr/bin/env python3
# ---
# id: filesystem.snapshot-modes
# name: Snapshot File Modes
# description: Write a bounded v1 mode/type contract for explicit absolute filesystem paths without modifying the target paths.
# domain: filesystem
# interpreter: python3
# requires: [linux]
# mutating: true
# idempotent: false
# timeout_seconds: 120
# arguments:
#   - name: output
#     type: string
#     required: true
#     pattern: '^/[^\r\n\t]+$'
#     max_length: 4096
#   - name: force
#     type: boolean
#     required: false
#   - name: path
#     type: string_list
#     required: true
#     pattern: '^/[^\r\n\t]+$'
#     max_length: 4096
#     min_items: 1
#     max_items: 256
# ---
"""Write a bounded file mode/type snapshot contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

HEADER = "# agent-tooling-file-modes-v1"


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


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


def snapshot(output: Path, paths: list[Path], force: bool) -> tuple[dict[str, object], int]:
    if not output.is_absolute():
        return {"tool": "filesystem.snapshot-modes", "status": "error", "error": "output path must be absolute"}, 2
    if output.exists() and not force:
        return {
            "tool": "filesystem.snapshot-modes",
            "status": "error",
            "error": "snapshot already exists; set force=true to replace it",
        }, 2

    records: list[tuple[str, str, str]] = []
    for path in paths:
        if not path.is_absolute():
            return {"tool": "filesystem.snapshot-modes", "status": "error", "error": "target path must be absolute"}, 2
        kind = path_type(path)
        if kind == "missing":
            return {
                "tool": "filesystem.snapshot-modes",
                "status": "error",
                "error": f"target does not exist: {path}",
            }, 2
        try:
            mode = mode_string(path)
        except OSError as exc:
            return {
                "tool": "filesystem.snapshot-modes",
                "status": "error",
                "error": f"unable to stat target: {path}: {type(exc).__name__}",
            }, 2
        records.append((mode, kind, str(path)))

    output.parent.mkdir(parents=True, exist_ok=True)
    content = HEADER + "\n" + "".join(f"{mode}\t{kind}\t{path}\n" for mode, kind, path in records)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{output.name}.tmp-",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
        temporary_name = None
    except OSError as exc:
        return {
            "tool": "filesystem.snapshot-modes",
            "status": "error",
            "error": f"unable to write snapshot: {type(exc).__name__}",
        }, 2
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass

    return {
        "tool": "filesystem.snapshot-modes",
        "operation": "snapshot",
        "entries": len(records),
        "snapshot": str(output),
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", type=parse_bool, default=False)
    parser.add_argument("--path", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload, exit_code = snapshot(Path(args.output), [Path(value) for value in args.path], args.force)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
