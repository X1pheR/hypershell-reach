from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route

from .config import ReachConfig, load_config
from .executor import ExecutorService
from .server import app as mcp_server
from .server import initialize_runtime, set_executor_service
from .ui import create_app as create_web_app
from .validation import validate_configuration


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
        await executor.start()
        initialize_runtime(config, executor_service=executor)
        try:
            async with mcp_sessions.run():
                yield
        finally:
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


def _validate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reach validate", description="Validate Hypershell Reach configuration")
    parser.add_argument("--config", help="Configuration file. Defaults to REACH_CONFIG.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "validate":
        args = _validate_parser().parse_args(arguments[1:])
        report = validate_configuration(args.config)
        print(report.text)
        raise SystemExit(0 if report.valid else 1)

    args = _serve_parser().parse_args(arguments)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    app = create_service_app(load_config(args.config))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
