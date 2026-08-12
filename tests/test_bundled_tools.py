from __future__ import annotations

import importlib.util
from pathlib import Path

from hats_mcp.config import ToolSource
from hats_mcp.managed_tools import load_tool_registry, resolve_tool_source_root


BUNDLED_SOURCE = ToolSource(id="hats", type="bundled")
SCRIPT = (
    Path(__file__).parents[1]
    / "src/hats_mcp/bundled_tools/performance/host-preflight.py"
)
spec = importlib.util.spec_from_file_location("performance_host_preflight", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_bundled_source_resolves_inside_package() -> None:
    root = resolve_tool_source_root(BUNDLED_SOURCE)

    assert root.name == "bundled_tools"
    assert root.is_dir()


def test_bundled_registry_contains_performance_preflight() -> None:
    script = load_tool_registry([BUNDLED_SOURCE]).get("performance.host-preflight")

    assert script.source_id == "hats"
    assert script.relative_path == "performance/host-preflight.py"
    assert script.metadata.interpreter == "python3"
    assert script.metadata.mutating is False
    assert script.metadata.idempotent is True
    assert script.metadata.required_capabilities() == ["linux", "python3"]
    assert [argument.name for argument in script.metadata.arguments] == [
        "samples",
        "interval_ms",
        "max_cpu_percent",
        "max_load_per_cpu_percent",
        "max_container_cpu_percent",
        "docker",
        "top_containers",
    ]


def test_performance_evaluation_reports_expected_busy_reason() -> None:
    reasons = module.evaluate(
        cpu_samples=[12.0, 18.0],
        load_per_cpu=0.2,
        docker={"available": True, "top": [{"name": "qmd", "cpu_percent": 347.25}]},
        max_cpu_percent=50.0,
        max_load_per_cpu=0.75,
        max_container_cpu_percent=25.0,
    )

    assert reasons == ["container-cpu-busy"]


def test_cpu_calculation_and_percent_parsing_match_legacy_contract() -> None:
    before = module.parse_proc_stat("cpu  100 10 20 870 0 0 0 0 0 0")
    after = module.parse_proc_stat("cpu  140 10 30 920 0 0 0 0 0 0")

    assert round(module.cpu_busy_percent(before, after), 2) == 50.0
    assert module.parse_percent("347.25%") == 347.25
