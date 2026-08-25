#!/usr/bin/env python3
"""Emit a narrow, secret-free Hermes skill-state projection as JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml


def _normalize_names(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def project_state(
    *,
    config_path: Path,
    repo_path: Path,
    consumer_platform: str | None,
) -> dict[str, object]:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    skills = data.get("skills") if isinstance(data, dict) else {}
    if not isinstance(skills, dict):
        skills = {}

    global_disabled = _normalize_names(skills.get("disabled"))
    platform_map = skills.get("platform_disabled") or {}
    if not isinstance(platform_map, dict):
        platform_map = {}
    platform_disabled = _normalize_names(
        platform_map.get(consumer_platform) if consumer_platform else None
    )
    external_dirs = skills.get("external_dirs") or []
    if isinstance(external_dirs, str):
        external_dirs = [external_dirs]
    if not isinstance(external_dirs, list):
        external_dirs = []
    external_dirs = [str(value).strip() for value in external_dirs if str(value).strip()]

    if consumer_platform:
        os.environ["HERMES_PLATFORM"] = consumer_platform
    else:
        os.environ.pop("HERMES_PLATFORM", None)

    repo_text = str(repo_path)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    os.chdir(repo_path)

    from tools.skills_tool import skills_list

    payload = json.loads(skills_list())
    if not payload.get("success"):
        raise RuntimeError(payload.get("error") or "Hermes skills_list failed")
    effective_names = sorted(
        {
            str(item.get("name", "")).strip()
            for item in payload.get("skills", [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
    )

    return {
        "schema_version": 1,
        "consumer_platform": consumer_platform,
        "global_disabled": global_disabled,
        "platform_disabled": platform_disabled,
        "disabled": sorted(set(global_disabled) | set(platform_disabled)),
        "external_dirs": external_dirs,
        "effective_names": effective_names,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--consumer-platform")
    args = parser.parse_args()

    result = project_state(
        config_path=Path(args.config_path),
        repo_path=Path(args.repo_path),
        consumer_platform=args.consumer_platform,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
