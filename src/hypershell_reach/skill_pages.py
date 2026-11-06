"""Read-only, source-qualified Skill document rendering.

Markdown is parsed by markdown-it-py (the Python implementation of the same
markdown-it family used by HomeSight); task lists and highlighting use
maintained plugins/libraries, never handwritten Markdown parsing.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from .skills import SkillPackage, _parse_frontmatter, _resolve_file, list_skill_files
from .ui_docs import TocItem

_SAFE_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_SAFE_LINK_SCHEMES = {"http", "https", "mailto"}


@dataclass(frozen=True)
class SkillDocument:
    html: str
    toc: tuple[TocItem, ...]
    original: str
    path: str
    files: tuple[str, ...]


def skill_url(skill_id: str) -> str:
    return "/skills/" + quote(skill_id, safe="")


def file_url(skill_id: str, relative_path: str) -> str:
    return skill_url(skill_id) + "?file=" + quote(relative_path, safe="")


def download_url(skill_id: str, relative_path: str) -> str:
    return skill_url(skill_id) + "/files/" + "/".join(quote(part, safe="") for part in relative_path.split("/"))


def asset_url(skill_id: str, relative_path: str) -> str:
    return skill_url(skill_id) + "/assets/" + "/".join(quote(p, safe="") for p in relative_path.split("/"))


def _resolve_reference(href: str, current: str, manifest: set[str], *, image: bool = False) -> str | None:
    # All potentially active markup is escaped by markdown-it's html=False.
    # File access is *additionally* limited to the enumerated Skill manifest.
    if not href or "\\" in href or href.startswith("//"):
        return None
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        if image or parsed.scheme.lower() not in _SAFE_LINK_SCHEMES:
            return None
        return href
    if parsed.path == "" and parsed.fragment:
        from .ui_docs import _slug
        return "#" + _slug(unquote(parsed.fragment))
    path = unquote(parsed.path)
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return None
    if any(part == "" for part in path.split("/")):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(current), path))
    if target.startswith("../") or target not in manifest:
        return None
    if image:
        if PurePosixPath(target).suffix.lower() not in _SAFE_IMAGES:
            return None
        return asset_url(_CURRENT_SKILL_ID.get(), target)  # set during this render
    result = file_url(_CURRENT_SKILL_ID.get(), target)
    if parsed.fragment:
        from .ui_docs import _slug
        result += "#" + _slug(unquote(parsed.fragment))
    return result


# A context variable lets one markdown-it render rule use the same Skill
# ID without global mutable cross-request state.
from contextvars import ContextVar
_CURRENT_SKILL_ID: ContextVar[str] = ContextVar("skill_id")


def _fence(renderer, tokens, index, options, env) -> str:
    token = tokens[index]
    language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else ""
    if language.lower() == "mermaid":
        # Reach has no active Mermaid browser engine yet; do not execute
        # untrusted Mermaid code or pretend the diagram was rendered.
        return '<pre class="skill-mermaid-fallback"><code class="language-mermaid">' + escape(token.content) + "</code></pre>\n"
    try:
        lexer = get_lexer_by_name(language) if language else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    formatted = highlight(token.content, lexer, HtmlFormatter(nowrap=True))
    return '<pre class="highlight"><code class="language-' + escape(language, quote=True) + '">' + formatted + "</code></pre>\n"


def render_skill_document(skill: SkillPackage, relative_path: str = "SKILL.md") -> SkillDocument:
    manifest = {str(item["path"]) for item in list_skill_files(skill)}
    if relative_path not in manifest:
        raise ValueError("skill file is not in the package manifest")
    path = _resolve_file(skill, relative_path)
    if path.stat().st_size > 1_048_576:
        raise ValueError("text document exceeds safe page size")
    original = path.read_text(encoding="utf-8")
    body = _parse_frontmatter(original)[1] if relative_path == "SKILL.md" else original

    md = MarkdownIt("js-default", {"html": False, "linkify": False, "typographer": False})
    md.use(tasklists_plugin, enabled=True, label=False)
    md.add_render_rule("fence", _fence)
    tokens = md.parse(body)
    toc: list[TocItem] = []
    used: dict[str, int] = {}
    from .ui_docs import _slug, _set_attr

    context = _CURRENT_SKILL_ID.set(skill.id)
    try:
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and index + 1 < len(tokens):
                inline = tokens[index + 1]
                if inline.type == "inline":
                    title = inline.content.strip()
                    anchor = _slug(title)
                    used[anchor] = used.get(anchor, 0) + 1
                    if used[anchor] > 1:
                        anchor += "-" + str(used[anchor])
                    _set_attr(token, "id", anchor)
                    if token.tag in {"h2", "h3"}:
                        toc.append(TocItem(int(token.tag[1]), title, anchor))

            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type not in {"link_open", "image"}:
                    continue
                key = "href" if child.type == "link_open" else "src"
                href = (child.attrs or {}).get(key)
                if href is None:
                    continue
                safe = _resolve_reference(href, relative_path, manifest, image=child.type == "image")
                # Never fall back to an unsafe or out-of-package URL.
                child.attrSet(key, safe or "#")
        html = md.renderer.render(tokens, md.options, {})
    finally:
        _CURRENT_SKILL_ID.reset(context)

    return SkillDocument(html=html, toc=tuple(toc), original=original, path=relative_path, files=tuple(sorted(manifest)))


def read_skill_image(skill: SkillPackage, relative_path: str) -> tuple[bytes, str]:
    from mimetypes import guess_type
    if relative_path not in {str(item["path"]) for item in list_skill_files(skill)}:
        raise ValueError("skill asset is not in the package manifest")
    if PurePosixPath(relative_path).suffix.lower() not in _SAFE_IMAGES:
        raise ValueError("unsupported inline image type")
    path = _resolve_file(skill, relative_path)
    return path.read_bytes(), guess_type(path.name)[0] or "application/octet-stream"
