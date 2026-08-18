from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from html import escape
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from .config import HATSConfig, load_config
from .read_model import HATSReadModel
from .ui_assets import CSS, HATS_MARK_SVG, JAVASCRIPT
from .ui_docs import USER_GUIDE, DocumentationPage, TocItem, documentation_groups, render_document, technical_page

_NAV = (
    ("Overview", "/"),
    ("Targets", "/targets"),
    ("Tooling", "/tooling"),
    ("Runs", "/runs"),
    ("Tasks", "/tasks"),
    ("Skills", "/skills"),
    ("Documentation", "/docs"),
)

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'; object-src 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

_ASSET_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

_SUCCESS_STATES = {"succeeded", "success", "completed", "enabled", "ready"}
_WARNING_STATES = {"running", "partial", "blocked", "waiting", "unknown", "observed"}
_ERROR_STATES = {"failed", "error", "remote_error", "transport_error", "timeout", "local_error", "interrupted"}


def _brand() -> str:
    return """
<a class="brand" href="/" aria-label="Open HATS overview">
  <img class="brand-mark" src="/assets/hats-mark.svg" alt="" width="44" height="44">
  <span class="brand-copy"><strong>HATS</strong><small>Homelab Agent Tooling &amp; Skills</small></span>
</a>
"""


def _nav_links(active: str | None) -> str:
    items: list[str] = []
    for label, href in _NAV:
        current = ' aria-current="page"' if href == active else ""
        items.append(f'<a href="{href}"{current}>{escape(label)}</a>')
    return "".join(items)


def _page(title: str, intro: str, content: str, *, active: str | None = None) -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#050816">
  <title>{escape(title)} · HATS</title>
  <link rel="icon" href="/assets/hats-mark.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <header class="site-header" aria-label="Product header">
    <div class="header-leading">
      <button id="mobile-menu-toggle" class="icon-button mobile-menu-toggle" type="button" aria-label="Open navigation" aria-haspopup="dialog" aria-controls="mobile-navigation" aria-expanded="false">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>
      {_brand()}
    </div>
    <nav class="primary-navigation" aria-label="Primary navigation">{_nav_links(active)}</nav>
    <div class="header-actions"><span class="utility-pill"><span class="utility-dot" aria-hidden="true"></span>Read-only</span></div>
  </header>
  <main id="main-content" tabindex="-1">
    <div class="page-heading-row"><div><h1>{escape(title)}</h1><p class="page-summary">{escape(intro)}</p></div></div>
    {content}
  </main>
  <dialog id="mobile-navigation" class="mobile-navigation-sheet" aria-labelledby="mobile-navigation-title">
    <div class="mobile-sheet-layout">
      <div class="mobile-sheet-heading">
        <div>{_brand()}</div>
        <button id="close-mobile-navigation" class="icon-button" type="button" aria-label="Close navigation"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      </div>
      <span id="mobile-navigation-title" class="sr-only">HATS navigation</span>
      <nav class="mobile-primary-navigation" aria-label="Mobile primary navigation">{_nav_links(active)}</nav>
      <div class="mobile-utility-navigation"><span class="utility-pill"><span class="utility-dot" aria-hidden="true"></span>Read-only</span></div>
    </div>
  </dialog>
  <script src="/assets/app.js" defer></script>
