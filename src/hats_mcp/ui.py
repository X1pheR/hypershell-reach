from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from html import escape
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .config import HATSConfig, load_config
from .read_model import HATSReadModel

_NAV = (
    ("Targets", "/targets"),
    ("Managed tooling", "/tooling"),
    ("Runs", "/runs"),
    ("Tasks", "/tasks"),
    ("Skills", "/skills"),
    ("Tooling Candidates", "/candidates"),
)

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

_CSS = """
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #0b1020; color: #e6edf8; }
a { color: inherit; text-decoration: none; }
.shell { max-width: 1440px; margin: 0 auto; padding: 28px; }
header { display: flex; gap: 22px; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.brand strong { display: block; font-size: 1.25rem; letter-spacing: .02em; }
.brand span, .muted { color: #95a3b8; font-size: .9rem; }
nav { display: flex; flex-wrap: wrap; gap: 8px; }
nav a { padding: 8px 11px; border: 1px solid #26324b; border-radius: 9px; color: #c8d3e5; background: #11192c; }
nav a.active, nav a:hover { border-color: #6d79ff; color: #fff; background: #18203a; }
h1 { font-size: 1.65rem; margin: 0 0 6px; }
.intro { color: #95a3b8; margin: 0 0 22px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }
.card { padding: 18px; border: 1px solid #26324b; border-radius: 12px; background: #11192c; }
.card h2 { font-size: 1rem; margin: 0 0 8px; }
.card p { color: #95a3b8; margin: 0; line-height: 1.45; }
.table-wrap { overflow-x: auto; border: 1px solid #26324b; border-radius: 12px; background: #11192c; }
table { width: 100%; border-collapse: collapse; min-width: 780px; }
th, td { padding: 11px 13px; border-bottom: 1px solid #202b42; text-align: left; vertical-align: top; font-size: .9rem; }
th { color: #9ba9bd; font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; background: #0f1729; }
tr:last-child td { border-bottom: 0; }
.badge { display: inline-block; margin: 1px 4px 1px 0; padding: 2px 7px; border: 1px solid #34415d; border-radius: 999px; color: #c8d3e5; font-size: .78rem; }
.yes { color: #8fe3bd; } .no { color: #f3a6aa; }
.notice { margin: 0 0 14px; padding: 11px 13px; border: 1px solid #34415d; border-radius: 10px; background: #121b30; color: #bac7da; }
.empty { padding: 24px; color: #95a3b8; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .84em; }
@media (max-width: 760px) { .shell { padding: 18px; } header { align-items: flex-start; flex-direction: column; } }
"""


def _value(value: Any) -> str:
    if value is None or value == "":
        return '<span class="muted">—</span>'
    if isinstance(value, bool):
        return f'<span class="{"yes" if value else "no"}">{"yes" if value else "no"}</span>'
    if isinstance(value, (list, tuple, set)):
        if not value:
            return '<span class="muted">—</span>'
        return "".join(f'<span class="badge">{escape(str(item))}</span>' for item in value)
    return escape(str(value))


