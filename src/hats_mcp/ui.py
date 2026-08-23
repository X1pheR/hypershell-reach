from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlencode

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


@dataclass(frozen=True)
class _Link:
    href: str
    label: str


def _brand() -> str:
    return """
<a class="brand" href="/" aria-label="Open HATS overview">
  <img class="brand-mark" src="/assets/hats-mark.svg" alt="" width="44" height="44">
  <span class="brand-copy"><strong>Hypershell</strong><small>HATS</small></span>
</a>
"""


def _nav_links(active: str | None) -> str:
    items: list[str] = []
    for label, href in _NAV:
        current = ' aria-current="page"' if href == active else ""
        items.append(f'<a href="{href}"{current}>{escape(label)}</a>')
    return "".join(items)


_CONTEXT_HELP = {
    "/": "/help",
    "/targets": "/help/technical/configuration",
    "/tooling": "/help/technical/tools",
    "/runs": "/help/technical/runs-and-tasks",
    "/tasks": "/help/technical/runs-and-tasks",
    "/skills": "/help/technical/skills",
}


def _context_help(active: str | None) -> str:
    href = _CONTEXT_HELP.get(active or "")
    if not href:
        return ""
    return (
        f'<a class="icon-button contextual-help" href="{href}" aria-label="About this page" '
        'data-tooltip="About this page"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">'
        '<circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.6 2.6 0 1 1 4.45 1.84c-.9.86-2.15 1.27-2.15 2.66"/>'
        '<path d="M12 17h.01"/></svg></a>'
    )


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
    <div class="header-actions">
      <a class="icon-button utility-help" href="/help" aria-label="Help" data-tooltip="Help"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.6 2.6 0 1 1 4.45 1.84c-.9.86-2.15 1.27-2.15 2.66"/><path d="M12 17h.01"/></svg></a>
      <span class="availability-badge" aria-label="Runtime availability: Available">Runtime available</span>
      <span class="mode-badge" aria-label="Mode: Read-only">Read-only</span>
    </div>
  </header>
  <main id="main-content" tabindex="-1">
    <div class="page-heading-row"><div><h1>{escape(title)}</h1><p class="page-summary">{escape(intro)}</p></div>{_context_help(active)}</div>
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
      <div class="mobile-utility-navigation">
        <a class="mobile-utility-link" href="/help"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.7 9a2.6 2.6 0 1 1 4.45 1.84c-.9.86-2.15 1.27-2.15 2.66"/><path d="M12 17h.01"/></svg><span>Help</span></a>
        <span class="availability-badge" aria-label="Runtime availability: Available">Runtime available</span>
        <span class="mode-badge" aria-label="Mode: Read-only">Read-only</span>
      </div>
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
    if isinstance(value, _Link):
        return f'<a href="{escape(value.href)}">{escape(value.label)}</a>'
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


def _table_query(request: Request, updates: dict[str, str | int | None]) -> str:
    params = dict(request.query_params)
    for key, value in updates.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = str(value)
    query = urlencode(params)
    return f"{request.url.path}?{query}" if query else request.url.path


