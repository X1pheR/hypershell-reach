from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
import sys
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route

from .config import ReachConfig, load_config
from .executor import ExecutorService
from .server import app as mcp_server
from .server import export_hermes_snapshot, initialize_runtime, set_executor_service
from .ui import create_app as create_web_app
from .validation import validate_configuration
from .peer_sync import MAX_BATCH_BYTES, import_batch, initialize_passive_baseline, passive_sync_loop, sync_once


class _StreamableHTTPASGIApp:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:
        await self.manager.handle_request(scope, receive, send)


def create_service_app(config: ReachConfig) -> Starlette:
    web_app = create_web_app(config)
    executor = ExecutorService(config, serve_socket=False)
    mcp_sessions = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=True,
        stateless=True,
    )

    @asynccontextmanager
    async def lifespan(_: Starlette):
        # Record legacy OCI Tasks even when periodic peer sync is disabled.
        # This prevents automatic ownership transfer at a later cutover.
        initialize_passive_baseline(config)
        await executor.start()
        initialize_runtime(config, executor_service=executor)
        sync_task: asyncio.Task[None] | None = None
        if (config.topology is not None
                and config.topology.effective_mode == "passive"
                and config.topology.primary_target is not None):
            sync_task = asyncio.create_task(passive_sync_loop(config), name="reach-peer-sync")
        try:
            async with mcp_sessions.run():
                yield
        finally:
            if sync_task is not None:
                sync_task.cancel()
                try:
                    await sync_task
                except asyncio.CancelledError:
                    pass
            await executor.stop()
            set_executor_service(None)

    return Starlette(
        routes=[
            Route("/mcp", endpoint=_StreamableHTTPASGIApp(mcp_sessions)),
            *web_app.routes,
        ],
        lifespan=lifespan,
    )


def _serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reach", description="Hypershell Reach service")
    parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port. Default: 8080")
    return parser


def _snapshot_export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reach export-hermes-snapshot",
        description="Export a canonical read-only Hermes skill snapshot manifest.",
    )
    parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
    parser.add_argument("--source", default="hermes", help="Hermes skill source ID. Default: hermes")
    return parser


def _validate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reach validate", description="Validate Hypershell Reach configuration")
    parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"migrate-persistence", "export-persistence"}:
        from .persistence_migration import migrate_persistence, export_legacy
        command = arguments[0]
        parser = argparse.ArgumentParser(prog=f"reach {command}", description="Offline peer-local state migration/rollback; quiesce all source writers first.")
        parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
        parser.add_argument("--database", help="Explicit unified DB path; defaults to workspace.database.")
        if command == "migrate-persistence":
            parser.add_argument("--fresh", action="store_true", help="Explicitly initialize empty state instead of importing legacy stores.")
        else:
            parser.add_argument("--output", required=True, help="New rollback directory; must not exist.")
        args = parser.parse_args(arguments[1:])
        config = load_config(args.config)
        database = args.database or config.workspace.database
        if not database:
            parser.error("--database or workspace.database is required")
        if command == "export-persistence":
            receipt = export_legacy(database, args.output)
        elif args.fresh:
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as temporary:
                candidates = Path(temporary) / "candidates" if config.workspace.candidates else None
                if candidates is not None:
                    candidates.mkdir()
                receipt = migrate_persistence(database, candidates_root=candidates)
        else:
            receipt = migrate_persistence(database, runs_root=config.workspace.runs,
                tasks_root=config.workspace.tasks, archive_root=config.workspace.trash,
                candidates_root=config.workspace.candidates)
        print(json.dumps(receipt, sort_keys=True))
        return
    if arguments and arguments[0] in {"peer-import", "peer-sync-once"}:
        command = arguments[0]
        parser = argparse.ArgumentParser(prog=f"reach {command}")
        parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
        if command == "peer-import":
            parser.add_argument("--origin", required=True, help="SSH-authenticated source node.")
        args = parser.parse_args(arguments[1:])
        config = load_config(args.config)
        if command == "peer-import":
            raw = sys.stdin.buffer.read(MAX_BATCH_BYTES + 1)
            if len(raw) > MAX_BATCH_BYTES:
                parser.error("peer import exceeds maximum batch size")
            print(json.dumps(import_batch(config, raw, authenticated_origin=args.origin),
                             sort_keys=True))
        else:
            print(json.dumps(asyncio.run(sync_once(config)), sort_keys=True))
        return
    if arguments and arguments[0] == "validate":
        args = _validate_parser().parse_args(arguments[1:])
        report = validate_configuration(args.config)
        print(report.text)
        raise SystemExit(0 if report.valid else 1)
    if arguments and arguments[0] == "export-runs-json":
        from .runs import RunStore
        parser = argparse.ArgumentParser(prog="reach export-runs-json", description="Export peer-local Runs for offline rollback.")
        parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
        parser.add_argument("--output", required=True, help="New output directory; must not already exist.")
        args = parser.parse_args(arguments[1:])
        config = load_config(args.config)
        print(json.dumps(RunStore(config.workspace.runs, read_only=True, database=config.workspace.database).export_json(args.output), sort_keys=True))
        return
    if arguments and arguments[0] == "export-hermes-snapshot":
        args = _snapshot_export_parser().parse_args(arguments[1:])
        payload = asyncio.run(export_hermes_snapshot(load_config(args.config), args.source))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return

    args = _serve_parser().parse_args(arguments)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    app = create_service_app(load_config(args.config))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
