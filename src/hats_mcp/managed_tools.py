from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import ToolSource

_SCRIPT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\.[a-z0-9][a-z0-9._-]{0,126}$")
_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ARGUMENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_SCRIPT_BYTES = 262_144
_MAX_FRONTMATTER_BYTES = 16_384
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
_INTERPRETER_CAPABILITY = {"sh": "sh", "bash": "bash", "python3": "python3"}
_BUNDLED_TOOLS_ROOT = Path(__file__).resolve().parent / "bundled_tools"


class ArgumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    type: Literal["string", "integer", "boolean"]
    required: bool = True
    description: str | None = Field(default=None, max_length=300)
    enum: list[str] | None = None
    pattern: str | None = Field(default=None, max_length=256)
    min_length: int | None = Field(default=None, ge=0, le=32_768)
    max_length: int | None = Field(default=None, ge=1, le=32_768)
    minimum: int | None = None
    maximum: int | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _ARGUMENT_NAME.fullmatch(value):
            raise ValueError("invalid argument name")
        return value

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid argument pattern: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_type_options(self) -> "ArgumentSpec":
        if self.type != "string" and any(
            value is not None for value in (self.enum, self.pattern, self.min_length, self.max_length)
        ):
            raise ValueError("enum, pattern and length bounds require type=string")
        if self.type != "integer" and any(
            value is not None for value in (self.minimum, self.maximum)
        ):
            raise ValueError("minimum and maximum require type=integer")
        if self.enum is not None:
            if not self.enum:
                raise ValueError("enum must not be empty")
            if len(set(self.enum)) != len(self.enum):
                raise ValueError("enum values must be unique")
        if self.min_length is not None and self.max_length is not None:
            if self.min_length > self.max_length:
                raise ValueError("min_length must not exceed max_length")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum must not exceed maximum")
        return self


class ScriptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=190)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=63)
    interpreter: Literal["sh", "bash", "python3"]
    requires: list[str] = Field(default_factory=list)
    mutating: bool
    idempotent: bool
    timeout_seconds: int = Field(default=90, ge=1, le=900)
    arguments: list[ArgumentSpec] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SCRIPT_ID.fullmatch(value):
            raise ValueError("invalid script ID")
        return value

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if not _DOMAIN.fullmatch(value):
            raise ValueError("invalid domain")
        return value

    @field_validator("requires")
    @classmethod
    def validate_requires(cls, values: list[str]) -> list[str]:
        normalized = sorted(set(values))
        invalid = [value for value in normalized if not _CAPABILITY.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid required capabilities: {', '.join(invalid)}")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> "ScriptMetadata":
        if not self.id.startswith(f"{self.domain}."):
            raise ValueError("script ID must start with '<domain>.'")
        names = [argument.name for argument in self.arguments]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate argument names: {', '.join(duplicates)}")
        return self

    def required_capabilities(self) -> list[str]:
        return sorted(set(self.requires) | {_INTERPRETER_CAPABILITY[self.interpreter]})


@dataclass(frozen=True)
class ManagedScript:
    source_id: str
    relative_path: str
    path: Path
    metadata: ScriptMetadata
    content: str
    sha256: str
    bytes: int

    def summary(self) -> dict[str, object]:
        return {
            "id": self.metadata.id,
            "name": self.metadata.name,
            "description": self.metadata.description,
            "source": self.source_id,
            "domain": self.metadata.domain,
            "interpreter": self.metadata.interpreter,
            "requires": self.metadata.required_capabilities(),
            "mutating": self.metadata.mutating,
            "idempotent": self.metadata.idempotent,
        }

    def detail(self) -> dict[str, object]:
        return {
            **self.summary(),
            "relative_path": self.relative_path,
            "timeout_seconds": self.metadata.timeout_seconds,
            "arguments": [argument.model_dump(exclude_none=True) for argument in self.metadata.arguments],
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


class ToolRegistry:
    def __init__(self, scripts: dict[str, ManagedScript]):
        self._scripts = scripts

    def list(self) -> list[ManagedScript]:
        return [self._scripts[script_id] for script_id in sorted(self._scripts)]

    def get(self, script_id: str) -> ManagedScript:
        script = self._scripts.get(script_id)
        if script is None:
            raise ValueError(f"unknown script: {script_id}")
        return script


def _extract_frontmatter(content: str, path: Path) -> dict[str, Any] | None:
    lines = content.splitlines()
    index = 1 if lines and lines[0].startswith("#!") else 0
    if index >= len(lines) or lines[index].strip() != "# ---":
        return None

    frontmatter: list[str] = []
    frontmatter_bytes = 0
    for line in lines[index + 1 :]:
        if line.strip() == "# ---":
            try:
                payload = yaml.safe_load("\n".join(frontmatter))
            except yaml.YAMLError as exc:
                raise ValueError(f"invalid frontmatter YAML in {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"frontmatter must be a mapping in {path}")
            return payload
        if not line.startswith("#"):
            raise ValueError(f"frontmatter lines must be comments in {path}")
        value = line[1:]
        if value.startswith(" "):
            value = value[1:]
        frontmatter.append(value)
        frontmatter_bytes += len(line.encode("utf-8")) + 1
        if frontmatter_bytes > _MAX_FRONTMATTER_BYTES:
            raise ValueError(f"frontmatter exceeds {_MAX_FRONTMATTER_BYTES} bytes in {path}")

    raise ValueError(f"frontmatter is not terminated in {path}")


def _load_script(path: Path, source_id: str, root: Path) -> ManagedScript | None:
    if path.is_symlink():
        raise ValueError(f"managed tool must not be a symlink: {path}")
    if not path.is_file():
        raise ValueError(f"managed tool must be a regular file: {path}")
    size = path.stat().st_size
    if size > _MAX_SCRIPT_BYTES:
        raise ValueError(f"managed tool exceeds {_MAX_SCRIPT_BYTES} bytes: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"managed tool is not valid UTF-8: {path}") from exc

    payload = _extract_frontmatter(content, path)
    if payload is None:
        return None
    try:
        metadata = ScriptMetadata.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid managed tool metadata in {path}: {exc}") from exc

    allowed_interpreters = {".sh": {"sh", "bash"}, ".py": {"python3"}}
    allowed = allowed_interpreters.get(path.suffix)
    if allowed is None or metadata.interpreter not in allowed:
        raise ValueError(
            f"interpreter {metadata.interpreter} does not match supported suffix {path.suffix}: {path}"
        )

    relative_path = path.relative_to(root).as_posix()
    return ManagedScript(
        source_id=source_id,
        relative_path=relative_path,
        path=path,
        metadata=metadata,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        bytes=size,
    )


def resolve_tool_source_root(source: ToolSource) -> Path:
    if source.type == "bundled":
        return _BUNDLED_TOOLS_ROOT
    assert source.path is not None
    return Path(source.path)


def load_tool_registry(sources: list[ToolSource]) -> ToolRegistry:
    scripts: dict[str, ManagedScript] = {}
    owners: dict[str, list[str]] = {}

    for source in sources:
        if not source.enabled:
            continue
        root = resolve_tool_source_root(source)
        if not root.exists():
            raise ValueError(f"tool source does not exist: {source.id}")
        if not root.is_dir():
            raise ValueError(f"tool source is not a directory: {source.id}")
        root = root.resolve()

        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(current_root)
            for dirname in list(dirnames):
                child = current / dirname
                if dirname in _SKIP_DIRS or dirname.startswith("."):
                    dirnames.remove(dirname)
                elif child.is_symlink():
                    raise ValueError(f"managed tool source contains a symlink directory: {child}")

            for filename in filenames:
                path = current / filename
                if path.suffix not in {".sh", ".py"}:
                    continue
                script = _load_script(path, source.id, root)
                if script is None:
                    continue
                owners.setdefault(script.metadata.id, []).append(
                    f"{source.id}:{script.relative_path}"
                )
                scripts.setdefault(script.metadata.id, script)

    duplicates = {script_id: paths for script_id, paths in owners.items() if len(paths) > 1}
    if duplicates:
        details = "; ".join(
            f"{script_id} -> {', '.join(paths)}" for script_id, paths in sorted(duplicates.items())
        )
        raise ValueError(f"duplicate managed tool IDs: {details}")

    return ToolRegistry(scripts)


def validate_script_arguments(script: ManagedScript, values: dict[str, Any]) -> list[str]:
    specs = {argument.name: argument for argument in script.metadata.arguments}
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise ValueError(f"unknown arguments for {script.metadata.id}: {', '.join(unknown)}")

    missing = [
        argument.name
        for argument in script.metadata.arguments
        if argument.required and argument.name not in values
    ]
    if missing:
        raise ValueError(f"missing arguments for {script.metadata.id}: {', '.join(missing)}")

    argv: list[str] = []
    for spec in script.metadata.arguments:
        if spec.name not in values:
            continue
        value = values[spec.name]
        if spec.type == "string":
            if not isinstance(value, str):
                raise ValueError(f"argument {spec.name} must be a string")
            if "\x00" in value:
                raise ValueError(f"argument {spec.name} must not contain NUL bytes")
            if spec.min_length is not None and len(value) < spec.min_length:
                raise ValueError(f"argument {spec.name} is shorter than min_length")
            max_length = spec.max_length if spec.max_length is not None else 4_096
            if len(value) > max_length:
                raise ValueError(f"argument {spec.name} exceeds max_length")
            if spec.enum is not None and value not in spec.enum:
                raise ValueError(f"argument {spec.name} is not an allowed value")
            if spec.pattern is not None and re.fullmatch(spec.pattern, value) is None:
                raise ValueError(f"argument {spec.name} does not match its pattern")
            serialized = value
        elif spec.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"argument {spec.name} must be an integer")
            if spec.minimum is not None and value < spec.minimum:
                raise ValueError(f"argument {spec.name} is below minimum")
            if spec.maximum is not None and value > spec.maximum:
                raise ValueError(f"argument {spec.name} exceeds maximum")
            serialized = str(value)
        else:
            if not isinstance(value, bool):
                raise ValueError(f"argument {spec.name} must be a boolean")
            serialized = "true" if value else "false"

        argv.extend([f"--{spec.name.replace('_', '-')}", serialized])

    return argv


def build_script_command(script: ManagedScript, values: dict[str, Any]) -> str:
    argv = validate_script_arguments(script, values)
    if script.metadata.interpreter in {"sh", "bash"}:
        base = [script.metadata.interpreter, "-s", "--"]
    else:
        base = ["python3", "-"]
    return " ".join(shlex.quote(value) for value in [*base, *argv])


def ensure_target_compatible(script: ManagedScript, target_capabilities: list[str]) -> None:
    missing = sorted(set(script.metadata.required_capabilities()) - set(target_capabilities))
    if missing:
        raise ValueError(
            f"target is missing capabilities required by {script.metadata.id}: {', '.join(missing)}"
        )
