from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess

from hats_mcp.config import ToolSource
from hats_mcp.managed_tools import load_tool_registry, resolve_tool_source_root


BUNDLED_SOURCE = ToolSource(id="hats", type="bundled")
ROOT = Path(__file__).parents[1]
PERFORMANCE_SCRIPT = ROOT / "src/hats_mcp/bundled_tools/performance/host-preflight.py"
GIT_SCRIPT = ROOT / "src/hats_mcp/bundled_tools/git/summary-bounded.py"
SNAPSHOT_MODES_SCRIPT = ROOT / "src/hats_mcp/bundled_tools/filesystem/snapshot-modes.py"
COMPARE_MODES_SCRIPT = ROOT / "src/hats_mcp/bundled_tools/filesystem/compare-modes.py"
LITERAL_MATCH_COUNT_SCRIPT = ROOT / "src/hats_mcp/bundled_tools/filesystem/literal-match-count.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


performance = _load("performance_host_preflight", PERFORMANCE_SCRIPT)
git_summary = _load("git_summary_bounded", GIT_SCRIPT)
snapshot_modes = _load("filesystem_snapshot_modes", SNAPSHOT_MODES_SCRIPT)
compare_modes = _load("filesystem_compare_modes", COMPARE_MODES_SCRIPT)
literal_match_count = _load("filesystem_literal_match_count", LITERAL_MATCH_COUNT_SCRIPT)


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def _git_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "HATS Fixture")
    _git(repo, "config", "user.email", "fixture@invalid.example")
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def test_bundled_source_resolves_inside_package() -> None:
    root = resolve_tool_source_root(BUNDLED_SOURCE)

    assert root.name == "bundled_tools"
    assert root.is_dir()


def test_bundled_registry_contains_expected_tools() -> None:
    scripts = {script.metadata.id: script for script in load_tool_registry([BUNDLED_SOURCE]).list()}

    assert set(scripts) == {
        "filesystem.compare-modes",
        "filesystem.snapshot-modes",
        "filesystem.literal-match-count",
        "git.summary-bounded",
        "performance.host-preflight",
    }

    performance_script = scripts["performance.host-preflight"]
    assert performance_script.source_id == "hats"
    assert performance_script.relative_path == "performance/host-preflight.py"
    assert performance_script.metadata.interpreter == "python3"
    assert performance_script.metadata.mutating is False
    assert performance_script.metadata.idempotent is True
    assert performance_script.metadata.required_capabilities() == ["linux", "python3"]
    assert [argument.name for argument in performance_script.metadata.arguments] == [
        "samples",
        "interval_ms",
        "max_cpu_percent",
        "max_load_per_cpu_percent",
        "max_container_cpu_percent",
        "docker",
        "top_containers",
    ]

    snapshot_script = scripts["filesystem.snapshot-modes"]
    assert snapshot_script.metadata.mutating is True
    assert snapshot_script.metadata.idempotent is False
    assert snapshot_script.metadata.required_capabilities() == ["linux", "python3"]
    assert [argument.name for argument in snapshot_script.metadata.arguments] == [
        "output",
        "force",
        "path",
    ]
    assert snapshot_script.metadata.arguments[2].type == "string_list"
    assert snapshot_script.metadata.arguments[2].max_items == 256

    compare_script = scripts["filesystem.compare-modes"]
    assert compare_script.metadata.mutating is False
    assert compare_script.metadata.idempotent is True
    assert compare_script.metadata.required_capabilities() == ["linux", "python3"]

    match_script = scripts["filesystem.literal-match-count"]
    assert match_script.metadata.mutating is False
    assert match_script.metadata.idempotent is True
    assert match_script.metadata.required_capabilities() == ["linux", "python3"]
    assert [argument.name for argument in match_script.metadata.arguments] == [
        "file",
        "needle_file",
        "expected",
    ]

    git_script = scripts["git.summary-bounded"]
    assert git_script.source_id == "hats"
    assert git_script.relative_path == "git/summary-bounded.py"
    assert git_script.metadata.interpreter == "python3"
    assert git_script.metadata.mutating is False
    assert git_script.metadata.idempotent is True
    assert git_script.metadata.required_capabilities() == ["git", "python3"]
    assert [argument.name for argument in git_script.metadata.arguments] == [
        "repo",
        "max_status",
        "max_diagnostics",
        "require_clean",
    ]


def test_performance_evaluation_reports_expected_busy_reason() -> None:
    reasons = performance.evaluate(
        cpu_samples=[12.0, 18.0],
        load_per_cpu=0.2,
        docker={"available": True, "top": [{"name": "qmd", "cpu_percent": 347.25}]},
        max_cpu_percent=50.0,
        max_load_per_cpu=0.75,
        max_container_cpu_percent=25.0,
    )

    assert reasons == ["container-cpu-busy"]


