from __future__ import annotations

import hashlib
import json

from .skills import HermesState


def _revision(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_snapshot_payload(
    *,
    source_id: str,
    state: HermesState,
    content_fingerprint: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "source_id": source_id,
        "consumer_platform": state.consumer_platform,
        "disabled": sorted(state.disabled),
        "external_dirs": sorted(state.external_dirs),
        "effective_names": sorted(state.effective_names),
        "content_fingerprint": content_fingerprint,
    }
    return {**payload, "snapshot_revision": _revision(payload)}


def parse_snapshot_payload(
    payload: dict[str, object],
    *,
    source_id: str,
    content_fingerprint: str,
) -> HermesState:
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported Hermes snapshot schema")
    if payload.get("source_id") != source_id:
        raise RuntimeError("Hermes snapshot source mismatch")
    if payload.get("content_fingerprint") != content_fingerprint:
        raise RuntimeError("Hermes snapshot content fingerprint mismatch")
    signed_payload = {key: value for key, value in payload.items() if key != "snapshot_revision"}
    if payload.get("snapshot_revision") != _revision(signed_payload):
        raise RuntimeError("Hermes snapshot revision mismatch")
    return HermesState(
        effective_names=frozenset(str(value) for value in payload.get("effective_names", [])),
        disabled=frozenset(str(value) for value in payload.get("disabled", [])),
        external_dirs=tuple(str(value) for value in payload.get("external_dirs", [])),
        consumer_platform=(
            str(payload["consumer_platform"])
            if payload.get("consumer_platform") is not None
            else None
        ),
    )
