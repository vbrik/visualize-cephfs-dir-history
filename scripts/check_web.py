"""Smoke-test the web page in headless Chrome.

Serves web/ on a free local port with two data sets:

- tests/data/web, two synthetic trees (the first with a non-leaf unit, a
  unit below it and a name that is not UTF-8), for regression checks of
  the page's semantics, URL codec, update ordering, data refresh, click
  hit-testing and the Watched view (tree 1 watches three dirs, tree 2
  none); some checks serve altered copies of them;
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
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Browser, Locator, Page, sync_playwright

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


def check_model(page: Page, base: str) -> None:
    """Pure model functions, called in the page."""
    page.goto(base)
    page.wait_for_timeout(SETTLE_MS)
    cards = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const data = {times: ["t0", "t1", "t2"], watched: ["a", "gone", "flat"],
              rbytes: {a: [10, 30, 20], gone: [5, null, null], flat: [7, 7, 7]}};
            return m.trendCards(data, [0, 2]);
        }"""
    )
    assert [c["path"] for c in cards] == ["a", "gone", "flat"], "cards out of order"
    a, gone, flat = cards
    assert (a["start"], a["end"], a["change"]) == (10, 20, 10), f"change: {a}"
    assert (a["lo"], a["hi"]) == (10, 30), f"scale: {a}"
    assert (gone["end"], gone["change"]) == (0, -5), f"absent end: {gone}"
    assert flat["lo"] == flat["hi"] == 7, f"flat scale: {flat}"
    zoomed = page.evaluate(
        """async () => (await import("./js/model.js")).trendCards(
            {times: ["t0", "t1", "t2"], watched: ["a"], rbytes: {a: [10, 30, 20]}},
            [1, 2])"""
    )
    assert (zoomed[0]["lo"], zoomed[0]["change"]) == (20, -10), "scale not by range"
    absent = page.evaluate(
        """async () => (await import("./js/model.js")).trendCards(
            {times: ["t0", "t1"], watched: ["x"], rbytes: {x: [null, null]}}, [0, 1])"""
    )
    assert absent[0]["lo"] is None and absent[0]["hi"] is None, f"absent: {absent}"
    none = page.evaluate(
        """async () => (await import("./js/model.js")).trendCards(
            {times: ["t0"], rbytes: {}}, [0, 0])"""
    )
    assert none == [], "a file without `watched` must give no cards"
    scales = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const {trendCardOption} = await import("./js/charts/trendCard.js");
            const data = {times: ["2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z"],
              watched: ["flat", "gone"], rbytes: {flat: [7, 7], gone: [null, null]}};
            return m.trendCards(data, [0, 1]).map((c) => {
              const y = trendCardOption(data, c, {colors: {}, range: [0, 1]}).yAxis;
              return [y.min, y.max];
            });
        }"""
    )
    assert scales == [[6, 8], [0, 1]], f"flat or absent card scale: {scales}"


def cards(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        "#cards .trend-card .name", "els => els.map(e => e.textContent)"
    )


def zoom(page: Page, chart: str, start: str, end: str) -> None:
    """Zoom the ECharts instance in element CHART to [START, END]."""
    page.evaluate(
        """([id, from, to]) => echarts.getInstanceByDom(document.getElementById(id))
            .dispatchAction({type: "dataZoom", startValue: from, endValue: to})""",
        [chart, start, end],
    )


def card(page: Page, name: str) -> Locator:
    """The trend card named NAME, exactly."""
    exact = page.locator(".name", has_text=re.compile(f"^{re.escape(name)}$"))
    return page.locator("#cards .trend-card").filter(has=exact)


def check_watched(page: Page, base: str) -> None:
    """The Watched view: switching, order, click-through, shared range."""

    def state() -> str:
        return page.evaluate("location.hash")

    page.goto(f"{base}#tree=1")
    page.wait_for_timeout(SETTLE_MS)
    assert page.is_visible("#view"), "view switch hidden although tree 1 watches dirs"
    page.click("#view button[data-view=watched]")
    page.wait_for_timeout(SETTLE_MS)
    assert "view=watched" in state(), "switching did not change the URL"
    names = cards(page)
    assert names[:2] == ["Z/y", "A"] and len(names) == 3, f"card order: {names}"
    assert names[2].startswith("caf"), "non-UTF-8 watched dir not shown"
    for unit_only in ("#group", "#top", "#min", "#rest", "#reset", "#legend"):
        assert page.is_hidden(unit_only), f"{unit_only} shown in the Watched view"
    a = card(page, "A")
    assert "+200 GiB" in a.inner_text(), f"change of A: {a.inner_text()}"
    # The scale's span shows even when its ends round alike (e.g. 108 TiB).
    assert "span 200 GiB" in a.inner_text(), f"scale of A: {a.inner_text()}"
    # The absolute path is the name's tooltip, not the card's, which would
    # cover the sparkline's own tooltip.
    assert a.get_attribute("title") is None, "card-wide title over the sparkline"
    assert a.locator(".name").get_attribute("title") == "/fixture/A", "name title"

    # A slider drag renders once, on release: per-move renders of 100 cards
    # over 400 points take seconds.
    before = page.inner_text("#watch-range")
    box = page.locator("#watch-slider").bounding_box()
    y = box["y"] + box["height"] - 24
    page.mouse.move(box["x"] + 25, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.7, y, steps=5)
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#watch-range") == before, "rendered during the drag"
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#watch-range") != before, "no render on release"
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)

    # Zooming here is the Units view's range too.
    times = json.loads((FIXTURE / "tree-1.json").read_text())["times"]
    zoom(page, "watch-slider", times[1], times[2])
    page.wait_for_timeout(SETTLE_MS)
    assert "+100 GiB" in a.inner_text(), f"zoomed change of A: {a.inner_text()}"
    page.reload()
    page.wait_for_timeout(SETTLE_MS)
    assert len(cards(page)) == 3, "view lost on reload"

    # A card opens the Units view in its dir; every crumb above it works.
    card(page, "Z/y").click()
    page.wait_for_timeout(SETTLE_MS)
    assert "view=" not in state() and "in=Z/y" in state(), f"click-through: {state()}"
    assert "no units" in page.inner_text("#summary"), "a dir without units looks broken"
    page.locator("#crumbs button", has_text="Z").first.click()
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#crumbs") == "/fixture / Z", "crumb above a watched dir"

    # Enter on a focused card works like a click.
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    card(page, "A").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(SETTLE_MS)
    assert "in=A" in state(), "Enter on a card did nothing"

    # Changing the tree keeps the view; a tree without watched dirs says so.
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    page.select_option("#tree", "2")
    page.wait_for_timeout(SETTLE_MS)
    assert "view=watched" in state(), "changing the tree left the Watched view"
    assert page.is_visible("#watch-note") and cards(page) == [], "no note for tree 2"

    # The Units view shows a range zoomed in the Watched view.
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    zoom(page, "watch-slider", times[1], times[2])
    page.wait_for_timeout(SETTLE_MS)
    page.click("#view button[data-view=units]")
    page.wait_for_timeout(SETTLE_MS)
    start, end = page.evaluate(
        "(ts) => ts.map((t) => new Date(t).toLocaleString())", [times[1], times[2]]
    )
    shown = page.inner_text("#table-range")
    assert shown == f"{start} \u2013 {end}", f"range after switching: {shown}"


def check_watched_resize(page: Page, base: str) -> None:
    """Sparklines take their card's size after resizes while hidden."""
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    page.click("#view button[data-view=units]")
    page.wait_for_timeout(SETTLE_MS)
    page.set_viewport_size({"width": 900, "height": 700})
    page.wait_for_timeout(SETTLE_MS)
    page.click("#view button[data-view=watched]")
    page.wait_for_timeout(SETTLE_MS)
    widths = page.eval_on_selector_all(
        "#cards .spark",
        "els => els.map(e => [e.clientWidth, e.querySelector('svg')?.clientWidth])",
    )
    assert widths and all(box == svg for box, svg in widths), f"sparks: {widths}"


