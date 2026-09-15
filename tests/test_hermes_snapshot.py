from __future__ import annotations

from hypershell_reach.skills import HermesState


def test_snapshot_payload_revision_is_deterministic_and_covers_state_and_content() -> None:
    from hypershell_reach.hermes_snapshot import build_snapshot_payload

    state = HermesState(
        effective_names=frozenset({"one", "two"}),
        disabled=frozenset({"off"}),
        external_dirs=("/external",),
        consumer_platform="cli",
    )

    first = build_snapshot_payload(
        source_id="hermes",
        state=state,
        content_fingerprint="a" * 64,
    )
    same = build_snapshot_payload(
        source_id="hermes",
        state=state,
        content_fingerprint="a" * 64,
    )
    changed_content = build_snapshot_payload(
        source_id="hermes",
        state=state,
        content_fingerprint="b" * 64,
    )
    changed_state = build_snapshot_payload(
        source_id="hermes",
        state=HermesState(
            effective_names=frozenset({"one"}),
            disabled=frozenset({"off", "two"}),
            external_dirs=("/external",),
            consumer_platform="cli",
        ),
        content_fingerprint="a" * 64,
    )

    assert first == same
    assert first["schema_version"] == 1
    assert first["source_id"] == "hermes"
    assert first["effective_names"] == ["one", "two"]
    assert first["disabled"] == ["off"]
    assert first["external_dirs"] == ["/external"]
    assert first["content_fingerprint"] == "a" * 64
    assert len(first["snapshot_revision"]) == 64
    assert first["snapshot_revision"] != changed_content["snapshot_revision"]
    assert first["snapshot_revision"] != changed_state["snapshot_revision"]


def test_snapshot_payload_parser_reconstructs_hermes_state() -> None:
    from hypershell_reach.hermes_snapshot import build_snapshot_payload, parse_snapshot_payload

    state = HermesState(
        effective_names=frozenset({"one", "two"}),
        disabled=frozenset({"off"}),
        external_dirs=("/external",),
        consumer_platform="cli",
    )
    payload = build_snapshot_payload(
        source_id="hermes",
        state=state,
        content_fingerprint="a" * 64,
    )

    parsed = parse_snapshot_payload(
        payload,
        source_id="hermes",
        content_fingerprint="a" * 64,
    )

    assert parsed == state


def test_snapshot_payload_parser_rejects_content_mismatch() -> None:
    from hypershell_reach.hermes_snapshot import build_snapshot_payload, parse_snapshot_payload

    payload = build_snapshot_payload(
        source_id="hermes",
        state=HermesState(
            effective_names=frozenset({"one"}),
            disabled=frozenset(),
            external_dirs=(),
            consumer_platform="cli",
        ),
        content_fingerprint="a" * 64,
    )

    import pytest

    with pytest.raises(RuntimeError, match="content fingerprint mismatch"):
        parse_snapshot_payload(
            payload,
            source_id="hermes",
            content_fingerprint="b" * 64,
        )


def test_snapshot_payload_parser_rejects_revision_mismatch() -> None:
    from hypershell_reach.hermes_snapshot import build_snapshot_payload, parse_snapshot_payload

    payload = build_snapshot_payload(
        source_id="hermes",
        state=HermesState(
            effective_names=frozenset({"one"}),
            disabled=frozenset(),
            external_dirs=(),
            consumer_platform="cli",
        ),
        content_fingerprint="a" * 64,
    )
    payload["effective_names"] = ["one", "tampered"]

    import pytest

    with pytest.raises(RuntimeError, match="revision mismatch"):
        parse_snapshot_payload(
            payload,
            source_id="hermes",
            content_fingerprint="a" * 64,
        )


def test_snapshot_payload_parser_rejects_source_mismatch() -> None:
    from hypershell_reach.hermes_snapshot import build_snapshot_payload, parse_snapshot_payload

    payload = build_snapshot_payload(
        source_id="hermes",
        state=HermesState(
            effective_names=frozenset({"one"}),
            disabled=frozenset(),
            external_dirs=(),
            consumer_platform="cli",
        ),
        content_fingerprint="a" * 64,
    )

    import pytest

    with pytest.raises(RuntimeError, match="source mismatch"):
        parse_snapshot_payload(
            payload,
            source_id="other-hermes",
            content_fingerprint="a" * 64,
        )


def test_snapshot_payload_parser_rejects_unsupported_schema_even_with_matching_revision() -> None:
    import hashlib
    import json
    import pytest

    from hypershell_reach.hermes_snapshot import build_snapshot_payload, parse_snapshot_payload

    payload = build_snapshot_payload(
        source_id="hermes",
        state=HermesState(
            effective_names=frozenset({"one"}),
            disabled=frozenset(),
            external_dirs=(),
            consumer_platform="cli",
        ),
        content_fingerprint="a" * 64,
    )
    payload["schema_version"] = 2
    signed = {key: value for key, value in payload.items() if key != "snapshot_revision"}
    payload["snapshot_revision"] = hashlib.sha256(
        json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(RuntimeError, match="unsupported Hermes snapshot schema"):
        parse_snapshot_payload(
            payload,
            source_id="hermes",
            content_fingerprint="a" * 64,
        )
