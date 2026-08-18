from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="runner-local browser acceptance is executed by the dedicated CI job",
)

BASE_URL = os.environ.get("HATS_BROWSER_BASE_URL", "http://127.0.0.1:18081")


@dataclass
class BrowserErrors:
    console: list[str] = field(default_factory=list)
    page: list[str] = field(default_factory=list)
    http: list[tuple[int, str]] = field(default_factory=list)


def _observe_page(page: Page) -> BrowserErrors:
    errors = BrowserErrors()
    page.on("console", lambda message: errors.console.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.page.append(str(error)))
    page.on(
        "response",
        lambda response: errors.http.append((response.status, response.url)) if response.status >= 400 else None,
    )
    return errors


def _assert_clean_browser(errors: BrowserErrors) -> None:
    assert errors.console == []
    assert errors.page == []
    assert errors.http == []


def _assert_wcag_a_aa(page: Page) -> None:
    from axe_playwright_python.sync_playwright import Axe

    results = Axe().run(
        page,
        options={
            "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa"]},
            "resultTypes": ["violations"],
        },
    )
    assert results.violations_count == 0, results.generate_report()


def test_hats_browser_desktop_shell(page: Page) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(BASE_URL, wait_until="networkidle")

    expect(page).to_have_title("Overview · HATS")
    expect(page.get_by_role("heading", name="Overview", exact=True)).to_be_visible()
    navigation = page.get_by_role("navigation", name="Primary navigation")
    expect(navigation).to_be_visible()
    expect(navigation.get_by_role("link", name="Targets", exact=True)).to_be_visible()
    expect(page.get_by_text("Read-only", exact=True).first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


def test_hats_browser_mobile_navigation(page: Page) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE_URL, wait_until="networkidle")

    menu = page.get_by_role("button", name="Open navigation")
    expect(menu).to_be_visible()
    menu.click()

    dialog = page.get_by_role("dialog", name="HATS navigation")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(menu).to_be_focused()

    menu.click()
    dialog.get_by_role("link", name="Targets", exact=True).click()
    expect(page).to_have_title("Targets · HATS")
    expect(page.get_by_role("heading", name="Targets", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)
