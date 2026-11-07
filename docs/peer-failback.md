# Native peer failback — accepted direction, implementation pending

This document specifies the bounded change for Hypershell Reach active/passive deployments. The first native implementation is now an **isolated, unreleased local candidate**. No live Home or OCI Reach deployment is running this implementation; all behavior below remains subject to source review, release acceptance and two-peer rollout gates.

Implementation detail: the OCI passive peer sends record batches over its existing verified SSH target to a fixed remote `reach-peer-import` command. The Home infrastructure deployment owns that narrow entrypoint; generic Reach source contains no Hypershell-specific Docker container names. Home imports transactionally into an origin-qualified metadata mirror; fenced eligible Tasks are promoted atomically into native Home Tasks using newly generated IDs and a retained origin mapping.

## Goal and boundaries

**PF-01 — Home-first execution.** All normal agents and gateways route Reach requests to the active peer, even if the execution target is on the standby host. A passive peer is a disaster-recovery executor only when the active peer cannot be reached. This routing choice belongs to the consumers; a health probe alone must not automatically replay an execution. The passive peer cannot infer a global primary outage from one failed probe.

**PF-02 — Minimal deployment configuration.** The existing topology identity defines primary ownership. Optional `topology.mode: active|passive` explicitly states the same role and MUST fail configuration validation if inconsistent with `node_id == primary_node_id`. For a passive peer, `topology.primary_target` may name an existing enabled SSH target for the primary host; the remote peer's authenticated identity MUST be verified before transferring anything. This reuses the target's host, SSH key and known-hosts. The polling cadence is a product default of 120 seconds, not a new user-facing setting. Legacy topology without `mode` remains valid; no synchronization is implied until a separately accepted release.

**PF-03 — One-way durable state.** The passive peer sends its Tasks (including archived history), Runs (including ambiguous, interrupted and retained records), original Task-to-Run IDs and safe metadata to the active peer. Candidates remain active-owned and are NEVER accepted from a passive peer. Do not synchronize a running process, execution lease, raw command, secrets, output, SQLite/WAL file or source configuration. Referenced evidence remains at its original owner; the transfer carries only `result_ref`.

## Import invariants

**PF-04 — Authenticated private transport.** The passive initiates a connection over the already configured authenticated SSH target. The importer is a narrow native, non-public Reach CLI operation and is reached by the deployment-owned fixed SSH entrypoint. It validates authenticated source node, expected primary node, protocol/schema versions, size/record count, data models and source identity; it never grants general database or command access. An anonymous HTTP endpoint and broad new credential are excluded.

**PF-05 — Origin and idempotency.** Imported metadata is keyed by `(origin_node_id, kind, original_id)`. Run payloads are immutable once terminal; Task updates use monotonic source revisions and reject same-revision/different-digest payloads. Retry of a committed batch returns the same receipt; no duplicated Task or Run. The primary persists each bounded batch in one SQLite transaction and returns a verifiable receipt. The passive advances its local checkpoint only after a matching receipt. Crashes before receipt simply replay the batch.

**PF-06 — Mirror before owner handoff.** Imported active OCI Tasks are initially **read-only source-owned mirrors**. The primary must not execute them or accept Task-linked mutations while the passive owns them. A live local lease, pending mutation, ambiguous Run, unknown remote outcome or divergent Home-side Task forces a visible blocked/manual-reconciliation state. Neither an expired lease nor an SSH timeout is a reason to declare the work finished or to retry it.

**PF-07 — Fenced ownership handoff.** For a safe-to-transfer Task, the passive first commits a durable fence preventing any new source-side Task mutation/execution, while preserving history. Only after proof of the fence may the primary promote a Task to its editable continuity owner. The primary returns a durable transfer receipt linking `origin_node_id/original_task_id` to `home_task_id`. Replayed transfers reuse the mapping and cannot create duplicates. The passive then records the acknowledged transfer as a versioned schema-v3 `transferred` terminal local state without erasing its history. If interrupted between fencing, promotion and acknowledgement, both sides recover from the last committed receipt without executing the task again. Do not repurpose `completed` to mean `transferred`.

**PF-08 — Conflicts and deletion.** A same-named Task is not enough to prove identity. Never merge by title or overwrite a Home Task from a foreign peer. Conflicts become a typed operator review. Source-origin history is not automatically deleted upon acknowledgement; both sites apply their own existing retention rules only after transfer safety and visibility are demonstrated. A primary may retain mirror records longer than the source's configured cleanup window, but must never resurrect a source-deleted record as executable work.

**PF-09 — Availability and observability.** Passive failures to reach primary do not block recovery work or destroy local state. The UI exposes role, last successful sync, pending count, transfer-blocked count and last error, with no secrets. The Home UI differentiates locally executable Tasks from imported read-only OCI history. Recovery backlog is visible through existing monitoring/scheduled-work patterns, without a new standalone daemon or alerting service.

## Acceptance scenarios required before deployment

- Initial import of pre-existing OCI Tasks and Runs, including archive and same-ID collision; zero loss or duplicate after replay.
- Acknowledgement loss, mid-batch interruption and interrupted restart: exactly one committed logical record.
- Malformed input, wrong source/primary identity, changed digest at same revision, active-role sender and Candidate payload fail closed.
- Source active lease, pending mutation, ambiguous execution, conflicting Home Task, and interrupted two-phase transfer never execute twice.
- Successful transfer retains the original Task/Run relationship and origin deep link, and Home can continue the Task normally.
- Passive can record recovery work while Home is down; sync resumes automatically after Home recovers.
- All existing v0.12.1 MCP/web/SQLite/retention tests pass. A full governed product release and two-peer rollout remain separate acceptance gates.

## Rollout and compatibility

1. Qualify existing Home-first MCPJungle/agent routing independently without moving production state.
2. Implement protocol and isolated two-peer synthetic tests against compatible disposable stores.
3. Roll out with sender disabled until both peers run the accepted compatible release.
4. Perform read-only dry-run inventory and initial import; inspect collisions and outstanding OCI Tasks.
5. Enable scheduled passive push only after end-to-end proof and source/consumer parity.
6. Confirm Home-led workflows and independent OCI recovery; only then reconcile historical OCI ownership.

No new automatic writer should be activated by the explicit mode configuration alone.

## Release/rollback gate (not yet accepted)

The new `transferred` Task state is schema v3 and is intentionally **not readable by v0.12.1**. Do not activate the passive sender merely because a candidate image is healthy. Both peers must run a fully accepted compatible release first. Retain exact peer-local online SQLite backups and the accepted new image/configuration.

A *non-destructive* compatibility export now exists: `reach peer-rollback-export --config <path> --output <new-absolute-path>`. It creates an application-consistent SQLite snapshot, converts the v3 `transferred` source archives into v2 `cancelled` archives for older-reader compatibility, and preserves origin-to-Home mappings as metadata. It never changes the live database. This export has synthetic tests, but its exact **stopped-writer cutover and v0.12.1 consumer acceptance** have not yet been accepted. Do not switch image or replace a database while either Reach peer is serving. Preserve preimage, output receipt and review changed-scope migration proof before promotion.

The source candidate includes synthetic two-peer import, loss-of-ACK replay, stale-version rejection, active lease/pending-mutation/ambiguous-Run fencing and Task takeover tests, but no live SSH-to-Home acceptance or consumer routing change has been performed.
