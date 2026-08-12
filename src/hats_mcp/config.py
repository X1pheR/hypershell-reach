from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    max_timeout_seconds: int = Field(default=300, ge=1, le=900)
    max_output_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tmp: str
    tasks: str
    trash: str

    @field_validator("tmp", "tasks", "trash")
    @classmethod
    def require_absolute_paths(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("workspace paths must be absolute")
        return value


class ToolSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=63)
    type: Literal["filesystem"] = "filesystem"
    path: str = Field(min_length=1)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SOURCE_ID.fullmatch(value):
            raise ValueError("invalid tool source ID")
        return value

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("tool source paths must be absolute")
        return value


class Sources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[ToolSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_tool_source_ids(self) -> "Sources":
        ids = [source.id for source in self.tools]
        duplicates = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool source IDs: {', '.join(duplicates)}")
        return self


class SSHConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65_535)
    user: str = Field(pattern=r"^[a-z_][a-z0-9_-]*[$]?$", min_length=1, max_length=64)
    identity_file: str = Field(min_length=1)
    known_hosts_file: str = Field(min_length=1)

    @field_validator("identity_file", "known_hosts_file")
    @classmethod
    def require_absolute_paths(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("SSH file paths must be absolute")
        return value

    @field_validator("host")
    @classmethod
    def reject_unsafe_host_text(cls, value: str | None) -> str | None:
        if value is not None and (any(character.isspace() for character in value) or "\x00" in value):
            raise ValueError("host must not contain whitespace or NUL bytes")
        return value


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    transport: Literal["ssh"] = "ssh"
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    connect_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    max_timeout_seconds: int | None = Field(default=None, ge=1, le=900)
    max_output_bytes: int | None = Field(default=None, ge=1_024, le=1_048_576)
    ssh: SSHConfig

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        normalized = sorted(set(values))
        invalid = [value for value in normalized if not _CAPABILITY.fullmatch(value)]
        if invalid:
            raise ValueError(f"invalid capabilities: {', '.join(invalid)}")
        return normalized

    @model_validator(mode="after")
    def require_host_when_enabled(self) -> "Target":
        if self.enabled and not self.ssh.host:
            raise ValueError("enabled targets require an SSH host")
        return self


class HATSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    workspace: Workspace
    defaults: Defaults = Field(default_factory=Defaults)
    sources: Sources = Field(default_factory=Sources)
    targets: dict[str, Target]

    @model_validator(mode="after")
    def validate_target_ids(self) -> "HATSConfig":
        if not self.targets:
            raise ValueError("at least one target is required")
        invalid = sorted(target_id for target_id in self.targets if not _TARGET_ID.fullmatch(target_id))
        if invalid:
            raise ValueError(f"invalid target IDs: {', '.join(invalid)}")
        return self

    def enabled_target(self, target_id: str) -> Target:
        target = self.targets.get(target_id)
        if target is None:
            raise ValueError(f"unknown target: {target_id}")
        if not target.enabled:
            raise ValueError(f"target is disabled: {target_id}")
        return target

    def resolved_connect_timeout(self, target: Target) -> int:
        return target.connect_timeout_seconds or self.defaults.connect_timeout_seconds

    def resolved_max_timeout(self, target: Target) -> int:
        return target.max_timeout_seconds or self.defaults.max_timeout_seconds

    def resolved_max_output(self, target: Target) -> int:
        return target.max_output_bytes or self.defaults.max_output_bytes


def load_config(path: str | Path | None = None) -> HATSConfig:
    configured_path = path if path is not None else os.environ.get("HATS_CONFIG")
    if configured_path is None or not str(configured_path).strip():
        raise RuntimeError("HATS_CONFIG is not set")

    config_path = Path(configured_path)
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"HATS config not found: {config_path}") from exc
    except IsADirectoryError as exc:
        raise RuntimeError(f"HATS config is not a file: {config_path}") from exc
    except PermissionError as exc:
        raise RuntimeError(f"HATS config is not readable: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"HATS config is invalid YAML: {exc}") from exc

    try:
        return HATSConfig.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"HATS config failed validation: {exc}") from exc
