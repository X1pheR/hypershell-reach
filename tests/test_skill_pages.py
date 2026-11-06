"""Source-qualified Skill page, Markdown and filesystem abuse regression coverage."""
from __future__ import annotations

from pathlib import Path
from starlette.testclient import TestClient

from test_ui import _config
from hypershell_reach.ui import create_app


def _client(tmp_path: Path, *, large: bool = False) -> TestClient:
    config = _config(tmp_path)
    skill = tmp_path / "skills" / "example"
    (skill / "references").mkdir()
    (skill / "assets").mkdir()
    (skill / "references" / "guide.md").write_text(
        "# Guide\n\n![Image](../assets/icon.png)\n[Back](../SKILL.md) [Heading](../SKILL.md#usage)\n", encoding="utf-8"
    )
    (skill / "scripts").mkdir()
    (skill / "scripts" / "example.sh").write_text(
        'echo "<script>alert(1)</script>"\n', encoding="utf-8"
    )
    (skill / "assets" / "icon.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
    )
    text = (
        "---\nname: example\ndescription: Example skill.\nversion: 3.2.1\n---\n"
        "# Example\n\n## Usage\n\n| A | B |\n| --- | --- |\n| one | two |\n\n"
        "- [x] Done\n- [ ] Pending\n\n"
        "~~~python\nprint('hello')\n~~~\n\n"
        "~~~mermaid\nflowchart LR\n A-->B\n~~~\n\n"
        "[Guide](./references/guide.md) ![icon](assets/icon.png) [In this page](#usage)\n"
        "<script>alert('xss')</script>\n"
        "[Attack](javascript:alert(1)) [Escape](../secret.md)\n"
    )
    if large:
        text += "\n" + ("complete skill line\n" * 8100) + "\nTHE-FINAL-END-MARKER\n"
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    (tmp_path / "skills" / "secret.md").write_text("private", encoding="utf-8")
    return TestClient(create_app(config))


def test_skill_list_links_to_encoded_detail_and_metadata(tmp_path):
    client = _client(tmp_path)
    overview = client.get("/skills")
    assert overview.status_code == 200
    assert 'href="/skills/local%3Aexample"' in overview.text
    response = client.get("/skills/local%3Aexample")
    assert response.status_code == 200
    for item in ("Qualified ID", "local:example", "3.2.1", "Source type", "Original SKILL.md"):
        assert item in response.text
    assert 'href="/skills/local%3Aexample?file=references%2Fguide.md"' in response.text
    assert client.get("/skills/missing%3Aexample").status_code == 404
    assert client.get("/skills/local%3Amissing").status_code == 404
    assert client.post("/skills/local%3Aexample").status_code == 405


def test_skill_markdown_gfm_highlight_sanitizes_untrusted_html(tmp_path):
    html = _client(tmp_path).get("/skills/local%3Aexample").text
    assert "<table>" in html and "<th>A</th>" in html
    assert "task-list-item-checkbox" in html
    assert 'class="highlight"' in html
    assert 'class="language-python"' in html
    assert "&lt;script&gt;alert" in html
    assert "<script>alert(" not in html
    assert 'href="javascript:' not in html
    assert 'src="javascript:' not in html
    assert 'href="/skills/local%3Aexample?file=references%2Fguide.md"' in html
    assert 'href="#doc-usage"' in html
    assert 'src="/skills/local%3Aexample/assets/assets/icon.png"' in html
    assert 'href="../secret.md"' not in html


def test_supporting_files_and_images_are_package_scoped(tmp_path):
    client = _client(tmp_path)
    guide = client.get("/skills/local%3Aexample?file=references%2Fguide.md")
    assert guide.status_code == 200
    assert 'src="/skills/local%3Aexample/assets/assets/icon.png"' in guide.text
    assert 'href="/skills/local%3Aexample?file=SKILL.md"' in guide.text
    assert 'href="/skills/local%3Aexample?file=SKILL.md#doc-usage"' in guide.text
    assert 'href="/skills/local%3Aexample/files/references/guide.md"' in guide.text
    code = client.get("/skills/local%3Aexample?file=scripts%2Fexample.sh")
    assert code.status_code == 200
    assert "&lt;script&gt;" in code.text
    image = client.get("/skills/local%3Aexample/assets/assets/icon.png")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.headers["x-content-type-options"] == "nosniff"
    raw = client.get("/skills/local%3Aexample/files/scripts/example.sh")
    assert raw.status_code == 200
    assert raw.headers["content-disposition"].startswith("attachment;")
    assert raw.headers["content-type"].startswith("application/octet-stream")
    for path in [
        "/skills/local%3Aexample?file=..%2Fsecret.md",
        "/skills/local%3Aexample?file=assets%2Fmissing.png",
        "/skills/local%3Aexample/assets/..%2Fsecret.md",
        "/skills/local%3Aexample/files/..%2Fsecret.md",
        "/skills/local%3Aexample/assets/scripts/example.sh",
    ]:
        assert client.get(path).status_code in (400, 404)


def test_skill_page_does_not_truncate_at_mcp_chunk_limit(tmp_path):
    client = _client(tmp_path, large=True)
    response = client.get("/skills/local%3Aexample")
    assert response.status_code == 200
    assert "THE-FINAL-END-MARKER" in response.text
    assert "truncated" not in response.text.lower()



def test_skill_js_csp_is_scoped_and_assets_are_read_only(tmp_path):
    client = _client(tmp_path)
    skill = client.get("/skills/local%3Aexample")
    csp = skill.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "unsafe-eval" not in csp
    assert "style-src 'self';" in client.get("/skills").headers["content-security-policy"]
    for filename in ("mermaid-12.0.0.min.js", "dompurify-3.4.16.min.js", "skill-mermaid.js"):
        response = client.get("/assets/" + filename)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/assets/../../etc/passwd").status_code == 404


def test_symlinked_supporting_file_does_not_escape_skill_package(tmp_path):
    client = _client(tmp_path)
    skill_dir = tmp_path / "skills" / "example"
    (skill_dir / "references" / "indirect.md").symlink_to(tmp_path / "skills" / "secret.md")
    assert client.get("/skills/local%3Aexample?file=references%2Findirect.md").status_code == 404
    assert client.get("/skills/local%3Aexample/files/references/indirect.md").status_code == 404
