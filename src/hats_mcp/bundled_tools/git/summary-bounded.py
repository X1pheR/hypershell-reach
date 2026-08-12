#!/usr/bin/env python3
# ---
# id: git.summary-bounded
# name: Bounded Git Summary
# description: Produce a bounded read-only Git work-tree summary with isolated global configuration, scoped safe.directory and optional clean-state enforcement.
# domain: git
# interpreter: python3
# requires: [git]
# mutating: false
# idempotent: true
# timeout_seconds: 120
# arguments:
#   - name: repo
#     type: string
#     required: true
#     pattern: '^/[^\r\n]+$'
#     max_length: 4096
#   - name: max_status
#     type: integer
#     required: false
#     minimum: 1
#     maximum: 500
#   - name: max_diagnostics
#     type: integer
#     required: false
#     minimum: 1
#     maximum: 200
#   - name: require_clean
#     type: boolean
#     required: false
# ---
"""Produce a bounded, read-only Git repository summary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterable


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def bounded_lines(text: str, limit: int) -> tuple[list[str], int, bool]:
    lines = [sanitize_line(line) for line in text.splitlines() if line]
    return lines[:limit], len(lines), len(lines) > limit


def sanitize_line(value: str) -> str:
    return "".join(character if character.isprintable() else " " for character in value.replace("\t", " "))


def git_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def run_git(
    repo: Path,
    environment: dict[str, str],
    *arguments: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=environment,
        timeout=timeout,
        check=False,
    )


def first_stdout(completed: subprocess.CompletedProcess[str], default: str = "") -> str:
    if completed.returncode != 0:
        return default
    return completed.stdout.strip()


def status_counts(lines: Iterable[str]) -> tuple[int, int, int, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    conflicts = 0
    conflict_states = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
    for line in lines:
        state = line[:2]
        if state == "??":
            untracked += 1
        else:
            if len(state) >= 1 and state[0] != " ":
                staged += 1
            if len(state) >= 2 and state[1] != " ":
                unstaged += 1
        if state in conflict_states:
            conflicts += 1
    return staged, unstaged, untracked, conflicts


def error_payload(message: str, diagnostics: str = "") -> dict[str, object]:
    bounded, count, truncated = bounded_lines(diagnostics, 20)
    return {
        "tool": "git.summary-bounded",
        "status": "error",
        "error": message,
        "diagnostic_count": count,
        "diagnostics_truncated": truncated,
        "diagnostics": bounded,
        "global_config_isolated": True,
    }


def summarize(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    repo = Path(args.repo)
    if not repo.is_absolute():
        return error_payload("repository path must be absolute"), 2
    if not repo.is_dir():
        return error_payload("repository directory does not exist"), 2

    with tempfile.TemporaryDirectory(prefix="hats-git-summary-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        environment = git_environment(home)

        try:
            preflight = run_git(repo, environment, "rev-parse", "--is-inside-work-tree")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return error_payload(f"git preflight failed: {type(exc).__name__}"), 2
        if preflight.returncode != 0 or preflight.stdout.strip() != "true":
            return error_payload("path is not an accessible Git work tree", preflight.stderr), 2

        try:
            status_result = run_git(repo, environment, "status", "--porcelain=v1", "--untracked-files=normal")
        except subprocess.TimeoutExpired:
            return error_payload("git status timed out"), 2
        if status_result.returncode != 0:
            return error_payload("git status failed", status_result.stderr), 2

        status_all = status_result.stdout.splitlines()
        status, status_count, status_truncated = bounded_lines(status_result.stdout, args.max_status)
        staged_count, unstaged_count, untracked_count, conflict_count = status_counts(status_all)

        branch = first_stdout(
            run_git(repo, environment, "symbolic-ref", "--quiet", "--short", "HEAD"),
            "detached",
        )
        head = first_stdout(run_git(repo, environment, "rev-parse", "--short=12", "HEAD"), "unborn")
        upstream = first_stdout(
            run_git(repo, environment, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
            "",
        )
        ahead = 0
        behind = 0
        if upstream:
            counts = run_git(repo, environment, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
            if counts.returncode == 0:
                parts = counts.stdout.split()
                if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
                    ahead, behind = int(parts[0]), int(parts[1])

        try:
            unstaged_check = run_git(repo, environment, "diff", "--check")
            staged_check = run_git(repo, environment, "diff", "--cached", "--check")
        except subprocess.TimeoutExpired:
            return error_payload("git diff --check timed out"), 2

        diagnostic_text = "\n".join(
            part.rstrip("\n")
            for part in (unstaged_check.stdout + unstaged_check.stderr, staged_check.stdout + staged_check.stderr)
            if part
        )
        diagnostics, diagnostic_count, diagnostics_truncated = bounded_lines(
            diagnostic_text,
            args.max_diagnostics,
        )
        diff_check_passed = unstaged_check.returncode == 0 and staged_check.returncode == 0

        unstaged_stat = run_git(repo, environment, "diff", "--stat", "--compact-summary")
        staged_stat = run_git(repo, environment, "diff", "--cached", "--stat", "--compact-summary")
        unstaged_stat_lines = len([line for line in unstaged_stat.stdout.splitlines() if line]) if unstaged_stat.returncode == 0 else 0
        staged_stat_lines = len([line for line in staged_stat.stdout.splitlines() if line]) if staged_stat.returncode == 0 else 0

    clean = status_count == 0
    policy_passed = diff_check_passed and (clean or not args.require_clean)
    payload: dict[str, object] = {
        "tool": "git.summary-bounded",
        "repository": str(repo),
        "branch": branch,
        "head": head,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "clean": clean,
        "require_clean": args.require_clean,
        "policy_passed": policy_passed,
        "status_count": status_count,
        "staged_count": staged_count,
        "unstaged_count": unstaged_count,
        "untracked_count": untracked_count,
        "conflict_count": conflict_count,
        "status_truncated": status_truncated,
        "status": status,
        "diff_check_passed": diff_check_passed,
        "diagnostic_count": diagnostic_count,
        "diagnostics_truncated": diagnostics_truncated,
        "diagnostics": diagnostics,
        "unstaged_stat_lines": unstaged_stat_lines,
        "staged_stat_lines": staged_stat_lines,
        "global_config_isolated": True,
    }
    return payload, 0 if policy_passed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--max-status", type=int, default=50)
    parser.add_argument("--max-diagnostics", type=int, default=20)
    parser.add_argument("--require-clean", type=parse_bool, default=False)
    return parser.parse_args()


def main() -> int:
    try:
        payload, exit_code = summarize(parse_args())
    except OSError as exc:
        payload, exit_code = error_payload(f"local filesystem failure: {type(exc).__name__}"), 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
