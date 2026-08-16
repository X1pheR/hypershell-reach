from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass(frozen=True)
class DocumentationPage:
    slug: str
    title: str
    description: str
    filename: str
    group: str

    @property
    def route(self) -> str:
        if self.slug == "user-guide":
            return "/docs"
        return f"/docs/technical/{self.slug}"


@dataclass(frozen=True)
class TocItem:
    level: int
    title: str
    anchor: str


@dataclass(frozen=True)
class RenderedDocument:
    title: str
    html: str
    toc: tuple[TocItem, ...]


USER_GUIDE = DocumentationPage(
    slug="user-guide",
    title="User guide",
    description="Understand the HATS UI and what each read-only view means.",
    filename="user-guide.md",
    group="Guide",
)

TECHNICAL_DOCS = (
    DocumentationPage("architecture", "Architecture", "Product boundaries and module structure.", "architecture.md", "Product"),
    DocumentationPage("security", "Security", "Execution, authorization and secret-handling boundaries.", "security.md", "Product"),
    DocumentationPage("installation", "Installation", "Install and upgrade an immutable HATS release.", "installation.md", "Operate"),
    DocumentationPage("configuration", "Configuration", "Configure workspaces, targets, tooling and skills.", "configuration.md", "Operate"),
    DocumentationPage("mcpjungle", "MCPJungle", "Optional MCPJungle deployment guidance.", "mcpjungle.md", "Operate"),
    DocumentationPage("runs-and-tasks", "Runs and tasks", "Execution evidence and continuity state.", "runs-and-tasks.md", "Reference"),
    DocumentationPage("skills", "Skills", "Agent Skills sources, discovery and retrieval.", "skills.md", "Reference"),
    DocumentationPage("tools", "Tool contracts", "Managed-tool contracts and bundled tools.", "tools.md", "Reference"),
    DocumentationPage("tooling-lifecycle", "Tooling lifecycle", "How recurring gaps become maintained tooling.", "tooling-lifecycle.md", "Reference"),
    DocumentationPage("web-ui", "Web UI", "The optional read-only HTTP runtime.", "web-ui.md", "Reference"),
    DocumentationPage("development", "Development", "Repository setup, tests and source structure.", "development.md", "Maintain"),
    DocumentationPage("releasing", "Release lifecycle", "Versioning, tagging and release acceptance.", "releasing.md", "Maintain"),
)

_TECHNICAL_BY_SLUG = {page.slug: page for page in TECHNICAL_DOCS}
_PAGE_BY_FILENAME = {page.filename: page for page in (USER_GUIDE, *TECHNICAL_DOCS)}
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def technical_page(slug: str) -> DocumentationPage | None:
    return _TECHNICAL_BY_SLUG.get(slug)


def documentation_groups() -> tuple[tuple[str, tuple[DocumentationPage, ...]], ...]:
    groups: list[tuple[str, tuple[DocumentationPage, ...]]] = []
    for group in ("Product", "Operate", "Reference", "Maintain"):
        pages = tuple(page for page in TECHNICAL_DOCS if page.group == group)
        groups.append((group, pages))
    return tuple(groups)


def _docs_root() -> Path:
    packaged = Path(__file__).resolve().with_name("_docs")
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[2] / "docs"
    if checkout.is_dir():
        return checkout
    raise FileNotFoundError("HATS documentation files are unavailable")


def _slug(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", value.lower()).strip("-") or "section"
    return f"doc-{normalized}"


def _rewrite_href(href: str) -> str:
    if href.startswith("#"):
        return f"#{_slug(href[1:])}"

    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return href

    path = parsed.path
    if not path.endswith(".md"):
        return href

    page = _PAGE_BY_FILENAME.get(Path(path).name)
    if page is None:
        return href

    target = page.route
    if parsed.fragment:
        target = f"{target}#{_slug(parsed.fragment)}"
    return target


def _set_attr(token: Token, key: str, value: str) -> None:
    if token.attrs is None:
        token.attrs = {}
    token.attrs[key] = value


def render_document(page: DocumentationPage) -> RenderedDocument:
    source = (_docs_root() / page.filename).read_text(encoding="utf-8")
    markdown = MarkdownIt("js-default")
    tokens = markdown.parse(source)

    title = page.title
    if (
        len(tokens) >= 3
        and tokens[0].type == "heading_open"
        and tokens[0].tag == "h1"
        and tokens[1].type == "inline"
        and tokens[2].type == "heading_close"
    ):
        title = tokens[1].content.strip() or page.title
        del tokens[:3]

    toc: list[TocItem] = []
    used_anchors: dict[str, int] = {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in {"h2", "h3"} and index + 1 < len(tokens):
            inline = tokens[index + 1]
            if inline.type == "inline":
                heading = inline.content.strip()
                base = _slug(heading)
                count = used_anchors.get(base, 0) + 1
                used_anchors[base] = count
                anchor = base if count == 1 else f"{base}-{count}"
                _set_attr(token, "id", anchor)
                toc.append(TocItem(level=int(token.tag[1]), title=heading, anchor=anchor))

        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type != "link_open" or child.attrs is None:
                continue
            href = child.attrs.get("href")
            if href:
                child.attrs["href"] = _rewrite_href(href)

    return RenderedDocument(
        title=title,
        html=markdown.renderer.render(tokens, markdown.options, {}),
        toc=tuple(toc),
    )