def check_absent_in_range(page: Page, base: str) -> None:
    """A dir absent over the shown range says so, not that it never existed."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["rbytes"]["Z/y"] = [2**30, None, None]
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    times = tree["times"]
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    zoom(page, "watch-slider", times[1], times[2])
    page.wait_for_timeout(SETTLE_MS)
    text = card(page, "Z/y").inner_text()
    assert "absent in this range" in text, f"absent card: {text}"


def check_no_watched(page: Page, base: str) -> None:
    """Without watched dirs in any tree, there is no Watched view."""
    index = json.loads((FIXTURE / "trees.json").read_text())
    for t in index:
        t["watched"] = 0
    page.route("**/data/trees.json", lambda r: r.fulfill(json=index))
    page.goto(f"{base}#tree=1&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    assert page.is_hidden("#view"), "view switch shown with no watched dirs"
    assert page.locator("#legend li").count(), "Units view not shown"


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


def refresh_with(page: Page, index: list, tree: dict) -> None:
    """Serve INDEX and tree 1 as TREE, and let the page's refresh fetch them;
    the page must have installed page.clock before loading."""
    page.route("**/data/trees.json", lambda r: r.fulfill(json=index))
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    page.clock.fast_forward("10:00")  # main.REFRESH_MS
    page.wait_for_timeout(SETTLE_MS)


def check_refresh_after_regeneration(page: Page, base: str) -> None:
    """A regenerated tree file is fetched even with no new sample."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["threshold"] = 2**40  # e.g. a rerun with another --threshold
    tree["generated"] = "2026-10-01T04:00:00Z"
    index = json.loads((FIXTURE / "trees.json").read_text())
    index[0]["generated"] = tree["generated"]

    page.clock.install()
    page.goto(f"{base}#tree=1")
    page.wait_for_timeout(SETTLE_MS)
    refresh_with(page, index, tree)
    assert "≥ 1.00 TiB" in page.inner_text("#summary"), "regenerated file not fetched"


