"""Smoke-test the web page in headless Chrome against the data in web/data.

Serves web/ on a free local port, then for every tree: loads the page, groups
by one level, drills into the first group, hides and unhides a band, and
reloads from the URL hash. Fails on any console error or uncaught exception.
Needs `pip install playwright` and Chrome (no browser download: it drives
the installed one); run dirhistory-generate first.

    venv/bin/python scripts/check_web.py [--chrome PATH] [--shots DIR]
"""

import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

WEB = Path(__file__).resolve().parent.parent / "web"
SETTLE_MS = 500  # time for fetch + render after a navigation or click


def check(page: Page, base: str, tree: int, shots: Path | None) -> None:
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


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--chrome", default="/usr/bin/google-chrome-stable")
    p.add_argument("--shots", type=Path, help="save a screenshot per tree here")
    args = p.parse_args()

    trees = [t["id"] for t in json.loads((WEB / "data" / "trees.json").read_text())]
    handler = functools.partial(QuietHandler, directory=WEB)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"

    problems: list[str] = []
    with server, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome)
        for scheme in ("light", "dark"):
            page = browser.new_page(color_scheme=scheme)
            page.on("console", lambda m: m.type == "error" and problems.append(m.text))
            page.on("pageerror", lambda e: problems.append(str(e)))
            for tree in trees:
                try:
                    check(page, base, tree, args.shots if scheme == "light" else None)
                except AssertionError as e:
                    problems.append(str(e))
            page.close()
        browser.close()
        server.shutdown()

    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    print(f"{len(trees)} trees checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
