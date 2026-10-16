from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECTOR = Path(__file__).parents[1] / "src/hypershell_reach/hermes_state_projector.py"


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

def test_projector_supports_ruamel_without_pyyaml(tmp_path) -> None:
    # Simulate the interpreter where ruamel is available but PyYAML is absent.
    import runpy
    import sys
    from types import ModuleType
    old = {name: sys.modules.get(name) for name in ("ruamel", "ruamel.yaml", "yaml")}
    try:
        ruamel = ModuleType("ruamel")
        ruamel.__path__ = []
        child = ModuleType("ruamel.yaml")
        class FakeYAML:
            def __init__(self, typ):
                assert typ == "safe"
            def load(self, text):
                return {"skills": {"disabled": ["disabled-a"]}}
        child.YAML = FakeYAML
        ruamel.yaml = child
        sys.modules["ruamel"] = ruamel
        sys.modules["ruamel.yaml"] = child
        sys.modules["yaml"] = None
        scope = runpy.run_path(str(PROJECTOR), run_name="projector_fixture")
        assert scope["load_config_yaml"]("skills: []") == {"skills": {"disabled": ["disabled-a"]}}
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
