from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from .config import SkillSource

EXCLUDED_SKILL_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".hub",
        ".archive",
        ".venv",
        "venv",
        "node_modules",
        "site-packages",
        "__pycache__",
        ".tox",
        ".nox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
SKILL_SUPPORT_DIRS = frozenset({"references", "templates", "assets", "scripts"})
KNOWN_ENVIRONMENTS = frozenset({"kanban", "docker", "s6"})
PLATFORM_MAP = {"macos": "darwin", "linux": "linux", "windows": "win32"}
MAX_SKILL_FILE_BYTES = 1_048_576
MAX_SUPPORT_FILE_BYTES = 16_777_216
MAX_READ_BYTES = 131_072
MAX_DESCRIPTION_LENGTH = 1024
MAX_CATALOG_DESCRIPTION_LENGTH = 200
MAX_FRESHNESS_FILES = 20_000
MAX_FRESHNESS_BYTES = 536_870_912
MAX_FRESHNESS_PROBE_PATHS = 50_000
_FRESHNESS_CHUNK_BYTES = 1_048_576
_HERMES_FRESHNESS_METADATA = (".bundled_manifest", ".hub/lock.json", "_org/.active_org")
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class HermesState:
    effective_names: frozenset[str]
    disabled: frozenset[str]
    external_dirs: tuple[str, ...]
    consumer_platform: str | None
    stderr_bytes: int = 0


@dataclass(frozen=True)
class SkillSourceProbeEntry:
    path: str
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SkillSourcesSnapshot:
    fingerprint: str
    probe_entries: tuple[SkillSourceProbeEntry, ...]


@dataclass(frozen=True)
class SkillPackage:
    source_id: str
    source_type: str
    root: Path
    skill_dir: Path
    relative_dir: str
    name: str
    description: str
    category: str | None
    frontmatter: dict[str, Any]
    provenance: str
    provenance_details: dict[str, Any]
    effective: bool
    compatible: bool
    environment_relevant: bool
    enabled: bool
    bytes: int
    sha256: str

    @property
    def id(self) -> str:
        return f"{self.source_id}:{self.name}"

    def catalog_summary(self) -> dict[str, object]:
        description = self.description
        if len(description) > MAX_CATALOG_DESCRIPTION_LENGTH:
            description = description[: MAX_CATALOG_DESCRIPTION_LENGTH - 3] + "..."
        return {
            "id": self.id,
            "name": self.name,
            "description": description,
            "category": self.category,
        }

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "source": self.source_id,
            "source_type": self.source_type,
            "provenance": self.provenance,
            "effective": self.effective,
            "enabled": self.enabled,
            "compatible": self.compatible,
            "environment_relevant": self.environment_relevant,
        }


class SkillRegistry:
    def __init__(
        self,
        skills: dict[str, SkillPackage],
        source_reports: list[dict[str, object]],
    ) -> None:
        self._skills = skills
        self.source_reports = source_reports

    def list(self, *, include_inactive: bool = False) -> list[SkillPackage]:
        skills = list(self._skills.values())
        if not include_inactive:
            skills = [skill for skill in skills if skill.effective]
        return sorted(skills, key=lambda skill: (skill.source_id, skill.category or "", skill.name))

    def get(self, skill_id: str, *, require_effective: bool = True) -> SkillPackage:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise ValueError(f"unknown skill: {skill_id}")
        if require_effective and not skill.effective:
            raise ValueError(f"skill is not active: {skill_id}")
        return skill

    def catalog_revision(self) -> str:
        payload = [skill.catalog_summary() for skill in self.list()]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if content.startswith("\ufeff"):
        content = content[1:]
    if not content.startswith("---"):
        return {}, content
    marker = re.search(r"\n---\s*\n", content[3:])
    if marker is None:
        return {}, content
    yaml_content = content[3 : marker.start() + 3]
    body = content[marker.end() + 3 :]
    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid skill frontmatter YAML: {exc}") from exc
    return (parsed if isinstance(parsed, dict) else {}), body