def check_refresh_keeps_zoom(page: Page, base: str) -> None:
    """A zoomed start keeps its date when a refresh drops older points."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    times = tree["times"]  # hourly points 00:00, 01:00, 02:00
    later = "2026-10-01T03:00:00Z"
    tree["times"] = [*times[1:], later]  # 00:00 merged away, 03:00 added
    for series in [
        *tree["rbytes"].values(),
        *(u["own"] for u in tree["units"].values()),
    ]:
        series[:] = [*series[1:], series[-1]]
    index = json.loads((FIXTURE / "trees.json").read_text())
    tree["generated"] = index[0]["generated"] = "2026-10-01T04:00:00Z"
    index[0]["last_sample"] = later

    page.clock.install()
    page.goto(f"{base}#tree=1")
    page.wait_for_timeout(SETTLE_MS)
    page.evaluate(
        """([from, to]) => echarts.getInstanceByDom(document.getElementById("chart"))
            .dispatchAction({type: "dataZoom", startValue: from, endValue: to})""",
        [times[1], times[-1]],
    )
    page.wait_for_timeout(SETTLE_MS)
    refresh_with(page, index, tree)
    shown = page.inner_text("#table-range")
    start, end = page.evaluate(
        "(ts) => ts.map((t) => new Date(t).toLocaleString())", [times[1], later]
    )
    assert shown == f"{start} \u2013 {end}", f"zoom after refresh: {shown}"


def check_click_between_points(page: Page, base: str) -> None:
    """A click drills into the band drawn under it, which lies between
    points on the line joining them, not at the nearest point."""
    gib = 2**30
    t0, t1 = "2026-10-01T00:00:00Z", "2026-10-01T04:00:00Z"
    g, h = [100 * gib, 0], [100 * gib, 100 * gib]  # G shrinks below H
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["tree"]["max_depth"] = 2
    tree["times"] = [t0, t1]
    tree["rbytes"] = {".": [200 * gib, 100 * gib], "G": g, "G/x": g, "H": h, "H/y": h}
    tree["units"] = {"G/x": {"own": g, "leaf": True}, "H/y": {"own": h, "leaf": True}}
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    page.goto(f"{base}#tree=1&group=1")
    page.wait_for_timeout(SETTLE_MS)
    # A quarter of the way along, G's top is at 75 GiB; the nearest point's 100.
    x, y = page.evaluate(
        """([t0, t1, bytes]) => {
            const el = document.getElementById("chart");
            const ms = Date.parse(t0) + (Date.parse(t1) - Date.parse(t0)) / 4;
            const chart = echarts.getInstanceByDom(el);
            const [x, y] = chart.convertToPixel("grid", [ms, bytes]);
            const box = el.getBoundingClientRect();
            return [box.left + x, box.top + y];
        }""",
        [t0, t1, 80 * gib],
    )
    page.mouse.click(x, y)
    page.wait_for_timeout(SETTLE_MS)
    state = page.evaluate("location.hash")
    assert "in=H" in state, f"click between points drilled to {state}"


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

    entries = page.evaluate("fetch('data/trees.json').then((r) => r.json())")
    watched = next(t.get("watched", 0) for t in entries if t["id"] == tree)
    if not any(t.get("watched", 0) for t in entries):
        return
    page.goto(f"{base}#tree={tree}&view=watched")
    page.wait_for_timeout(SETTLE_MS)
    if shots:
        page.screenshot(path=shots / f"tree-{tree}-watched.png", full_page=True)
    if not watched:
        assert page.is_visible("#watch-note"), f"tree {tree}: no note without cards"
        return
    n = page.locator("#cards .trend-card").count()
    assert n == watched, f"tree {tree}: {n} cards, trees.json says {watched}"

    # Drag the slider's left handle a third of the way; cards follow.
    before = page.inner_text("#watch-range")
    box = page.locator("#watch-slider").bounding_box()
    y = box["y"] + box["height"] - 24
    page.mouse.move(box["x"] + 25, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 3, y, steps=10)
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#watch-range") != before, f"tree {tree}: drag did nothing"

    other = next((t["id"] for t in entries if t["id"] != tree), None)
    if other is not None:
        page.select_option("#tree", str(other))
        page.wait_for_timeout(SETTLE_MS)
        assert "view=watched" in state(), f"tree {tree}: tree change left Watched"
        page.goto(f"{base}#tree={tree}&view=watched")
        page.wait_for_timeout(SETTLE_MS)
    page.locator("#cards .trend-card").first.click()
    page.wait_for_timeout(SETTLE_MS)
    assert "view=" not in state(), f"tree {tree}: click-through stayed in Watched"
    assert page.locator("#legend li").count(), f"tree {tree}: Units not shown"


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
        for check in (
            check_model,
            check_fixture,
            check_watched,
            check_watched_resize,
            check_absent_in_range,
            check_no_watched,
            check_slow_tree,
            check_slow_index,
            check_refresh_after_regeneration,
            check_refresh_keeps_zoom,
            check_click_between_points,
        ):
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