def _table(columns: Sequence[tuple[str, str]], rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="table-wrap"><div class="empty">No entries.</div></div>'
    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_value(row.get(key))}</td>" for key, _ in columns) + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _page(title: str, intro: str, content: str, *, active: str | None = None) -> HTMLResponse:
    nav = "".join(
        f'<a class="{"active" if href == active else ""}" href="{href}">{escape(label)}</a>'
        for label, href in _NAV
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · HATS</title><style>{_CSS}</style></head>
<body><div class="shell"><header><a class="brand" href="/"><strong>HATS</strong><span>Read-only operations view</span></a><nav>{nav}</nav></header>
<main><h1>{escape(title)}</h1><p class="intro">{escape(intro)}</p>{content}</main></div></body></html>"""
    return HTMLResponse(document, headers=_SECURITY_HEADERS)


def _safe_table(loader: Callable[[], list[dict[str, Any]]], columns: Sequence[tuple[str, str]]) -> str:
    try:
        rows = loader()
    except (OSError, RuntimeError, ValueError):
        return '<div class="notice">This view is temporarily unavailable.</div>'
    return _table(columns, rows)


def create_app(config: HATSConfig) -> Starlette:
    model = HATSReadModel(config)

    def home(_: Request) -> Response:
        cards = "".join(
            f'<a class="card" href="{href}"><h2>{escape(label)}</h2><p>{description}</p></a>'
            for label, href, description in (
                ("Targets", "/targets", "Configured execution targets and safe capabilities."),
                ("Managed tooling", "/tooling", "Registered scripts and execution metadata."),
                ("Runs", "/runs", "Recent persisted execution metadata without command output."),
                ("Tasks", "/tasks", "Current task-continuity summaries."),
                ("Skills", "/skills", "Configured skill content without remote execution."),
                ("Tooling Candidates", "/candidates", "Explicit promotion candidates from the configured registry."),
            )
        )
        return _page(
            "HATS overview",
            "Read-only visibility into HATS configuration and file-backed state.",
            f'<div class="grid">{cards}</div>',
        )

    def targets(_: Request) -> Response:
        content = _safe_table(
            model.targets,
            (
                ("id", "ID"),
                ("display_name", "Name"),
                ("transport", "Transport"),
                ("capabilities", "Capabilities"),
                ("enabled", "Enabled"),
                ("max_timeout_seconds", "Max timeout (s)"),
                ("max_output_bytes", "Max output (bytes)"),
            ),
        )
        return _page("Targets", "Safe target metadata. Connection and credential fields are intentionally omitted.", content, active="/targets")

    def tooling(_: Request) -> Response:
        content = _safe_table(
            model.tooling,
            (
                ("id", "ID"),
                ("name", "Name"),
                ("description", "Description"),
                ("source", "Source"),
                ("domain", "Domain"),
                ("interpreter", "Interpreter"),
                ("requires", "Requires"),
                ("mutating", "Mutating"),
                ("idempotent", "Idempotent"),
            ),
        )
        return _page("Managed tooling", "Registered managed scripts. Source code and filesystem paths are not rendered.", content, active="/tooling")

    def runs(_: Request) -> Response:
        content = _safe_table(
            model.run_summaries,
            (
                ("id", "Run"),
                ("operation", "Operation"),
                ("target", "Target"),
                ("task_id", "Task"),
                ("script_id", "Script"),
                ("status", "Status"),
                ("ambiguous", "Ambiguous"),
                ("started_at", "Started"),
                ("ended_at", "Ended"),
                ("retained", "Retained"),
            ),
        )
        return _page("Runs", "Recent execution records. Commands, arguments and output content are not persisted or rendered.", content, active="/runs")

    def tasks(_: Request) -> Response:
        content = _safe_table(
            model.task_summaries,
            (
                ("id", "Task"),
                ("title", "Title"),
                ("status", "Status"),
                ("updated_at", "Updated"),
                ("retained", "Retained"),
            ),
        )
        return _page("Tasks", "Current task-continuity summaries. Full continuity evidence is intentionally not rendered.", content, active="/tasks")

    def skills(_: Request) -> Response:
        try:
            rows, reports = model.skills()
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice">This view is temporarily unavailable.</div>'
        else:
            content_only = [report["id"] for report in reports if report.get("state") == "content-only"]
            notice = ""
            if content_only:
                notice = (
                    '<div class="notice">Hermes skill activation state is intentionally not projected by hats-ui. '
                    'This view shows configured skill content only; hats-mcp remains authoritative for the live effective catalog.</div>'
                )
            content = notice + _table(
                (
                    ("source", "Source"),
                    ("name", "Name"),
                    ("description", "Description"),
                    ("category", "Category"),
                    ("provenance", "Provenance"),
                ),
                rows,
            )
        return _page("Skills", "Configured read-only Agent Skill content.", content, active="/skills")

    def candidates(_: Request) -> Response:
        try:
            configured, rows = model.candidates()
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice">This view is temporarily unavailable.</div>'
        else:
            prefix = "" if configured else '<div class="notice">No tooling registry is configured.</div>'
            content = prefix + _table(
                (
                    ("id", "ID"),
                    ("title", "Title"),
                    ("status", "Status"),
                    ("promotion_reason", "Promotion reason"),
                ),
                rows,
            )
        return _page("Tooling Candidates", "Deployment-reviewed reusable-tooling promotion candidates.", content, active="/candidates")

    def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "role": "hats-ui"}, headers={"Cache-Control": "no-store"})

    return Starlette(
        routes=[
            Route("/", home, methods=["GET"]),
            Route("/targets", targets, methods=["GET"]),
            Route("/tooling", tooling, methods=["GET"]),
            Route("/runs", runs, methods=["GET"]),
            Route("/tasks", tasks, methods=["GET"]),
            Route("/skills", skills, methods=["GET"]),
            Route("/candidates", candidates, methods=["GET"]),
            Route("/healthz", health, methods=["GET"]),
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hats-ui", description="Read-only HATS Web UI")
    parser.add_argument("--config", help="Configuration file. Defaults to HATS_CONFIG.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port. Default: 8080")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    app = create_app(load_config(args.config))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=True)