def _normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _platform_matches(frontmatter: dict[str, Any], configured_platform: str | None) -> bool:
    platforms = _normalize_list(frontmatter.get("platforms"))
    if not platforms or configured_platform is None:
        return True
    current = PLATFORM_MAP.get(configured_platform, configured_platform)
    for platform in platforms:
        normalized = PLATFORM_MAP.get(platform.lower(), platform.lower())
        if current.startswith(normalized) or normalized.startswith(current):
            return True
    return False


def _environment_matches(frontmatter: dict[str, Any], active: list[str]) -> bool:
    environments = [value.lower() for value in _normalize_list(frontmatter.get("environments"))]
    if not environments:
        return True
    active_set = set(active)
    for environment in environments:
        if environment not in KNOWN_ENVIRONMENTS:
            return True
        if environment in active_set:
            return True
    return False


def _read_bundled_names(root: Path) -> set[str]:
    manifest = root / ".bundled_manifest"
    if not manifest.is_file() or manifest.is_symlink():
        return set()
    names: set[str] = set()
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        name, separator, _ = line.partition(":")
        if separator and name.strip():
            names.add(name.strip())
    return names


def _read_hub_metadata(root: Path) -> dict[str, dict[str, Any]]:
    lock = root / ".hub" / "lock.json"
    if not lock.is_file() or lock.is_symlink():
        return {}
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    installed = payload.get("installed") if isinstance(payload, dict) else None
    return installed if isinstance(installed, dict) else {}


def _read_active_org(root: Path) -> str | None:
    marker = root / "_org" / ".active_org"
    if not marker.is_file() or marker.is_symlink():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _iter_skill_files(
    root: Path,
    *,
    hermes: bool,
    probe_entries: dict[Path, SkillSourceProbeEntry] | None = None,
) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"skill source root must not be a symlink: {root}")
    active_org = _read_active_org(root) if hermes else None
    org_root = root / "_org"
    matches: list[Path] = []

    for current_text, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(current_text)
        if probe_entries is not None:
            probe_entries.setdefault(current, _probe_entry(current))
        has_skill_md = "SKILL.md" in filenames
        if current == root and hermes and "_org" in dirnames and active_org is None:
            dirnames.remove("_org")
        elif hermes and current == org_root:
            dirnames[:] = [name for name in dirnames if name == active_org]

        kept: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            if dirname in EXCLUDED_SKILL_DIRS:
                continue
            if has_skill_md and dirname in SKILL_SUPPORT_DIRS:
                continue
            if child.is_symlink():
                raise ValueError(f"symlinked skill directories are not supported: {child}")
            kept.append(dirname)
        dirnames[:] = kept

        if "SKILL.md" in filenames:
            candidate = current / "SKILL.md"
            if candidate.is_symlink():
                raise ValueError(f"symlinked SKILL.md is not supported: {candidate}")
            matches.append(candidate)

    return sorted(matches)


def _validated_source_root(source: SkillSource) -> Path:
    root = Path(source.path)
    if not root.exists():
        raise ValueError(f"skill source does not exist: {source.id}")
    if not root.is_dir():
        raise ValueError(f"skill source is not a directory: {source.id}")
    if root.is_symlink():
        raise ValueError(f"skill source root must not be a symlink: {source.id}")
    return root.resolve()


def skill_sources_config_signature(sources: list[SkillSource]) -> str:
    """Return a deterministic signature for configured skill-source definitions."""

    payload = [source.model_dump(mode="json") for source in sources]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iter_package_files(
    root: Path,
    skill_dirs: list[Path],
    *,
    probe_entries: dict[Path, SkillSourceProbeEntry] | None = None,
) -> list[Path]:
    files: dict[str, Path] = {}
    for skill_dir in skill_dirs:
        for current_text, dirnames, filenames in os.walk(skill_dir, followlinks=False):
            current = Path(current_text)
            if probe_entries is not None:
                probe_entries.setdefault(current, _probe_entry(current))
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if dirname not in EXCLUDED_SKILL_DIRS and not (current / dirname).is_symlink()
            )
            for filename in sorted(filenames):
                path = current / filename
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                files.setdefault(relative, path)
    return [files[relative] for relative in sorted(files)]


def _probe_entry(path: Path) -> SkillSourceProbeEntry:
    stat_result = path.stat(follow_symlinks=False)
    return SkillSourceProbeEntry(
        path=str(path),
        mode=stat_result.st_mode,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        ctime_ns=stat_result.st_ctime_ns,
    )