</body>
</html>"""
    return HTMLResponse(document, headers=_SECURITY_HEADERS)


def _status_class(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in _SUCCESS_STATES:
        return "success"
    if normalized in _WARNING_STATES:
        return "warning"
    if normalized in _ERROR_STATES:
        return "error"
    return "neutral"


def _value(key: str, value: Any) -> str:
    if value is None or value == "":
        return '<span class="muted">—</span>'
    if isinstance(value, bool):
        label = "Yes" if value else "No"
        return f'<span class="status-badge neutral">{label}</span>'
    if isinstance(value, (list, tuple, set)):
        if not value:
            return '<span class="muted">—</span>'
        return "".join(f'<span class="tag">{escape(str(item))}</span>' for item in value)
    if key == "status":
        text = str(value)
        return f'<span class="status-badge {_status_class(text)}">{escape(text)}</span>'
    return escape(str(value))


def _table(columns: Sequence[tuple[str, str]], rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="data-region"><div class="empty">No entries.</div></div>'
    head = "".join(f'<th scope="col">{escape(label)}</th>' for _, label in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_value(key, row.get(key))}</td>" for key, _ in columns) + "</tr>"
        for row in rows
    )
    return f'<div class="data-region"><div class="table-wrap" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>'


def _safe_table(loader: Callable[[], list[dict[str, Any]]], columns: Sequence[tuple[str, str]]) -> str:
    try:
        rows = loader()
    except (OSError, RuntimeError, ValueError):
        return '<div class="notice error" role="alert">This view is temporarily unavailable.</div>'
    return _table(columns, rows)


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _overview_card(
    title: str,
    href: str,
    metric: str,
    description: str,
    *,
    detail: str | None = None,
    state: str = "neutral",
) -> str:
    detail_html = f'<span class="overview-detail">{escape(detail)}</span>' if detail else ""
    return (
        f'<a class="destination-card state-{escape(state)}" href="{href}">'
        f'<h2>{escape(title)}</h2>'
        f'<strong class="overview-metric">{escape(metric)}</strong>'
        f'<p>{escape(description)}</p>'
        f'{detail_html}<span class="card-link">Open {escape(title.lower())} →</span></a>'
    )


def _panel(title: str, description: str, content: str, *, element_id: str | None = None) -> str:
    identifier = f' id="{escape(element_id)}"' if element_id else ""
    return (
        f'<section class="panel panel-accent"{identifier}>'
        f'<div class="section-heading"><div><h2>{escape(title)}</h2><p>{escape(description)}</p></div></div>'
        f"{content}</section>"
    )


def _docs_navigation(active_page: DocumentationPage) -> str:
    guide_current = ' aria-current="page"' if active_page.slug == USER_GUIDE.slug else ""
    parts = [
        '<p class="docs-nav-title">Guide</p>',
        '<div class="docs-nav-links">',
        f'<a href="/docs"{guide_current}>User guide</a>',
        "</div>",
        '<p class="docs-nav-title">Technical documentation</p>',
    ]
    for group, pages in documentation_groups():
        parts.append(f'<p class="docs-nav-group">{escape(group)}</p><div class="docs-nav-links">')
        for page in pages:
            current = ' aria-current="page"' if page.slug == active_page.slug else ""
            parts.append(f'<a href="{page.route}"{current}>{escape(page.title)}</a>')
        parts.append("</div>")
    navigation = f'<nav aria-label="Documentation sections">{"".join(parts)}</nav>'
    return (
        f'<aside class="docs-navigation docs-navigation-desktop">{navigation}</aside>'
        '<details class="docs-navigation docs-navigation-mobile">'
        '<summary>Documentation menu</summary>'
        f'{navigation}</details>'
    )


def _docs_toc(items: Sequence[TocItem]) -> str:
    if not items:
        body = '<p class="empty-toc">No sections on this page.</p>'
    else:
        links = "".join(
            f'<a class="level-{item.level}" href="#{escape(item.anchor)}">{escape(item.title)}</a>' for item in items
        )
        body = f'<nav aria-label="On this page">{links}</nav>'
    return f'<aside class="docs-toc"><h2>On this page</h2>{body}</aside>'


def _documentation_layout(page: DocumentationPage) -> str:
    rendered = render_document(page)
    return (
        '<div class="docs-layout">'
        f"{_docs_navigation(page)}"
        f'<article class="docs-article">{rendered.html}</article>'
        f"{_docs_toc(rendered.toc)}"
        "</div>"
    )


def create_app(config: HATSConfig) -> Starlette:
    model = HATSReadModel(config)

    def home(_: Request) -> Response:
        try:
            target_rows = model.targets()
            enabled_targets = sum(1 for row in target_rows if row.get("enabled"))
            targets_card = _overview_card(
                "Targets",
                "/targets",
                f"{enabled_targets}/{len(target_rows)} enabled",
                "Systems HATS can connect to and what they support.",
            )
        except (OSError, RuntimeError, ValueError):
            targets_card = _overview_card(
                "Targets", "/targets", "Unavailable", "Target information could not be read.", state="error"
            )

        try:
            tool_rows = model.tooling()
            tool_metric = _count_label(len(tool_rows), "managed tool")
        except (OSError, RuntimeError, ValueError):
            tool_rows = []
            tool_metric = "Unavailable"
        try:
            candidates_configured, candidate_rows = model.candidates()
            candidate_detail = (
                _count_label(len(candidate_rows), "tooling candidate")
                if candidates_configured
                else "No candidate registry configured"
            )
        except (OSError, RuntimeError, ValueError):
            candidate_detail = "Tooling candidates unavailable"
        tooling_card = _overview_card(
            "Tooling",
            "/tooling",
            tool_metric,
            "Reusable tools and gaps being considered for automation.",
            detail=candidate_detail,
            state="error" if tool_metric == "Unavailable" else "neutral",
        )

        try:
            run_rows = model.recent_run_summaries()
            failed_runs = sum(1 for row in run_rows if _status_class(str(row.get("status") or "")) == "error")
            running_runs = sum(1 for row in run_rows if str(row.get("status") or "").lower() == "running")
            run_detail_parts = []
            if running_runs:
                run_detail_parts.append(_count_label(running_runs, "running"))
            if failed_runs:
                run_detail_parts.append(_count_label(failed_runs, "failed run"))
            runs_card = _overview_card(
                "Runs",
                "/runs",
                _count_label(len(run_rows), "recent run"),
                "Recent HATS activity and outcomes.",
                detail=" · ".join(run_detail_parts) or "No failures in the visible history",
                state="warning" if running_runs or failed_runs else "neutral",
            )
        except (OSError, RuntimeError, ValueError):
            runs_card = _overview_card(
                "Runs", "/runs", "Unavailable", "Recent run information could not be read.", state="error"
            )

        try:
            task_rows = model.task_summaries()
            active_tasks = sum(1 for row in task_rows if str(row.get("status") or "").lower() != "completed")
            completed_tasks = len(task_rows) - active_tasks
            tasks_card = _overview_card(
                "Tasks",
                "/tasks",
                _count_label(active_tasks, "active task"),
                "Continuity records for work that may span sessions.",
                detail=_count_label(completed_tasks, "completed task"),
            )
        except (OSError, RuntimeError, ValueError):
            tasks_card = _overview_card(
                "Tasks", "/tasks", "Unavailable", "Task information could not be read.", state="error"
            )

        try:
            skill_reports = model.skill_source_summaries()
            unavailable_sources = [report for report in skill_reports if not report.get("available", False)]
            if unavailable_sources:
                skills_card = _overview_card(
                    "Skills",
                    "/skills",
                    "Unavailable",
                    "One or more configured skill sources cannot be read.",
                    detail=_count_label(len(unavailable_sources), "unavailable source"),
                    state="error",
                )
            else:
                skill_count = sum(int(report.get("count") or 0) for report in skill_reports)
                skills_card = _overview_card(
                    "Skills",
                    "/skills",
                    _count_label(skill_count, "skill"),
                    "Shared Agent Skills HATS can read.",
                    detail=_count_label(len(skill_reports), "configured source"),
                )
        except (OSError, RuntimeError, ValueError):
            skills_card = _overview_card(
                "Skills", "/skills", "Unavailable", "Skill information could not be read.", state="error"
            )

        docs_card = _overview_card(
            "Documentation",
            "/docs",
            "User guide + technical docs",
            "How to use, configure and maintain HATS.",
        )
        cards = "".join((targets_card, tooling_card, runs_card, tasks_card, skills_card, docs_card))
        return _page(
            "Overview",
            "See what HATS can access, what ran recently and where to find details.",
            f'<div class="overview-grid">{cards}</div>',
            active="/",
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
        return _page(
            "Targets",
            "Systems HATS can connect to and what they support. Connection details and credentials stay hidden.",
            _panel("Configured targets", "Configured systems available to HATS.", content),
            active="/targets",
        )

    def _candidates_content() -> str:
        try:
            configured, rows = model.candidates()
        except (OSError, RuntimeError, ValueError):
            return '<div class="notice error" role="alert">Tooling candidates are temporarily unavailable.</div>'
        prefix = "" if configured else '<div class="notice">No tooling registry is configured.</div>'
        return prefix + _table(
            (
                ("id", "ID"),
                ("title", "Title"),
                ("status", "Status"),
                ("promotion_reason", "Promotion reason"),
            ),
            rows,
        )

    def tooling(_: Request) -> Response:
        managed = _safe_table(
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
        content = '<div class="section-stack">' + _panel(
            "Managed tools",
            "Reviewed tools HATS can run. Source code and deployment paths stay hidden.",
            managed,
        ) + _panel(
            "Tooling candidates",
            "Recurring gaps that may justify reusable automation.",
            _candidates_content(),
            element_id="candidates",
        ) + "</div>"
        return _page("Tooling", "See reusable tools and gaps being considered for automation.", content, active="/tooling")

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
        return _page(
            "Runs",
            "Recent HATS activity. Command text, arguments and output are not stored here.",
            _panel("Recent activity", "The latest HATS run records.", content),
            active="/runs",
        )

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
        return _page(
            "Tasks",
            "Work continuity records that may span sessions. Detailed private context stays hidden.",
            _panel("Task records", "Current HATS task summaries.", content),
            active="/tasks",
        )

    def skills(_: Request) -> Response:
        try:
            rows, reports = model.skills()
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice error" role="alert">Skills are temporarily unavailable.</div>'
        else:
            unavailable = [report["id"] for report in reports if not report.get("available", False)]
            content_only = [
                report["id"]
                for report in reports
                if report.get("available", False) and report.get("state") == "content-only"
            ]
            notices: list[str] = []
            if unavailable:
                source_names = ", ".join(escape(str(source_id)) for source_id in unavailable)
                notices.append(
                    '<div class="notice error" role="alert"><strong>Skill source unavailable.</strong> '
                    f'Could not read: {source_names}.</div>'
                )
            if content_only:
                notices.append(
                    '<div class="notice">This page shows the skill content HATS can read. '
                    'Use the HATS skill catalog when current agent activation matters.</div>'
                )
            table = ""
            if rows or not unavailable:
                table = _table(
                    (
                        ("source", "Source"),
                        ("name", "Name"),
                        ("description", "Description"),
                        ("category", "Category"),
                        ("provenance", "Origin"),
                    ),
                    rows,
                )
            content = "".join(notices) + table
        return _page(
            "Skills",
            "Agent Skills available from configured sources.",
            _panel("Available skills", "Skills HATS can read from configured sources.", content),
            active="/skills",
        )

    def documentation(_: Request) -> Response:
        try:
            content = _documentation_layout(USER_GUIDE)
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice error" role="alert">Documentation is temporarily unavailable.</div>'
        return _page(
            "User guide",
            "Learn what HATS shows, how to use each view and what the Web UI deliberately keeps hidden.",
            content,
            active="/docs",
        )

    def technical_documentation(request: Request) -> Response:
        page = technical_page(request.path_params["slug"])
        if page is None:
            return PlainTextResponse("Documentation page not found.", status_code=404, headers={"Cache-Control": "no-store"})
        try:
            content = _documentation_layout(page)
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice error" role="alert">Documentation is temporarily unavailable.</div>'
        return _page(page.title, page.description, content, active="/docs")

    def candidates_compatibility(_: Request) -> Response:
        return RedirectResponse("/tooling#candidates", status_code=307)

    def stylesheet(_: Request) -> Response:
        return PlainTextResponse(CSS, media_type="text/css", headers=_ASSET_HEADERS)

    def javascript(_: Request) -> Response:
        return PlainTextResponse(JAVASCRIPT, media_type="text/javascript", headers=_ASSET_HEADERS)

    def product_mark(_: Request) -> Response:
        return Response(HATS_MARK_SVG, media_type="image/svg+xml", headers=_ASSET_HEADERS)

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
            Route("/docs", documentation, methods=["GET"]),
            Route("/docs/technical/{slug}", technical_documentation, methods=["GET"]),
            Route("/candidates", candidates_compatibility, methods=["GET"]),
            Route("/assets/app.css", stylesheet, methods=["GET"]),
            Route("/assets/app.js", javascript, methods=["GET"]),
            Route("/assets/hats-mark.svg", product_mark, methods=["GET"]),
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
