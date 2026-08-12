from __future__ import annotations

import argparse
from collections.abc import Sequence

from .validation import validate_configuration


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hats-mcp",
        description="Homelab Agent Tooling & Skills MCP",
    )
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser(
        "validate",
        help="Validate local HATS configuration and runtime prerequisites without network access.",
    )
    validate.add_argument(
        "--config",
        help="Configuration file. Defaults to HATS_CONFIG.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        report = validate_configuration(args.config)
        print(report.text)
        raise SystemExit(0 if report.valid else 1)

    from .server import main as run_server

    run_server()