def _hash_regular_file(path: Path, before: SkillSourceProbeEntry) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_FRESHNESS_CHUNK_BYTES):
            digest.update(chunk)
    after = _probe_entry(path)
    if after != before:
        raise RuntimeError(f"skill source file changed during freshness probe: {path}")
    return digest.hexdigest()


def skill_sources_snapshot(sources: list[SkillSource]) -> SkillSourcesSnapshot:
    """Build deterministic content freshness plus a cheap process-local change probe.

    The fingerprint covers every regular file inside discoverable skill packages plus
    Hermes metadata that changes discovery or provenance. It excludes timestamps,
    ownership, inode numbers and modes. The probe entries retain filesystem metadata
    only to avoid rehashing unchanged content on every cache access; any probe change
    causes a fresh deterministic content snapshot before registry reuse is decided.
    """

    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    observed_probe_entries: dict[Path, SkillSourceProbeEntry] = {}
    for source in sources:
        if not source.enabled:
            continue
        root = _validated_source_root(source)
        source_probe_entries: dict[Path, SkillSourceProbeEntry] = {
            root: _probe_entry(root)
        }
        skill_files = _iter_skill_files(
            root,
            hermes=source.type == "hermes",
            probe_entries=source_probe_entries,
        )
        paths = _iter_package_files(
            root,
            [path.parent for path in skill_files],
            probe_entries=source_probe_entries,
        )
        if source.type == "hermes":
            for relative in (".hub", "_org"):
                directory = root / relative
                if directory.is_dir() and not directory.is_symlink():
                    source_probe_entries.setdefault(directory, _probe_entry(directory))
            by_relative = {path.relative_to(root).as_posix(): path for path in paths}
            for relative in _HERMES_FRESHNESS_METADATA:
                path = root.joinpath(*PurePosixPath(relative).parts)
                if path.is_symlink() or not path.is_file():
                    continue
                by_relative.setdefault(relative, path)
            paths = [by_relative[relative] for relative in sorted(by_relative)]

        source_header = {"id": source.id, "type": source.type}
        digest.update(
            json.dumps(source_header, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        for path in paths:
            file_count += 1
            if file_count > MAX_FRESHNESS_FILES:
                raise RuntimeError(
                    f"skill freshness probe exceeds {MAX_FRESHNESS_FILES} files"
                )
            before = _probe_entry(path)
            source_probe_entries.setdefault(path, before)
            byte_count += before.size
            if byte_count > MAX_FRESHNESS_BYTES:
                raise RuntimeError(
                    f"skill freshness probe exceeds {MAX_FRESHNESS_BYTES} bytes"
                )
            sha256 = _hash_regular_file(path, before)
            entry = {
                "path": path.relative_to(root).as_posix(),
                "bytes": before.size,
                "sha256": sha256,
            }
            digest.update(
                json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        for path, entry in source_probe_entries.items():
            observed_probe_entries.setdefault(path, entry)

    if len(observed_probe_entries) > MAX_FRESHNESS_PROBE_PATHS:
        raise RuntimeError(
            f"skill freshness probe exceeds {MAX_FRESHNESS_PROBE_PATHS} tracked paths"
        )
    probe_entries: list[SkillSourceProbeEntry] = []
    for path in sorted(observed_probe_entries):
        observed = observed_probe_entries[path]
        current = _probe_entry(path)
        if current != observed:
            raise RuntimeError(f"skill source changed during freshness probe: {path}")
        probe_entries.append(current)
    return SkillSourcesSnapshot(
        fingerprint=digest.hexdigest(),
        probe_entries=tuple(probe_entries),
    )


def skill_sources_probe_unchanged(snapshot: SkillSourcesSnapshot) -> bool:
    """Return whether all paths that can affect the cached snapshot remain unchanged."""

    try:
        return all(_probe_entry(Path(entry.path)) == entry for entry in snapshot.probe_entries)
    except OSError:
        return False


def skill_sources_fingerprint(sources: list[SkillSource]) -> str:
    """Return the deterministic content fingerprint for configured enabled sources."""

    return skill_sources_snapshot(sources).fingerprint


def _category(root: Path, skill_dir: Path) -> str | None:
    relative = skill_dir.relative_to(root)
    parents = relative.parts[:-1]
    return "/".join(parents) if parents else None


def _provenance(
    *,
    source: SkillSource,
    root: Path,
    skill_dir: Path,
    name: str,
    bundled: set[str],
    hub: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    if source.type != "hermes":
        return "filesystem", {}
    if name in hub:
        metadata = hub[name]
        details = {
            key: metadata.get(key)
            for key in ("source", "identifier", "trust_level", "scan_verdict")
            if metadata.get(key) is not None
        }
        return "hub", details
    try:
        relative = skill_dir.relative_to(root)
    except ValueError:
        relative = Path()
    if relative.parts and relative.parts[0] == "_org":
        return "org", {"org_id": relative.parts[1] if len(relative.parts) > 1 else None}
    if name in bundled:
        return "bundled", {}
    return "local", {}


def _cap_description(value: str) -> str:
    if len(value) <= MAX_DESCRIPTION_LENGTH:
        return value
    return value[: MAX_DESCRIPTION_LENGTH - 3] + "..."


def _description(frontmatter: dict[str, Any], body: str) -> str:
    raw = frontmatter.get("description")
    if raw is not None and str(raw).strip():
        return _cap_description(str(raw).strip())
    for line in body.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            return _cap_description(value)
    return ""


def _scan_source(
    source: SkillSource,
    state: HermesState | None,
    *,
    allow_unresolved_hermes: bool = False,
) -> tuple[list[SkillPackage], dict[str, object]]:
    root = _validated_source_root(source)

    bundled = _read_bundled_names(root) if source.type == "hermes" else set()
    hub = _read_hub_metadata(root) if source.type == "hermes" else {}
    packages: list[SkillPackage] = []
    names: dict[str, str] = {}

    for skill_md in _iter_skill_files(root, hermes=source.type == "hermes"):
        size = skill_md.stat().st_size
        if size > MAX_SKILL_FILE_BYTES:
            raise ValueError(f"SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes: {skill_md}")
        content = skill_md.read_text(encoding="utf-8-sig", errors="replace")
        frontmatter, body = _parse_frontmatter(content)
        raw_name = frontmatter.get("name")
        name = str(raw_name).strip() if raw_name is not None else skill_md.parent.name
        if not _SKILL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid skill name in {skill_md}: {name!r}")
        if name in names:
            raise ValueError(
                f"duplicate skill name in source {source.id}: {name} -> {names[name]}, "
                f"{skill_md.relative_to(root).as_posix()}"
            )
        names[name] = skill_md.relative_to(root).as_posix()

        description = _description(frontmatter, body)
        if not description:
            raise ValueError(f"skill has no description: {skill_md}")

        compatible = _platform_matches(frontmatter, source.os_platform)
        environment_relevant = _environment_matches(frontmatter, source.active_environments)
        if source.type == "hermes":
            if state is None:
                if not allow_unresolved_hermes:
                    raise RuntimeError(f"Hermes state is unavailable for source: {source.id}")
                enabled = True
                effective = False
            else:
                enabled = name not in state.disabled
                effective = name in state.effective_names
        else:
            enabled = True
            effective = compatible and environment_relevant

        provenance, provenance_details = _provenance(
            source=source,
            root=root,
            skill_dir=skill_md.parent,
            name=name,
            bundled=bundled,
            hub=hub,
        )
        packages.append(
            SkillPackage(
                source_id=source.id,
                source_type=source.type,
                root=root,
                skill_dir=skill_md.parent,
                relative_dir=skill_md.parent.relative_to(root).as_posix(),
                name=name,
                description=description,
                category=_category(root, skill_md.parent),
                frontmatter=frontmatter,
                provenance=provenance,
                provenance_details=provenance_details,
                effective=effective,
                compatible=compatible,
                environment_relevant=environment_relevant,
                enabled=enabled,
                bytes=size,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )

    physical_names = {package.name for package in packages}
    report: dict[str, object] = {
        "id": source.id,
        "type": source.type,
        "physical_count": len(packages),
        "effective_count": (
            None
            if source.type == "hermes" and state is None
            else sum(1 for package in packages if package.effective)
        ),
        "state_checked": source.type != "hermes" or state is not None,
        "exact_content_parity": source.type != "hermes" or state is not None,
    }
    if source.type == "hermes" and state is not None:
        missing_content = sorted(state.effective_names - physical_names)
        if missing_content:
            report["exact_content_parity"] = False
            report["missing_effective_content"] = missing_content
            raise RuntimeError(
                "Hermes effective catalog contains skills outside the configured content source: "
                + ", ".join(missing_content)
            )
        report.update(
            {
                "consumer_platform": state.consumer_platform,
                "disabled_count": len(state.disabled),
                "external_dir_count": len(state.external_dirs),
                "state_stderr_bytes": state.stderr_bytes,
            }
        )
    return packages, report


def inspect_skill_source_summary(source: SkillSource) -> dict[str, object]:
    """Return cheap local content availability/count without parsing package metadata."""

    root = _validated_source_root(source)
    return {
        "id": source.id,
        "type": source.type,
        "available": True,
        "state": "content-only" if source.type == "hermes" else "configured",
        "count": len(_iter_skill_files(root, hermes=source.type == "hermes")),
    }


def inspect_skill_source(source: SkillSource) -> tuple[list[SkillPackage], dict[str, object]]:
    """Validate one local skill content source without making network calls.

    Hermes sources are scanned for readable local packages only. Their effective
    enable/disable state remains unresolved until the normal runtime projection runs.
    """

    return _scan_source(source, None, allow_unresolved_hermes=True)


def build_skill_registry(
    sources: list[SkillSource],
    hermes_states: dict[str, HermesState] | None = None,
) -> SkillRegistry:
    states = hermes_states or {}
    skills: dict[str, SkillPackage] = {}
    reports: list[dict[str, object]] = []
    for source in sources:
        if not source.enabled:
            continue
        packages, report = _scan_source(source, states.get(source.id))
        reports.append(report)
        for package in packages:
            if package.id in skills:
                raise ValueError(f"duplicate qualified skill ID: {package.id}")
            skills[package.id] = package
    return SkillRegistry(skills, reports)


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise ValueError("skill file path must be a non-empty POSIX relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("skill file path must be relative")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError("skill file path contains an invalid path component")
    return posix


def _resolve_file(skill: SkillPackage, relative_path: str) -> Path:
    relative = _safe_relative_path(relative_path)
    path = skill.skill_dir.joinpath(*relative.parts)
    if path.is_symlink():
        raise ValueError("symlinked skill files are not readable")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(skill.skill_dir.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("skill file is outside the skill package or does not exist") from exc
    if not resolved.is_file():
        raise ValueError("skill file is not a regular file")
    if resolved.stat().st_size > MAX_SUPPORT_FILE_BYTES:
        raise ValueError(f"skill file exceeds {MAX_SUPPORT_FILE_BYTES} bytes")
    return resolved


def list_skill_files(skill: SkillPackage) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for current_text, dirnames, filenames in os.walk(skill.skill_dir, followlinks=False):
        current = Path(current_text)
        kept: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            if child.is_symlink() or dirname in EXCLUDED_SKILL_DIRS:
                continue
            kept.append(dirname)
        dirnames[:] = kept
        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(skill.skill_dir).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size})
    return sorted(files, key=lambda item: str(item["path"]))


def read_skill_file(
    skill: SkillPackage,
    relative_path: str,
    *,
    offset: int = 0,
    max_bytes: int = 65_536,
) -> dict[str, object]:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if max_bytes < 1 or max_bytes > MAX_READ_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_READ_BYTES}")
    path = _resolve_file(skill, relative_path)
    total = path.stat().st_size
    if offset > total:
        raise ValueError("offset exceeds file size")
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(max_bytes)
    try:
        content = chunk.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        content = None
        binary = True
    next_offset = offset + len(chunk)
    return {
        "skill_id": skill.id,
        "path": relative_path,
        "offset": offset,
        "returned_bytes": len(chunk),
        "total_bytes": total,
        "truncated": next_offset < total,
        "next_offset": next_offset if next_offset < total else None,
        "binary": binary,
        "content": content,
    }
