"""Smoke-test the web page in headless Chrome.

Serves web/ on a free local port with two data sets:

- tests/data/web, a synthetic tree with a non-leaf unit, a unit below it and
  a name that is not UTF-8, for regression checks of the page's semantics;
- web/data (if present), the real data written by dirhistory-generate: for
  every tree, in light and dark mode, it groups by one level, drills into
  the first group, hides a band, reloads from the URL hash and unhides it.

Fails on any failed check, console error or uncaught exception. Needs
`pip install playwright` and Chrome (no browser download: it drives the
installed one).

    venv/bin/python scripts/check_web.py [--chrome PATH] [--shots DIR]
"""

import argparse
import functools
import http.server
import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
FIXTURE = ROOT / "tests" / "data" / "web"
SETTLE_MS = 500  # time for fetch + render after a navigation or click


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web/, but /data/ from the directory given as data."""

    def __init__(self, *args, data: Path, **kwargs):
        self.data = data
        super().__init__(*args, directory=WEB, **kwargs)

    def translate_path(self, path: str) -> str:
        if path.startswith("/data/"):
            return str(self.data / path.removeprefix("/data/").split("?")[0])
        return super().translate_path(path)

    def log_message(self, *args) -> None:
        pass


def serve(data: Path) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Handler, data=data)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def url(server: http.server.ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def legend(page: Page) -> list[str]:
    return page.eval_on_selector_all("#legend li", "els => els.map(e => e.innerText)")


def check_fixture(page: Page, base: str) -> None:
    """Regression checks against tests/data/web."""
    page.goto(f"{base}#tree=99")
    page.wait_for_timeout(SETTLE_MS)
    assert page.is_visible("#error"), "no error shown for an unknown tree"
    page.evaluate("location.hash = 'tree=1'")
    page.wait_for_timeout(SETTLE_MS)
    assert page.is_hidden("#error"), "error still shown after a good load"

    # A dir that is not in the tree, even one named like an Object property,
    # falls back to ROOT.
    page.evaluate("location.hash = 'tree=1&in=constructor'")
    page.wait_for_timeout(SETTLE_MS)
    assert page.is_hidden("#error"), "in=constructor broke the page"
    assert page.inner_text("#crumbs") == "/fixture", "in=constructor not ignored"

    # Hiding a unit's own files must not hide the units below it.
    page.locator("#legend li", has_text="A (own files)").locator("button.hide").click()
    page.wait_for_timeout(SETTLE_MS)
    rows = legend(page)
    assert not any("A (own files)" in r for r in rows), "unit not hidden"
    assert any(r.startswith("A/b") for r in rows), "hiding A's own files hid A/b"
    page.locator("#hidden-list button").first.click()
    page.wait_for_timeout(SETTLE_MS)

    # Hiding a group hides all of its units.
    page.goto(f"{base}#tree=1&group=1")
    page.wait_for_timeout(SETTLE_MS)
    page.locator("#legend li", has_text="A/ (2 units)").locator("button.hide").click()
    page.wait_for_timeout(SETTLE_MS)
    assert not any(r.startswith("A") for r in legend(page)), "group not hidden"

    # The URL codec round-trips any name, including ones TextDecoder alters.
    # Names stay in the page: Playwright turns lone surrogates into U+FFFD.
    changed = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const names = ["caf\\uDCE9", "\\uFEFFbom", "a\\uFEFFb/\\uDCFF\\u00E9 +%"];
            const bad = names.filter((s) => m.decodeValue(m.encodeValue(s)) !== s);
            return JSON.stringify(bad);
        }"""
    )
    assert changed == "[]", f"URL codec changed names: {changed}"

    # Names that are not UTF-8: drilling and hiding survive the URL hash.
    page.goto(f"{base}#tree=1&group=1")
    page.wait_for_timeout(SETTLE_MS)
    page.locator("#legend li", has_text="caf").locator("button.link").click()
    page.wait_for_timeout(SETTLE_MS)
    assert "caf" in page.inner_text("#crumbs"), "drilling into a non-UTF-8 dir failed"
    page.locator("#legend li", has_text="x").locator("button.hide").click()
    page.wait_for_timeout(SETTLE_MS)
    page.reload()
    page.wait_for_timeout(SETTLE_MS)
    assert "caf" in page.inner_text("#crumbs"), "drill lost on reload"
    assert not any(r.startswith("x") for r in legend(page)), "hide lost on reload"


