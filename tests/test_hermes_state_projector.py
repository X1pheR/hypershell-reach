from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECTOR = Path(__file__).parents[1] / "src/hats_mcp/hermes_state_projector.py"


def test_projector_returns_only_skill_state_and_effective_names(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "secret_value: do-not-return\n"
        "skills:\n"
        "  disabled: [global-off]\n"
        "  platform_disabled:\n"
        "    cli: [cli-off]\n"
        "  external_dirs: [/skills/external]\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    tools = repo / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "skills_tool.py").write_text(
        "import json, os\n"
        "def skills_list():\n"
        "    assert os.getenv('HERMES_PLATFORM') == 'cli'\n"
        "    return json.dumps({'success': True, 'skills': [{'name': 'active'}]})\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECTOR),
            "--config-path",
            str(config),
            "--repo-path",
            str(repo),
            "--consumer-platform",
            "cli",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["effective_names"] == ["active"]
    assert payload["disabled"] == ["cli-off", "global-off"]
    assert payload["external_dirs"] == ["/skills/external"]
    assert "secret_value" not in result.stdout
    assert "do-not-return" not in result.stdout