def test_cpu_calculation_and_percent_parsing_match_legacy_contract() -> None:
    before = performance.parse_proc_stat("cpu  100 10 20 870 0 0 0 0 0 0")
    after = performance.parse_proc_stat("cpu  140 10 30 920 0 0 0 0 0 0")

    assert round(performance.cpu_busy_percent(before, after), 2) == 50.0
    assert performance.parse_percent("347.25%") == 347.25


def test_git_summary_clean_fixture_isolated_from_broken_global_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _git_fixture(tmp_path)
    broken_home = tmp_path / "broken-home"
    broken_home.mkdir()
    broken_config = broken_home / ".gitconfig"
    broken_config.write_text("[broken\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(broken_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(broken_config))

    payload, exit_code = git_summary.summarize(
        argparse.Namespace(
            repo=str(repo),
            max_status=50,
            max_diagnostics=20,
            require_clean=True,
        )
    )

    assert exit_code == 0
    assert payload["clean"] is True
    assert payload["policy_passed"] is True
    assert payload["diff_check_passed"] is True
    assert payload["global_config_isolated"] is True
    assert payload["status_count"] == 0


def test_git_summary_dirty_fixture_is_bounded_and_fails_policy(tmp_path: Path) -> None:
    repo = _git_fixture(tmp_path)
    with (repo / "tracked.txt").open("a", encoding="utf-8") as handle:
        handle.write("trailing whitespace   \n")
    for index in range(12):
        (repo / f"untracked-{index}.txt").write_text("fixture\n", encoding="utf-8")

    payload, exit_code = git_summary.summarize(
        argparse.Namespace(
            repo=str(repo),
            max_status=5,
            max_diagnostics=5,
            require_clean=True,
        )
    )

    assert exit_code == 1
    assert payload["clean"] is False
    assert payload["policy_passed"] is False
    assert payload["status_truncated"] is True
    assert len(payload["status"]) == 5
    assert payload["diff_check_passed"] is False
    assert payload["diagnostic_count"] >= 1
    assert len(payload["diagnostics"]) <= 5
    assert payload["untracked_count"] == 12


def test_mode_snapshot_and_compare_detect_regression(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.sh"
    contract = tmp_path / "modes.tsv"
    fixture.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fixture.chmod(0o755)

    snapshot_payload, snapshot_exit = snapshot_modes.snapshot(contract, [fixture], False)
    assert snapshot_exit == 0
    assert snapshot_payload["entries"] == 1
    assert contract.read_text(encoding="utf-8") == (
        f"# agent-tooling-file-modes-v1\n755\tfile\t{fixture}\n"
    )

    fixture.chmod(0o644)
    mismatch_payload, mismatch_exit = compare_modes.compare(contract)
    assert mismatch_exit == 1
    assert mismatch_payload["checked"] == 1
    assert mismatch_payload["mismatches"] == 1
    assert mismatch_payload["details"] == [
        {
            "path": str(fixture),
            "expected_mode": "755",
            "actual_mode": "644",
            "expected_type": "file",
            "actual_type": "file",
        }
    ]

    fixture.chmod(0o755)
    match_payload, match_exit = compare_modes.compare(contract)
    assert match_exit == 0
    assert match_payload["mismatches"] == 0
    assert match_payload["details"] == []


def test_literal_match_count_matches_legacy_contract(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    needle = tmp_path / "needle.txt"
    target.write_bytes(b"service:\n  restart: unless-stopped\nother:\n  restart: unless-stopped\n")
    needle.write_bytes(b"service:\n  restart: unless-stopped\n")

    payload, exit_code = literal_match_count.count_matches(target, needle, 1)
    assert exit_code == 0
    assert payload["actual"] == 1
    assert payload["status"] == "passed"

    payload, exit_code = literal_match_count.count_matches(target, needle, 2)
    assert exit_code == 1
    assert payload["actual"] == 1
    assert payload["status"] == "failed"


def test_literal_match_count_rejects_empty_needle(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    needle = tmp_path / "needle.txt"
    target.write_text("fixture\n", encoding="utf-8")
    needle.write_bytes(b"")

    payload, exit_code = literal_match_count.count_matches(target, needle, 1)
    assert exit_code == 2
    assert payload["status"] == "error"


def test_mode_compare_accepts_legacy_v1_snapshot(tmp_path: Path) -> None:
    fixture = tmp_path / "dir"
    fixture.mkdir()
    fixture.chmod(0o750)
    contract = tmp_path / "legacy.tsv"
    contract.write_text(
        f"# agent-tooling-file-modes-v1\n750\tdirectory\t{fixture}\n",
        encoding="utf-8",
    )

    payload, exit_code = compare_modes.compare(contract)

    assert exit_code == 0
    assert payload["checked"] == 1
    assert payload["mismatches"] == 0


def test_git_summary_rejects_non_repository(tmp_path: Path) -> None:
    payload, exit_code = git_summary.summarize(
        argparse.Namespace(
            repo=str(tmp_path),
            max_status=50,
            max_diagnostics=20,
            require_clean=False,
        )
    )

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["global_config_isolated"] is True