def slow_fetch(page: Page, file: str, ms: int = SETTLE_MS) -> None:
    """Make the page's fetches of data/FILE take MS longer."""
    page.add_init_script(f"""{{
        const fetch = window.fetch;
        window.fetch = async (url, opts) => {{
            const r = await fetch(url, opts);
            if (String(url).endsWith("/{file}")) {{
                await new Promise((ok) => setTimeout(ok, {ms}));
            }}
            return r;
        }};
    }}""")


def check_slow_tree(page: Page, base: str) -> None:
    """A tree file that arrives after a later tree's must not replace it."""
    slow_fetch(page, "tree-1.json")
    page.goto(f"{base}#tree=1")
    page.evaluate("location.hash = 'tree=2'")
    page.wait_for_timeout(3 * SETTLE_MS)
    assert page.inner_text("#crumbs") == "/fixture2", "a stale tree replaced tree 2"


def check_slow_index(page: Page, base: str) -> None:
    """A URL change while the tree index loads waits for it."""
    slow_fetch(page, "trees.json")
    page.goto(base)
    page.evaluate("location.hash = 'tree=2'")
    page.wait_for_timeout(3 * SETTLE_MS)
    assert page.is_hidden("#error"), "a URL change before the index loaded failed"
    assert page.inner_text("#crumbs") == "/fixture2", "tree 2 not shown"


def check_tree(page: Page, base: str, tree: int, shots: Path | None) -> None:
    """Generic checks of one tree of real data."""

    def state() -> str:
        return page.evaluate("location.hash")

    page.goto(f"{base}#tree={tree}&group=1")
    page.wait_for_timeout(SETTLE_MS)
    if shots:
        page.screenshot(path=shots / f"tree-{tree}.png")
    groups = page.locator("#legend button.link")
    if groups.count():
        groups.first.click()
        page.wait_for_timeout(SETTLE_MS)
        assert "in=" in state(), f"tree {tree}: drilling did not change the view"
    hide = page.locator("#legend button.hide")
    if hide.count():
        hide.first.click()
        page.wait_for_timeout(SETTLE_MS)
        assert "hide=" in state(), f"tree {tree}: hiding did not change the view"
        page.reload()
        page.wait_for_timeout(SETTLE_MS)
        assert page.locator("#hidden-list button").count() == 1, "hide not restored"
        page.locator("#hidden-list button").first.click()
        page.wait_for_timeout(SETTLE_MS)
        assert "hide=" not in state(), f"tree {tree}: unhiding failed"


def run_checks(
    browser: Browser, scheme: str, check: Callable[[Page], None], problems: list[str]
) -> None:
    page = browser.new_page(color_scheme=scheme)
    page.on("console", lambda m: m.type == "error" and problems.append(m.text))
    page.on("pageerror", lambda e: problems.append(str(e)))
    try:
        check(page)
    except AssertionError as e:
        problems.append(str(e))
    page.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--chrome", default="/usr/bin/google-chrome-stable")
    p.add_argument("--shots", type=Path, help="save a screenshot per real tree here")
    args = p.parse_args()

    real = WEB / "data" / "trees.json"
    trees = [t["id"] for t in json.loads(real.read_text())] if real.exists() else []
    problems: list[str] = []
    with (
        serve(FIXTURE) as fixture,
        serve(WEB / "data") as data,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch(executable_path=args.chrome)
        for check in (check_fixture, check_slow_tree, check_slow_index):
            run_checks(
                browser, "light", lambda pg, c=check: c(pg, url(fixture)), problems
            )
        for scheme in ("light", "dark"):
            for tree in trees:
                shots = args.shots if scheme == "light" else None
                run_checks(
                    browser,
                    scheme,
                    lambda pg, t=tree, s=shots: check_tree(pg, url(data), t, s),
                    problems,
                )
        browser.close()
        fixture.shutdown()
        data.shutdown()

    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    print(f"fixture and {len(trees)} real trees checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
