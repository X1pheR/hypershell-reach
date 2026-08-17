from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="runner-local browser acceptance is executed by the dedicated CI job",
)

BASE_URL = os.environ.get("HATS_BROWSER_BASE_URL", "http://127.0.0.1:18081")


def test_hats_browser_desktop_shell(page: Page) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(BASE_URL)

    expect(page).to_have_title("Overview · HATS")
    expect(page.get_by_role("heading", name="Overview", exact=True)).to_be_visible()
    navigation = page.get_by_role("navigation", name="Primary navigation")
    expect(navigation).to_be_visible()
    expect(navigation.get_by_role("link", name="Targets", exact=True)).to_be_visible()
    expect(page.get_by_text("Read-only", exact=True).first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_hats_browser_mobile_navigation(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE_URL)

    menu = page.get_by_role("button", name="Open navigation")
    expect(menu).to_be_visible()
    menu.click()

    dialog = page.get_by_role("dialog", name="HATS navigation")
    expect(dialog).to_be_visible()
    dialog.get_by_role("link", name="Targets", exact=True).click()
    expect(page).to_have_title("Targets · HATS")
    expect(page.get_by_role("heading", name="Targets", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
