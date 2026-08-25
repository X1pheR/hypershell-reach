from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="canonical browser acceptance is executed by the dedicated CI job",
)

BASE_URL = os.environ.get("REACH_BROWSER_BASE_URL", "http://127.0.0.1:18081")


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


def test_reach_browser_desktop_shell(page: Page) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(BASE_URL, wait_until="networkidle")

    expect(page).to_have_title("Overview · Hypershell Reach")
    expect(page.get_by_role("heading", name="Overview", exact=True)).to_be_visible()
    navigation = page.get_by_role("navigation", name="Primary navigation")
    expect(navigation).to_be_visible()
    expect(navigation.get_by_role("link", name="Targets", exact=True)).to_be_visible()
    expect(page.get_by_text("Runtime available", exact=True).first).to_be_visible()
    expect(page.get_by_text("Read-only", exact=True).first).to_be_visible()
    expect(page.get_by_role("link", name="Help", exact=True).first).to_have_attribute("href", "/help")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


@pytest.mark.parametrize("width,height", [(320, 800), (360, 800), (390, 844)])
def test_family_mobile_reflow_has_no_page_level_horizontal_overflow(page: Page, width: int, height: int) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE_URL, wait_until="networkidle")

    expect(page.get_by_role("button", name="Open navigation")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


def test_header_progress_marks_internal_navigation_busy(page: Page) -> None:
    errors = _observe_page(page)
    page.goto(BASE_URL, wait_until="networkidle")

    expect(page.locator(".navigation-progress")).to_be_attached()
    assert page.locator("html").get_attribute("class") in (None, "")
    assert page.locator("body").get_attribute("aria-busy") is None

    page.evaluate(
        """() => {
          document.addEventListener('click', (event) => event.preventDefault(), { once: true });
          document.querySelector('a[href=\"/skills\"]').click();
        }"""
    )
    expect(page.locator("html")).to_have_class("is-navigating")
    expect(page.locator("body")).to_have_attribute("aria-busy", "true")

    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow'))")
    expect(page.locator("html")).not_to_have_class("is-navigating")
    assert page.locator("body").get_attribute("aria-busy") is None

    _assert_clean_browser(errors)


def test_reach_browser_mobile_navigation(page: Page) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE_URL, wait_until="networkidle")

    menu = page.get_by_role("button", name="Open navigation")
    expect(menu).to_be_visible()
    menu.click()

    dialog = page.get_by_role("dialog", name="Hypershell Reach navigation")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(menu).to_be_focused()

    menu.click()
    dialog.get_by_role("link", name="Targets", exact=True).click()
    expect(page).to_have_title("Targets · Hypershell Reach")
    expect(page.get_by_role("heading", name="Targets", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


def test_help_is_utility_destination_and_growing_tables_expose_discovery_controls(page: Page) -> None:
    errors = _observe_page(page)
    page.goto(f"{BASE_URL}/tooling", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Tooling", exact=True)).to_be_visible()
    expect(page.get_by_role("searchbox", name="Search").first).to_be_visible()
    expect(page.get_by_role("combobox", name="Domain")).to_be_visible()
    expect(page.locator("table.data-table").first).to_be_visible()

    help_link = page.get_by_role("link", name="Help", exact=True).first
    help_link.click()
    expect(page).to_have_url(f"{BASE_URL}/help")
    expect(page.get_by_role("heading", name="Help", exact=True, level=1)).to_be_visible()
    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


def test_growing_table_reflows_as_semantic_records_on_mobile(page: Page) -> None:
    errors = _observe_page(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{BASE_URL}/tooling", wait_until="networkidle")
    first_cell = page.locator("table.data-table tbody td").first
    expect(first_cell).to_be_visible()
    assert first_cell.evaluate("node => getComputedStyle(node).display") == "grid"
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)


def test_wp6_read_only_provenance_detail_navigation(page: Page) -> None:
    errors = _observe_page(page)
    task_id = "task-20260820T120000000000Z-abcdef123456"
    run_id = "run-20260820T120100000000Z-123456abcdef"

    page.goto(f"{BASE_URL}/tasks/{task_id}", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Browser WP6 continuity fixture", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Related Runs", exact=True)).to_be_visible()
    run_link = page.get_by_role("link", name=run_id, exact=True)
    expect(run_link).to_have_attribute("href", f"/runs/{run_id}")
    expect(page.get_by_text("Verify exact read-only browser provenance navigation.", exact=True)).to_be_visible()
    assert page.locator('form[method="post"], form[method="POST"]').count() == 0

    run_link.click()
    expect(page).to_have_url(f"{BASE_URL}/runs/{run_id}")
    expect(page.get_by_role("heading", name="Purpose", exact=True)).to_be_visible()
    task_link = page.get_by_role("link", name=task_id, exact=True)
    expect(task_link).to_have_attribute("href", f"/tasks/{task_id}")
    expect(page.get_by_role("link", name="system.inspect", exact=True)).to_have_attribute(
        "href", "/tooling/system.inspect"
    )
    expect(page.get_by_role("heading", name="Diagnostics", exact=True)).to_be_visible()
    assert page.locator('form[method="post"], form[method="POST"]').count() == 0

    page.goto(f"{BASE_URL}/candidates/ATR-999", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Browser WP6 structured candidate", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Problem", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Acceptance contract", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=task_id, exact=True)).to_have_attribute("href", f"/tasks/{task_id}")
    expect(page.get_by_role("link", name="system.inspect", exact=True)).to_have_attribute(
        "href", "/tooling/system.inspect"
    )
    assert page.locator('form[method="post"], form[method="POST"]').count() == 0
    assert page.locator('button:has-text("Create"), button:has-text("Update"), button:has-text("Approve"), button:has-text("Complete")').count() == 0

    _assert_wcag_a_aa(page)
    _assert_clean_browser(errors)