def _search_value(value: Any) -> str:
    if isinstance(value, _Link):
        return value.label
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _table(
    columns: Sequence[tuple[str, str]],
    rows: Sequence[dict[str, Any]],
    *,
    request: Request | None = None,
    prefix: str = "",
    search_fields: Sequence[str] = (),
    sort_fields: Sequence[str] = (),
    filter_field: str | None = None,
    filter_label: str = "Filter",
    default_sort: str | None = None,
    default_desc: bool = False,
    page_size=25,
) -> str:
    source_rows = list(rows)
    if request is None or not (search_fields or sort_fields or filter_field):
        if not source_rows:
            return '<div class="data-region"><div class="empty">No entries.</div></div>'
        head = "".join(f'<th scope="col">{escape(label)}</th>' for _, label in columns)
        body = "".join(
            "<tr>" + "".join(
                f'<td data-label="{escape(label)}">{_value(key, row.get(key))}</td>' for key, label in columns
            ) + "</tr>"
            for row in source_rows
        )
        return f'<div class="data-region"><div class="table-wrap" tabindex="0"><table class="data-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>'

    name = lambda key: f"{prefix}_{key}" if prefix else key
    query = request.query_params.get(name("q"), "").strip()
    selected_filter = request.query_params.get(name("filter"), "").strip()
    requested_sort = request.query_params.get(name("sort"), default_sort or "").strip()
    sort_key = requested_sort if requested_sort in sort_fields else (default_sort if default_sort in sort_fields else "")
    direction = request.query_params.get(name("dir"), "desc" if default_desc else "asc").lower()
    descending = direction == "desc"
    try:
        page = max(1, int(request.query_params.get(name("page"), "1")))
    except ValueError:
        page = 1

    filtered = source_rows
    if query:
        needle = query.casefold()
        filtered = [
            row for row in filtered
            if any(needle in _search_value(row.get(field)).casefold() for field in search_fields)
        ]
    if filter_field and selected_filter:
        filtered = [row for row in filtered if _search_value(row.get(filter_field)) == selected_filter]
    if sort_key:
        filtered.sort(
            key=lambda row: (_search_value(row.get(sort_key)).casefold(), _search_value(row.get("id")).casefold()),
            reverse=descending,
        )

    total = len(filtered)
    max_page = max(1, (total + page_size - 1) // page_size)
    page = min(page, max_page)
    start = (page - 1) * page_size
    visible = filtered[start:start + page_size]

    controls: list[str] = ['<form class="table-controls" method="get">']
    if search_fields:
        controls.append(
            f'<label class="table-search"><span>Search</span><input type="search" name="{escape(name("q"))}" '
            f'value="{escape(query)}" placeholder="Search visible fields"></label>'
        )
    if filter_field:
        options = sorted({_search_value(row.get(filter_field)) for row in source_rows if _search_value(row.get(filter_field))})
        option_html = ['<option value="">All</option>']
        for value in options:
            selected = ' selected' if value == selected_filter else ''
            option_html.append(f'<option value="{escape(value)}"{selected}>{escape(value)}</option>')
        controls.append(
            f'<label class="table-filter"><span>{escape(filter_label)}</span><select name="{escape(name("filter"))}">'
            f'{"".join(option_html)}</select></label>'
        )
    if sort_key:
        controls.append(f'<input type="hidden" name="{escape(name("sort"))}" value="{escape(sort_key)}">')
        controls.append(f'<input type="hidden" name="{escape(name("dir"))}" value="{escape(direction)}">')
    controls.append('<button class="secondary-action" type="submit">Apply</button>')
    controls.append(f'<a class="button-link" href="{escape(request.url.path)}">Clear</a>')
    controls.append('</form>')

    head_cells: list[str] = []
    for key, label in columns:
        if key not in sort_fields:
            head_cells.append(f'<th scope="col">{escape(label)}</th>')
            continue
        active = key == sort_key
        next_direction = "asc" if active and descending else "desc" if active else "asc"
        aria_sort = ("descending" if descending else "ascending") if active else "none"
        arrow = "↓" if active and descending else "↑" if active else "↕"
        href = _table_query(
            request,
            {name("sort"): key, name("dir"): next_direction, name("page"): None},
        )
        head_cells.append(
            f'<th scope="col" aria-sort="{aria_sort}"><a class="sort-link" href="{escape(href)}">'
            f'{escape(label)} <span aria-hidden="true">{arrow}</span></a></th>'
        )

    if visible:
        body = "".join(
            "<tr>" + "".join(
                f'<td data-label="{escape(label)}">{_value(key, row.get(key))}</td>' for key, label in columns
            ) + "</tr>"
            for row in visible
        )
        table = (
            f'<div class="table-wrap" tabindex="0"><table class="data-table"><thead><tr>{"".join(head_cells)}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>'
        )
    else:
        empty = "No entries." if not source_rows else "No entries match the current filters."
        table = f'<div class="empty">{empty}</div>'

    context = f'{total} result' if total == 1 else f'{total} results'
    if total:
        context += f' · showing {start + 1}–{min(start + page_size, total)}'
    pagination = ""
    if max_page > 1:
        links = []
        if page > 1:
            links.append(f'<a class="button-link" href="{escape(_table_query(request, {name("page"): page - 1}))}">Previous</a>')
        links.append(f'<span>Page {page} of {max_page}</span>')
        if page < max_page:
            links.append(f'<a class="button-link" href="{escape(_table_query(request, {name("page"): page + 1}))}">Next</a>')
        pagination = f'<nav class="table-pagination" aria-label="Results pages">{"".join(links)}</nav>'

    return (
        f'{"".join(controls)}<div class="data-region"><p class="result-context" role="status">{escape(context)}</p>'
        f'{table}{pagination}</div>'
    )


def _safe_table(
    loader: Callable[[], list[dict[str, Any]]],
    columns: Sequence[tuple[str, str]],
    **table_options: Any,
) -> str:
    try:
        rows = loader()
    except (OSError, RuntimeError, ValueError):
        return '<div class="notice error" role="alert">This view is temporarily unavailable.</div>'
    return _table(columns, rows, **table_options)


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


def _details(items: Sequence[tuple[str, str, Any]]) -> str:
    body = "".join(
        f'<div class="detail-item"><dt>{escape(label)}</dt><dd>{_value(key, value)}</dd></div>'
        for key, label, value in items
    )
    return f'<dl class="detail-grid">{body}</dl>'


def _text_list(items: Sequence[Any], *, empty: str = "None recorded.") -> str:
    if not items:
        return f'<p class="muted">{escape(empty)}</p>'
    return '<ul class="detail-list">' + "".join(f'<li>{escape(str(item))}</li>' for item in items) + '</ul>'


def _mapping_table(rows: Sequence[dict[str, Any]], columns: Sequence[tuple[str, str]]) -> str:
    return _table(columns, rows)


def _error_page(title: str, message: str, *, status_code: int = 404) -> Response:
    return PlainTextResponse(message, status_code=status_code, headers={"Cache-Control": "no-store"})


def _docs_navigation(active_page: DocumentationPage) -> str:
    guide_current = ' aria-current="page"' if active_page.slug == USER_GUIDE.slug else ""
    parts = [
        '<p class="docs-nav-title">Guide</p>',
        '<div class="docs-nav-links">',
        f'<a href="/help"{guide_current}>User guide</a>',
        "</div>",
        '<p class="docs-nav-title">Technical reference</p>',
    ]
    for group, pages in documentation_groups():
        parts.append(f'<p class="docs-nav-group">{escape(group)}</p><div class="docs-nav-links">')
        for page in pages:
            current = ' aria-current="page"' if page.slug == active_page.slug else ""
            parts.append(f'<a href="{page.route}"{current}>{escape(page.title)}</a>')
        parts.append("</div>")
    navigation = f'<nav aria-label="Help sections">{"".join(parts)}</nav>'
    return (
        f'<aside class="docs-navigation docs-navigation-desktop">{navigation}</aside>'
        '<details class="docs-navigation docs-navigation-mobile">'
        '<summary>Help menu</summary>'
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

        help_card = _overview_card(
            "Help",
            "/help",
            "User guide + technical reference",
            "How to use, configure and maintain HATS.",
        )
        cards = "".join((targets_card, tooling_card, runs_card, tasks_card, skills_card, help_card))
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

    def _candidates_content(request: Request) -> str:
        try:
            configured, rows = model.candidates()
        except (OSError, RuntimeError, ValueError):
            return '<div class="notice error" role="alert">Tooling candidates are temporarily unavailable.</div>'
        notice = "" if configured else '<div class="notice">No tooling registry is configured.</div>'
        linked_rows: list[dict[str, Any]] = []
        for row in rows:
            linked = dict(row)
            if row.get("structured"):
                candidate_id = str(row["id"])
                linked["title"] = _Link(f"/candidates/{candidate_id}", str(row["title"]))
                linked["id"] = _Link(f"/candidates/{candidate_id}", candidate_id)
            linked_rows.append(linked)
        return notice + _table(
            (
                ("title", "Title"),
                ("status", "Status"),
                ("promotion_reason", "Promotion reason"),
                ("id", "ID"),
            ),
            linked_rows,
            request=request,
            prefix="candidates",
            search_fields=("title", "status", "promotion_reason", "id"),
            sort_fields=("title", "status", "id"),
            filter_field="status",
            filter_label="Status",
            default_sort="id",
        )

    def tooling(request: Request) -> Response:
        def managed_rows() -> list[dict[str, Any]]:
            rows = model.tooling()
            for row in rows:
                tool_id = str(row["id"])
                row["id"] = _Link(f"/tooling/{tool_id}", tool_id)
            return rows

        managed = _safe_table(
            managed_rows,
            (
                ("name", "Name"),
                ("domain", "Domain"),
                ("description", "Description"),
                ("source", "Source"),
                ("interpreter", "Interpreter"),
                ("requires", "Requires"),
                ("mutating", "Mutating"),
                ("idempotent", "Idempotent"),
                ("id", "ID"),
            ),
            request=request,
            prefix="tools",
            search_fields=("name", "domain", "description", "source", "interpreter", "requires", "id"),
            sort_fields=("name", "domain", "source", "interpreter", "mutating"),
            filter_field="domain",
            filter_label="Domain",
            default_sort="name",
        )
        content = '<div class="section-stack">' + _panel(
            "Managed tools",
            "Reviewed tools HATS can run. Source code and deployment paths stay hidden.",
            managed,
        ) + _panel(
            "Tooling candidates",
            "Recurring gaps that may justify reusable automation.",
            _candidates_content(request),
            element_id="candidates",
        ) + "</div>"
        return _page("Tooling", "See reusable tools and gaps being considered for automation.", content, active="/tooling")

    def runs(request: Request) -> Response:
        def run_rows() -> list[dict[str, Any]]:
            rows = model.run_summaries()
            for row in rows:
                run_id = str(row["id"])
                row["id"] = _Link(f"/runs/{run_id}", run_id)
                if row.get("task_id"):
                    task_id = str(row["task_id"])
                    row["task_id"] = _Link(f"/tasks/{task_id}", task_id)
            return rows

        content = _safe_table(
            run_rows,
            (
                ("id", "Run"),
                ("status", "Status"),
                ("operation", "Operation"),
                ("target", "Target"),
                ("started_at", "Started"),
                ("ended_at", "Ended"),
                ("task_id", "Task"),
                ("script_id", "Script"),
                ("ambiguous", "Ambiguous"),
                ("retained", "Retained"),
            ),
            request=request,
            search_fields=("id", "status", "operation", "target", "task_id", "script_id"),
            sort_fields=("started_at", "status", "operation", "target", "ended_at"),
            filter_field="status",
            filter_label="Status",
            default_sort="started_at",
            default_desc=True,
        )
        return _page(
            "Runs",
            "Recent HATS activity. Command text, arguments and output are not stored here.",
            _panel("Recent activity", "The latest HATS run records.", content),
            active="/runs",
        )

    def tasks(_: Request) -> Response:
        def task_rows() -> list[dict[str, Any]]:
            rows = model.task_summaries()
            for row in rows:
                task_id = str(row["id"])
                row["id"] = _Link(f"/tasks/{task_id}", task_id)
            return rows

        content = _safe_table(
            task_rows,
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

    def tool_detail(request: Request) -> Response:
        tool_id = request.path_params["tool_id"]
        try:
            record = model.tool(tool_id)
        except ValueError:
            return _error_page("Tool not found", "Tool not found.")
        except (OSError, RuntimeError):
            return _error_page("Tool unavailable", "Tool detail is temporarily unavailable.", status_code=503)
        arguments = list(record.get("arguments") or [])
        argument_rows = [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "required": item.get("required"),
                "description": item.get("description"),
            }
            for item in arguments
        ]
        content = '<div class="section-stack">' + _panel(
            "Tool contract",
            "Stable managed-tool identity and safe execution metadata.",
            _details(
                (
                    ("id", "ID", record.get("id")),
                    ("name", "Name", record.get("name")),
                    ("description", "Description", record.get("description")),
                    ("domain", "Domain", record.get("domain")),
                    ("source", "Source", record.get("source")),
                    ("interpreter", "Interpreter", record.get("interpreter")),
                    ("requires", "Requires", record.get("requires")),
                    ("mutating", "Mutating", record.get("mutating")),
                    ("idempotent", "Idempotent", record.get("idempotent")),
                    ("timeout_seconds", "Timeout (s)", record.get("timeout_seconds")),
                    ("sha256", "SHA-256", record.get("sha256")),
                    ("bytes", "Bytes", record.get("bytes")),
                )
            ),
        ) + _panel(
            "Arguments",
            "Declared argument names and types only. Runtime values are never shown here.",
            _mapping_table(argument_rows, (("name", "Name"), ("type", "Type"), ("required", "Required"), ("description", "Description"))),
        ) + '</div>'
        return _page(str(record.get("name") or tool_id), "Managed tool detail.", content, active="/tooling")

    def run_detail(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        try:
            record = model.run(run_id)
        except ValueError:
            return _error_page("Run not found", "Run not found.")
        except (OSError, RuntimeError):
            return _error_page("Run unavailable", "Run detail is temporarily unavailable.", status_code=503)

        purpose = record.get("purpose")
        purpose_content = (
            f'<p class="detail-prose">{escape(str(purpose))}</p>'
            if purpose
            else '<div class="notice">Purpose is unavailable for this historical run.</div>'
        )
        script_id = record.get("script_id")
        script_value: Any = (
            _Link(f"/tooling/{script_id}", str(script_id)) if script_id else None
        )
        task_id = record.get("task_id")
        task_value: Any = _Link(f"/tasks/{task_id}", str(task_id)) if task_id else None
        result_summary = record.get("result_summary")
        result_content = (
            f'<p class="detail-prose">{escape(str(result_summary))}</p>'
            if result_summary
            else '<div class="notice">A bounded result summary is unavailable for this historical run.</div>'
        )
        content = '<div class="section-stack">' + _panel(
            "Purpose",
            "Human-readable reason for the execution.",
            purpose_content,
        ) + _panel(
            "Execution",
            "Technical operation, target and associated continuity context.",
            _details(
                (
                    ("id", "Run", record.get("id")),
                    ("operation", "Operation", record.get("operation")),
                    ("script_id", "Managed tool", script_value),
                    ("target", "Target", record.get("target")),
                    ("status", "Status", record.get("status")),
                    ("task_id", "Task", task_value),
                    ("started_at", "Started", record.get("started_at")),
                    ("ended_at", "Ended", record.get("ended_at")),
                )
            ),
        ) + _panel(
            "Result summary",
            "Bounded server-generated outcome. Raw command, arguments and output content are not persisted.",
            result_content,
        ) + _panel(
            "Diagnostics",
            "Bounded secret-safe execution metadata only.",
            _details(
                (
                    ("timeout_seconds", "Timeout (s)", record.get("timeout_seconds")),
                    ("exit_code", "Exit code", record.get("exit_code")),
                    ("timed_out", "Timed out", record.get("timed_out")),
                    ("duration_ms", "Duration (ms)", record.get("duration_ms")),
                    ("stdout_bytes", "Stdout bytes", record.get("stdout_bytes")),
                    ("stderr_bytes", "Stderr bytes", record.get("stderr_bytes")),
                    ("stdout_truncated", "Stdout truncated", record.get("stdout_truncated")),
                    ("stderr_truncated", "Stderr truncated", record.get("stderr_truncated")),
                    ("error_type", "Error type", record.get("error_type")),
                    ("ambiguous", "Ambiguous outcome", record.get("ambiguous")),
                    ("may_mutate", "May mutate", record.get("may_mutate")),
                    ("idempotent", "Idempotent", record.get("idempotent")),
                    ("retained", "Retained", record.get("retained")),
                )
            ),
        ) + '</div>'
        return _page(run_id, "Execution detail without raw command, argument values or output content.", content, active="/runs")

    def task_detail(request: Request) -> Response:
        task_id = request.path_params["task_id"]
        try:
            record = model.task(task_id)
            related = model.related_run_summaries(task_id)
        except ValueError:
            return _error_page("Task not found", "Task not found.")
        except (OSError, RuntimeError):
            return _error_page("Task unavailable", "Task detail is temporarily unavailable.", status_code=503)

        continuity = record.get("continuity") or {}
        related_rows: list[dict[str, Any]] = []
        for run in related:
            row = dict(run)
            run_id = str(row["id"])
            row["id"] = _Link(f"/runs/{run_id}", run_id)
            row["purpose"] = row.get("purpose") or "Purpose unavailable for historical run."
            related_rows.append(row)

        source_rows = list(continuity.get("sources") or [])
        assumption_rows = list(continuity.get("assumptions") or [])
        content = '<div class="section-stack">' + _panel(
            "Task record",
            "Full safe continuity identity and current lifecycle state.",
            _details(
                (
                    ("id", "Task", record.get("id")),
                    ("schema_version", "Schema version", record.get("schema_version")),
                    ("revision", "Revision", record.get("revision")),
                    ("title", "Title", record.get("title")),
                    ("objective", "Objective", record.get("objective")),
                    ("project_ref", "Project reference", record.get("project_ref")),
                    ("status", "Status", record.get("status")),
                    ("next_action", "Next action", record.get("next_action")),
                    ("retained", "Retained", record.get("retained")),
                    ("created_at", "Created", record.get("created_at")),
                    ("updated_at", "Updated", record.get("updated_at")),
                    ("archived_at", "Archived", record.get("archived_at")),
                )
            ),
        ) + _panel(
            "Authorization and recovery",
            "Operator boundary and recovery information preserved with the Task.",
            _details(
                (
                    ("authorization", "Authorization", continuity.get("authorization")),
                    ("recovery", "Recovery", continuity.get("recovery")),
                )
            ),
        ) + _panel(
            "Sources",
            "Evidence references that informed the continuity record.",
            _mapping_table(source_rows, (("classification", "Classification"), ("reference", "Reference"), ("purpose", "Purpose"))),
        ) + _panel(
            "Completed",
            "Work already completed for this Task.",
            _text_list(continuity.get("completed") or []),
        ) + _panel(
            "Validation",
            "Acceptance and validation already performed.",
            _text_list(continuity.get("validation") or []),
        ) + _panel(
            "Cleanup",
            "Cleanup actions already completed.",
            _text_list(continuity.get("cleanup") or []),
        ) + _panel(
            "Blockers",
            "Current blockers preserved for continuation.",
            _text_list(continuity.get("blockers") or []),
        ) + _panel(
            "Assumptions",
            "Explicit assumptions and their impact if wrong.",
            _mapping_table(assumption_rows, (("statement", "Statement"), ("evidence_class", "Evidence"), ("impact_if_wrong", "Impact"), ("decision", "Decision"))),
        ) + _panel(
            "Related Runs",
            "Runs are derived exclusively from Run.task_id; the Task stores no backlink list.",
            _table(
                (("purpose", "Purpose"), ("status", "Status"), ("operation", "Operation"), ("target", "Target"), ("started_at", "Started"), ("id", "Run")),
                related_rows,
            ),
        ) + '</div>'
        return _page(str(record.get("title") or task_id), "Task continuity detail and purpose-first related execution history.", content, active="/tasks")

    def candidate_detail(request: Request) -> Response:
        candidate_id = request.path_params["candidate_id"]
        try:
            record = model.candidate(candidate_id)
        except ValueError:
            return _error_page("Candidate not found", "Structured Candidate detail is not available.")
        except (OSError, RuntimeError):
            return _error_page("Candidate unavailable", "Candidate detail is temporarily unavailable.", status_code=503)

        problem = record["problem"]
        proposal = record["proposal"]
        safety = proposal["safety"]
        ownership = record["ownership"]
        promotion = record["promotion"]
        implementation = record.get("implementation") or {}
        task_id = implementation.get("task_id")
        task_value: Any = _Link(f"/tasks/{task_id}", str(task_id)) if task_id else None
        final_reference = implementation.get("final_reference")
        final_value: Any = None
        if final_reference:
            reference_id = str(final_reference.get("id"))
            if final_reference.get("kind") == "managed-tool":
                final_value = _Link(f"/tooling/{reference_id}", reference_id)
            else:
                final_value = reference_id

        content = '<div class="section-stack">' + _panel(
            "Problem",
            "The recurring bounded gap this Candidate is intended to address.",
            _details(
                (
                    ("summary", "Summary", problem.get("summary")),
                    ("cause", "Cause", problem.get("cause")),
                    ("recurrence", "Recurrence", problem.get("recurrence")),
                )
            ) + '<h3>Evidence</h3>' + _text_list(problem.get("evidence") or []),
        ) + _panel(
            "Proposal",
            "The proposed reusable capability and typed interface.",
            _details(
                (
                    ("capability", "Capability", proposal.get("capability")),
                    ("proposed_tool_id", "Proposed tool ID", proposal.get("proposed_tool_id")),
                )
            ) + '<h3>Required inputs</h3>' + _mapping_table(proposal.get("required_inputs") or [], (("name", "Name"), ("description", "Description"))) + '<h3>Expected outputs</h3>' + _mapping_table(proposal.get("expected_outputs") or [], (("name", "Name"), ("description", "Description"))),
        ) + _panel(
            "Safety",
            "Declared execution and secret-access boundary.",
            _details(
                (
                    ("mutating", "Mutating", safety.get("mutating")),
                    ("secret_access", "Secret access", safety.get("secret_access")),
                    ("boundary", "Boundary", safety.get("boundary")),
                )
            ),
        ) + _panel(
            "Ownership and lifecycle",
            "Stable ownership, promotion state and implementation references.",
            _details(
                (
                    ("owner_id", "Owner", ownership.get("owner_id")),
                    ("status", "State", promotion.get("state")),
                    ("rationale", "Promotion rationale", promotion.get("rationale")),
                    ("state_reason", "State reason", promotion.get("state_reason")),
                    ("task_id", "Implementation Task", task_value),
                    ("final_reference", "Final managed capability", final_value),
                    ("revision", "Revision", record.get("revision")),
                    ("created_at", "Created", record.get("created_at")),
                    ("updated_at", "Updated", record.get("updated_at")),
                )
            ),
        ) + _panel(
            "Acceptance contract",
            "Conditions that must hold before the Candidate can be accepted as implemented or automated.",
            _text_list(proposal.get("acceptance") or []),
        ) + '</div>'
        return _page(str(record.get("title") or candidate_id), "Structured Candidate proposal, safety, ownership and acceptance contract.", content, active="/tooling")

    def skills(request: Request) -> Response:
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
                        ("name", "Name"),
                        ("category", "Category"),
                        ("source", "Source"),
                        ("description", "Description"),
                        ("provenance", "Origin"),
                    ),
                    rows,
                    request=request,
                    search_fields=("name", "description", "category", "source", "provenance"),
                    sort_fields=("name", "category", "source", "provenance"),
                    filter_field="category",
                    filter_label="Category",
                    default_sort="name",
                )
            content = "".join(notices) + table
        return _page(
            "Skills",
            "Agent Skills available from configured sources.",
            _panel("Available skills", "Skills HATS can read from configured sources.", content),
            active="/skills",
        )

    def help_page(_: Request) -> Response:
        try:
            content = _documentation_layout(USER_GUIDE)
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice error" role="alert">Help is temporarily unavailable.</div>'
        return _page(
            "Help",
            "Learn what HATS shows, how to use each view and what the Web UI deliberately keeps hidden.",
            content,
        )

    def technical_help(request: Request) -> Response:
        page = technical_page(request.path_params["slug"])
        if page is None:
            return PlainTextResponse("Help page not found.", status_code=404, headers={"Cache-Control": "no-store"})
        try:
            content = _documentation_layout(page)
        except (OSError, RuntimeError, ValueError):
            content = '<div class="notice error" role="alert">Help is temporarily unavailable.</div>'
        return _page(page.title, page.description, content)

    def docs_compatibility(_: Request) -> Response:
        return RedirectResponse("/help", status_code=307)

    def docs_technical_compatibility(request: Request) -> Response:
        return RedirectResponse(f'/help/technical/{request.path_params["slug"]}', status_code=307)

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
            Route("/tooling/{tool_id}", tool_detail, methods=["GET"]),
            Route("/runs", runs, methods=["GET"]),
            Route("/runs/{run_id}", run_detail, methods=["GET"]),
            Route("/tasks", tasks, methods=["GET"]),
            Route("/tasks/{task_id}", task_detail, methods=["GET"]),
            Route("/candidates/{candidate_id}", candidate_detail, methods=["GET"]),
            Route("/skills", skills, methods=["GET"]),
            Route("/help", help_page, methods=["GET"]),
            Route("/help/technical/{slug}", technical_help, methods=["GET"]),
            Route("/docs", docs_compatibility, methods=["GET"]),
            Route("/docs/technical/{slug}", docs_technical_compatibility, methods=["GET"]),
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
