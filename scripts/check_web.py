"""Smoke-test the web page in headless Chrome.

Serves web/ on a free local port with two data sets:

- tests/data/web, two synthetic trees, for regression checks of the page's
  semantics, URL codec, update ordering, data refresh, click hit-testing,
  both views and both metrics (rbytes, rentries). Tree 1 has split dirs (A,
  Z, and a name that is not UTF-8), an unsplit dir Q stored with one subdir,
  a dir absent at the first point (Z/y), dirs stored as one number, and four
  sets: Top level, Mixed (a subdir and two top-level dirs), Partial (Q/r
  only) and Empty. Tree 2 has one dir and only Top level. Some checks serve
  altered copies.
- web/data (if present), the real data written by dirhistory-generate: for
  every tree, in light and dark mode, the Stack view at Depth 0 and 1,
  drilling into the first drillable band, hiding a band, reloading from
  the URL hash and unhiding it, ticking one band after None; the Stack view
  of rentries; then each set's Trends cards.

Pages are served with the Content-Security-Policy of
deploy/nginx.conf.example, so anything it blocks is a console error.
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
MINUS = "\N{MINUS SIGN}"  # the page's sign of a negative value
CSP = re.search(
    r'add_header Content-Security-Policy "([^"]+)"',
    (ROOT / "deploy" / "nginx.conf.example").read_text(),
)[1]


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web/, but /data/ from the directory given as data."""

    def __init__(self, *args, data: Path, **kwargs):
        self.data = data
        super().__init__(*args, directory=WEB, **kwargs)

    def translate_path(self, path: str) -> str:
        if path.startswith("/data/"):
            return str(self.data / path.removeprefix("/data/").split("?")[0])
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


def serve(data: Path) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Handler, data=data)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def url(server: http.server.ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def bands(page: Page) -> list[str]:
    """The legend's band labels, top of the stack first."""
    return page.eval_on_selector_all(
        "#legend .label", "els => els.map(e => e.textContent)"
    )


def state(page: Page) -> str:
    return page.evaluate("location.hash")


def keys(page: Page) -> set[str]:
    """The URL hash's keys (a substring test would find "in" in "smin")."""
    return {pair.split("=")[0] for pair in state(page).lstrip("#").split("&")}


def go(page: Page, where: str) -> None:
    """Load WHERE and let the page fetch and render."""
    page.goto(where)
    page.wait_for_timeout(SETTLE_MS)


def act(page: Page, action: Callable[[], object]) -> None:
    """Run ACTION, then let the page render."""
    action()
    page.wait_for_timeout(SETTLE_MS)


def link(page: Page, name: str) -> Locator:
    """The legend's drill button named NAME, exactly."""
    return page.locator(
        "#legend button.link", has_text=re.compile(f"^{re.escape(name)}$")
    )


def check_fixture(page: Page, base: str) -> None:
    """Regression checks of the Stack view against tests/data/web."""
    go(page, f"{base}#tree=99")
    assert page.is_visible("#error"), "no error shown for an unknown tree"
    act(page, lambda: page.evaluate("location.hash = 'tree=1'"))
    assert page.is_hidden("#error"), "error still shown after a good load"

    # Enter in a field must not submit the form (a reload).
    page.evaluate("window.notReloaded = true")
    for field in ("#depth", "#top"):
        act(page, lambda f=field: page.press(f, "Enter"))
        assert page.evaluate("window.notReloaded === true"), (
            f"Enter in {field} reloaded"
        )

    # Out-of-range state from an old or edited URL is ignored: a dir named
    # like an Object property, a dir drilling cannot reach from the set (Q
    # is not split), an old view name.
    for bad in ("in=constructor", "set=Top%20level&in=Q/r", "view=watched"):
        act(page, lambda b=bad: page.evaluate(f"location.hash = 'tree=1&{b}'"))
        assert page.is_hidden("#error"), f"{bad} broke the page"
        assert page.inner_text("#crumbs") == "Set Top level", f"{bad} not ignored"
        assert page.is_visible("#chart"), f"{bad}: Stack view not shown"
    go(page, f"{base}#tree=1&sflat=1&smin=0&depth=9")
    assert page.input_value("#depth") == "1", "depth not clamped to the split levels"

    # The legend drills into a split dir at Depth 1.
    go(page, f"{base}#tree=1&sflat=1&smin=0")
    assert page.input_value("#depth") == "0", "Depth not 0 by default"
    act(page, lambda: link(page, "A").click())
    assert "in=A" in state(page) and "depth=1" in state(page), f"drill: {state(page)}"
    assert page.input_value("#depth") == "1", "drilling did not show Depth 1"
    assert page.inner_text("#crumbs") == "Set Top level / A", page.inner_text("#crumbs")
    assert set(bands(page)) == {"A/ bytes", "b", "c"}, f"in A: {bands(page)}"

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

    # Names that are not UTF-8: hiding an own-files band and a dir band
    # survives the URL hash; chips bring them back.
    go(page, f"{base}#tree=1&sflat=1&smin=0&depth=1")
    own = page.locator("#legend li").filter(has_text="caf").filter(has_text="/ bytes")
    act(page, lambda: own.locator("button.hide").click())
    assert not any(
        b.startswith("caf") and b.endswith("/ bytes") for b in bands(page)
    ), "own-files band not hidden"
    assert any(b.startswith("caf") and b.endswith("/x") for b in bands(page)), (
        "hiding caf/ bytes hid caf/x"
    )
    act(page, lambda: page.locator("#crumbs button").first.click())  # Depth 0
    assert "hide=" in state(page) and "depth=" not in state(page), state(page)
    act(
        page,
        lambda: (
            page.locator("#legend li", has_text="caf").locator("button.hide").click()
        ),
    )
    act(page, page.reload)
    assert not any(b.startswith("caf") for b in bands(page)), "hide lost on reload"
    assert page.locator("#hidden-list button").count() == 2, "hides lost on reload"
    for _ in range(2):
        act(page, lambda: page.locator("#hidden-list button").first.click())
    assert "hide=" not in state(page), f"chips did not unhide: {state(page)}"
    assert any(b.startswith("caf") for b in bands(page)), "chips did not bring caf back"

    # Drilling into a non-UTF-8 dir survives a reload: crumbs and bands come
    # back. Names are compared in the page (lone surrogates).
    def drilled_caf() -> list[object]:
        return page.evaluate(
            """() => {
                const labels = [...document.querySelectorAll("#legend .label")]
                  .map((e) => e.textContent)
                  .sort();
                const crumbs = document.getElementById("crumbs").textContent;
                const dir = "caf\\uDCE9";
                const want = [`${dir}/ bytes`, "x"].sort();
                return [
                  crumbs === `Set Top level / ${dir}`,
                  JSON.stringify(labels) === JSON.stringify(want),
                ];
            }"""
        )

    go(page, f"{base}#tree=1&sflat=1&smin=0")
    legend_caf = page.locator("#legend button.link", has_text="caf")
    act(page, lambda: legend_caf.click())
    assert drilled_caf() == [True, True], f"drill into caf: {bands(page)}"
    act(page, page.reload)
    assert drilled_caf() == [True, True], (
        f"drill into caf lost on reload: {bands(page)}"
    )

    # A hide made at Depth 1 still holds at Depth 0: the band above it and
    # the total shrink (A 700 -> 350 GiB, total 990 -> 640 GiB).
    go(page, f"{base}#tree=1&sflat=1&smin=0")
    a_link = page.locator("button.link", has_text=re.compile("^A$"))
    a_value = page.locator("#legend li").filter(has=a_link).locator(".value")
    assert a_value.inner_text() == "700 GiB", f"A: {a_value.inner_text()}"
    assert "990 GiB" in page.inner_text("#summary"), page.inner_text("#summary")
    go(page, f"{base}#tree=1&sflat=1&smin=0&depth=1")
    hide_b = page.locator("#legend li", has_text="A/b").locator("button.hide")
    act(page, hide_b.click)
    act(page, lambda: page.locator("#crumbs button").first.click())  # Depth 0
    assert "hide=A/b" in state(page) and "depth=" not in state(page), state(page)
    assert a_value.inner_text() == "350 GiB", f"A less A/b: {a_value.inner_text()}"
    assert "640 GiB" in page.inner_text("#summary"), page.inner_text("#summary")

    # Bands past Top are counted.
    go(page, f"{base}#tree=1&sflat=1&smin=0&top=2")
    assert len(bands(page)) == 2, f"top=2: {bands(page)}"
    assert "2 more not shown" in page.inner_text("#summary"), page.inner_text(
        "#summary"
    )

    # Top keeps the first bands in the legend's sort; they stack by span.
    go(page, f"{base}#tree=1&sflat=1&smin=0&top=2&lsort=-size")
    assert bands(page) == ["Q", "Z"], f"top=2, -size: {bands(page)}"
    assert list(series(page)) == ["Q", "Z"], f"stack not by span: {series(page)}"
    assert "(raise Top)" in page.inner_text("#summary"), page.inner_text("#summary")

    # By default the Stack view filters out unchanged bands (Q) and those
    # under 1 GiB at peak (café's own files, 0 B, at Depth 1); a ticked band
    # is never filtered out. (Playwright shows café's name with U+FFFD.)
    caf = "caf\ufffd"
    go(page, f"{base}#tree=1")
    assert bands(page) == ["A", caf, "Z"], f"default filters: {bands(page)}"
    summary = page.inner_text("#summary")
    want = "1 filtered out (unchanged in this range, or under 1.00 GiB at peak)"
    assert want in summary, summary
    act(page, lambda: page.check("#sflat"))
    assert "sflat=1" in state(page) and "Q" in bands(page), state(page)
    act(page, lambda: page.fill("#smin", "150"))
    act(page, lambda: page.press("#smin", "Enter"))
    assert bands(page) == ["A", caf], f"smin=150: {bands(page)}"
    assert "2 filtered out (under 150 GiB at peak)" in page.inner_text("#summary")
    go(page, f"{base}#tree=1&depth=1")
    assert f"{caf}/ bytes" not in bands(page), f"0 B band shown: {bands(page)}"
    go(page, f"{base}#tree=1&pick=Q&pick=A")
    assert "Q" in bands(page) and list(series(page)) == ["Q", "A"], series(page)

    # Top's range: 512 is kept, more is clamped.
    go(page, f"{base}#tree=1&sflat=1&smin=0&top=512")
    assert page.input_value("#top") == "512", page.input_value("#top")
    go(page, f"{base}#tree=1&sflat=1&smin=0&top=600")
    assert page.input_value("#top") == "512", page.input_value("#top")

    # Reset restores the Stack view's state.
    go(
        page,
        f"{base}#tree=1&sflat=1&smin=0&depth=1&top=2&in=A&hide=Z&pick=A/b&lsort=-size"
        "&sort=size&sminent=5&metric=rentries",
    )
    act(page, lambda: page.click("#reset"))
    stack_keys = {
        "depth",
        "top",
        "in",
        "hide",
        "pick",
        "lsort",
        "smin",
        "sminent",
        "sflat",
    }
    assert not keys(page) & stack_keys, f"Reset kept some: {state(page)}"
    assert "sort=size" in state(page), f"Reset changed the Trends view: {state(page)}"
    assert "metric=rentries" in state(page), f"Reset changed the metric: {state(page)}"
    assert page.input_value("#depth") == "0" and page.input_value("#top") == "64", (
        "Reset did not restore the fields"
    )


def series(page: Page) -> dict[str, str]:
    """The Stack chart's series, bottom first, as {band key: color}."""
    return page.evaluate(
        """() => {
            const chart = echarts.getInstanceByDom(document.getElementById("chart"));
            const series = chart.getOption().series;
            return Object.fromEntries(series.map((s) => [s.id, s.itemStyle.color]));
        }"""
    )


def tick(page: Page, name: str) -> None:
    """Click the tick box of the legend row whose drill button matches
    regex NAME."""
    # has= is matched inside each row.
    drill = page.locator("button.link", has_text=re.compile(name))
    act(
        page,
        lambda: page.locator("#legend li").filter(has=drill).locator("input").click(),
    )


def tooltip_names(page: Page, i: int) -> list[str]:
    """The band names the Stack chart's tooltip lists at point I, in order."""
    return page.evaluate(
        """(i) => {
            const chart = echarts.getInstanceByDom(document.getElementById("chart"));
            const div = document.createElement("div");
            div.innerHTML = chart.getOption().tooltip[0].formatter([{dataIndex: i}]);
            return [...div.querySelectorAll(".tt-row:has(.tt-key[style]) .tt-name")]
              .map((el) => el.textContent);
        }""",
        i,
    )


def check_picks(page: Page, base: str) -> None:
    """The legend's tick boxes, None and All, and its column sorts."""
    go(page, f"{base}#tree=1&sflat=1&smin=0")
    # Keys stay in the page's strings: café's name is not UTF-8.
    colors = series(page)
    assert len(colors) == 4 and next(iter(colors)) == "Q", f"series: {list(colors)}"

    # None draws nothing but keeps every row listed, unticked, and the
    # range: the empty chart's zoom slider must not change it.
    shown = page.inner_text("#table-range")
    act(page, lambda: page.click("#pick-none"))
    assert page.inner_text("#table-range") == shown, "None changed the range"
    assert re.search(r"&pick=(&|$)", state(page)), f"None: {state(page)}"
    assert series(page) == {}, f"None drew {series(page)}"
    assert len(bands(page)) == 4, f"None changed the list: {bands(page)}"
    assert page.locator("#legend input.pick:checked").count() == 0, "rows still ticked"
    summary = page.inner_text("#summary")
    assert "0 B in 0 of 4 listed bands" in summary, summary
    assert page.locator("#table-body tr").count() == 0, "table lists undrawn bands"

    # Ticking draws just those, in stack order, with their colors; ticked
    # rows head the legend. (Playwright shows café's name with U+FFFD.)
    caf = "caf\ufffd"
    tick(page, "^Z$")
    tick(page, "^A$")
    assert series(page) == {"A": colors["A"], "Z": colors["Z"]}, series(page)
    assert bands(page) == ["A", "Z", caf, "Q"], f"ticked not first: {bands(page)}"
    summary = page.inner_text("#summary")
    assert "750 GiB in 2 of 4 listed bands" in summary, summary
    assert page.locator("#table-body tr").count() == 2, "table not of drawn bands"

    # Sorting reorders the legend, not the chart, and survives a reload; the
    # header shows the order; a third click is chart order again.
    delta = page.locator("#legend-head .sort[data-sort='delta']")
    act(page, delta.click)
    assert bands(page) == ["A", "Z", caf, "Q"], f"by change: {bands(page)}"
    assert delta.get_attribute("data-order") == "down", "no sort arrow"
    assert list(series(page)) == ["Z", "A"], "sorting changed the chart"
    act(page, page.reload)
    assert list(series(page)) == ["Z", "A"], "picks lost on reload"
    assert bands(page) == ["A", "Z", caf, "Q"] and "lsort=delta" in state(page), (
        "sort lost on reload"
    )
    act(page, delta.click)
    assert bands(page) == ["Z", "A", "Q", caf], f"reversed: {bands(page)}"
    assert delta.get_attribute("data-order") == "up", "no reversed arrow"
    # The tooltip lists bands in the legend's order, at every point.
    for i in (0, 1):
        assert tooltip_names(page, i) == ["Z", "A"], (
            f"tooltip at {i}: {tooltip_names(page, i)}"
        )
    act(page, delta.click)
    assert "lsort=" not in state(page), state(page)
    assert bands(page) == ["A", "Z", caf, "Q"], f"chart order: {bands(page)}"
    assert delta.get_attribute("data-order") is None, "arrow left in chart order"
    assert tooltip_names(page, 0) == ["A", "Z"], f"tooltip: {tooltip_names(page, 0)}"

    # A chart click finds the band among those drawn: 3/4 of the way to the
    # last point, A (on Z's 62.5 GiB) holds 150 GiB; café (unticked) would
    # lie between them and hold it.
    x, y = page.evaluate(
        """(bytes) => {
            const el = document.getElementById("chart");
            const chart = echarts.getInstanceByDom(el);
            const ms = (Date.parse("2026-10-01T01:00:00Z")
              + 3 * Date.parse("2026-10-01T02:00:00Z")) / 4;
            const [x, y] = chart.convertToPixel("grid", [ms, bytes]);
            const box = el.getBoundingClientRect();
            return [box.left + x, box.top + y];
        }""",
        150 * 2**30,
    )
    act(page, lambda: page.mouse.click(x, y))
    assert "in=A" in state(page), f"chart click among picks: {state(page)}"
    assert "pick=" not in state(page), f"drilling kept picks: {state(page)}"

    # Hiding a picked band drops its pick; All, or ticking the last
    # unticked row, ends picking.
    go(page, f"{base}#tree=1&sflat=1&smin=0&pick=A&pick=Z")
    z_link = page.locator("button.link", has_text=re.compile("^Z$"))
    hide_z = page.locator("#legend li").filter(has=z_link).locator("button.hide")
    act(page, hide_z.click)
    assert "pick=A" in state(page) and "pick=Z" not in state(page), state(page)
    act(page, lambda: page.click("#pick-all"))
    assert "pick=" not in state(page), f"All: {state(page)}"
    go(page, f"{base}#tree=1&sflat=1&smin=0&pick=A&pick=Z&pick=Q")
    tick(page, "^caf")
    assert "pick=" not in state(page), f"ticking the last row kept picks: {state(page)}"

    # A new level clears picks: changing Depth, a trend card's click-through.
    go(page, f"{base}#tree=1&sflat=1&smin=0&pick=A")
    page.fill("#depth", "1")
    act(page, lambda: page.press("#depth", "Enter"))
    assert "depth=1" in state(page) and "pick=" not in state(page), state(page)
    go(page, f"{base}#tree=1&sflat=1&smin=0&pick=A&view=trends")
    act(page, page.locator("#cards .trend-card").first.click)
    assert "in" in keys(page) and "pick" not in keys(page), state(page)


def check_model(page: Page, base: str) -> None:
    """Pure model functions, called in the page."""
    gib = 2**30
    caf = "caf\udce9"
    # Built and compared in the page, returned as JSON: Playwright turns
    # lone surrogates into U+FFFD, json.loads keeps them.
    go(page, base)
    got = json.loads(
        page.evaluate(
            """async () => {
            const m = await import("./js/model.js");
            const raw = await (await fetch("data/tree-1.json")).json();
            const data = m.normalize(raw);
            const set = (name) => data.sets.find((s) => s.name === name).paths;
            const top = set("Top level");
            const opts = {roots: top, base: null, depth: 0, top: m.MAX_BANDS,
              hidden: [], range: [0, 2]};
            const run = (o) => m.stackBands(data, {...opts, ...o});
            const keys = (o) => {
              const r = run(o);
              return {keys: r.bands.map((b) => b.key), cut: r.cut};
            };
            const info = (bands) => Object.fromEntries(bands.map((b) => [b.key,
              {label: b.label, own: b.own, drillable: b.drillable, values: b.values}]));
            const rooted = m.normalize({...raw,
              series: {".": [300, 9], a: [100, 5], "a/x": [40, 2],
                b: [[50, 100, 150], 1]},
              sets: [{name: "Root", paths: ["."]}], split: [".", "a"]});
            const rootValues = (hidden) => m.stackBands(rooted,
              {...opts, roots: ["."], hidden}).bands[0].values;
            return JSON.stringify({
              n: [m.MAX_BANDS, m.DEFAULT_TOP, m.PALETTE_SIZE],
              rbytes: data.rbytes,
              rentries: data.rentries,
              children: [...data.children],
              d0: keys({}),
              d1: keys({depth: 1}),
              bands0: info(run({}).bands),
              bands1: info(run({depth: 1}).bands),
              inA: info(run({roots: ["A"], base: "A", depth: 1}).bands),
              top2: keys({top: 2}),
              // Picks are listed first, in rank order, even past Top.
              keepQ: keys({top: 2, keep: ["Q"]}),
              keepPastTop: keys({top: 1, keep: ["Q", "Z", "nope"]}),
              // Top keeps the first in the legend's sort, stacked by span.
              byTop2: Object.fromEntries(
                ["", "size", "-size", "-delta", "name", "-name", "bogus"].map((by) =>
                  [by, keys({top: 2, by})])),
              keepBy: keys({top: 2, by: "-size", keep: ["A"]}),
              // Filters: peak at least min bytes; changed unless flat.
              unchanged: keys({flat: false}),
              small: run({min: 150 * 2 ** 30, top: 1}),
              keepSmall: keys({min: 150 * 2 ** 30, flat: false, keep: ["Q"]}),
              passes: [[{peak: 5, span: 0}, {min: 5, flat: true}],
                [{peak: 5, span: 0}, {min: 5, flat: false}],
                [{peak: 4, span: 1}, {min: 5, flat: true}],
              ].map(([x, f]) => m.passes(x, f)),
              reasons: [{min: 0, flat: true}, {min: 2 ** 30, flat: false},
                {min: 0, flat: false}].map((f) => m.filterReason(f)),
              entryReason: m.filterReason({min: 1000, flat: true},
                m.METRICS.rentries.amount),
              // rentries: the same bands, of another series.
              entries1: info(run({metric: "rentries", depth: 1}).bands),
              entries0: keys({metric: "rentries"}),
              entryCards: m.trendCards(data, top, [0, 2], "rentries")
                .map((c) => [c.path, c.end, c.change, c.span]),
              counts: [0, 999, 999.6, 1000, 12345, 999499, 1.5e9, -2500]
                .map(m.formatCount),
              deltas: [m.formatDelta(0, m.formatCount),
                m.formatDelta(2500, m.formatCount), m.formatDelta(0),
                m.formatDelta(-1024)],
              // A metric the file does not list is null.
              noEntries: m.normalize({...raw, metrics: ["rbytes"]}).rentries,
              // A file from before series pairs: an rbytes map only.
              old: (({series, metrics, ...rest}) => {
                const old = m.normalize({...rest, rbytes: Object.fromEntries(
                  Object.entries(series).map(([p, pair]) => [p, pair[0]]))});
                return [JSON.stringify(old.rbytes) === JSON.stringify(data.rbytes),
                  old.rentries, "series" in old];
              })(raw),
              legend: Object.fromEntries(
                ["", "delta", "-delta", "size", "name", "-name", "bogus"].map((by) =>
                  [by, m.sortLegend(run({}).bands, by, [0, 2]).map((b) => b.key)])),
              // Ticked bands first (Z, Q), each group in the sort's order.
              legendFirst: ["", "delta"].map((by) => {
                const bands = run({}).bands;
                const first = bands.filter((b) => ["Z", "Q"].includes(b.key));
                return m.sortLegend(bands, by, [0, 2], first).map((b) => b.key);
              }),
              hideA: keys({depth: 1, hidden: ["A"]}),
              hideOwnA: keys({depth: 1, hidden: ["A/."]}),
              // Hidden bytes leave the bands above them too (Depth 0).
              zLessY: info(run({hidden: ["Z/y"]}).bands).Z.values,
              aLessOwn: info(run({hidden: ["A/."]}).bands).A.values,
              rootLess: [["a/x"], ["a", "a/x"], ["a/x", "a"], ["a/."], ["a", "a/."],
                ["./."], ["nope", "nope/.", "b/."]].map(rootValues),
              maxTop: m.maxDepth(data, top),
              maxPartial: m.maxDepth(data, set("Partial")),
              root: info(m.stackBands(rooted, {...opts, roots: ["."], depth: 1}).bands),
              setDirOf: [m.setDirOf([".", "A"], "A/b"), m.setDirOf(["A", "."], "Q"),
                m.setDirOf(["Z"], "A")],
              reachable: ["A", "A/b", "caf\\uDCE9/x", "Q", "Q/r", "constructor",
                "Z/y/q"].filter((p) => m.reachable(data, top, p)),
            });
        }"""
        )
    )
    assert got["n"] == [512, 64, 64], (
        f"MAX_BANDS, DEFAULT_TOP, PALETTE_SIZE: {got['n']}"
    )
    assert got["entryReason"] == "under 1.00 k entries at peak", got["entryReason"]
    e1 = got["entries1"]
    assert e1["A/."]["label"] == "A/ entries", f"own entries label: {e1['A/.']}"
    # Own entries: the files directly in a dir, plus the dir itself.
    assert e1["A/."]["values"] == [1, 1, 1], f"A/ entries: {e1['A/.']}"
    assert e1["Z/."]["values"] == [500, 100, 101], f"Z/ entries: {e1['Z/.']}"
    assert e1["A/b"]["values"] == [2000] * 3, f"A/b entries: {e1['A/b']}"
    # Ranked by their own span: café 5000, Z 200, A and Q 0 (by key).
    assert got["entries0"] == {"keys": ["Q", "A", "Z", caf], "cut": 0}, got["entries0"]
    assert got["entryCards"] == [
        ["A", 3000, 0, 0],
        ["Q", 40000, 0, 0],
        ["Z", 300, -200, 200],
        [caf, 15000, 5000, 5000],
    ], f"entry cards: {got['entryCards']}"
    assert got["counts"] == [
        "0",
        "999",
        "1.00 k",
        "1.00 k",
        "12.3 k",
        "999 k",
        "1.50 G",
        f"{MINUS}2.50 k",
    ], f"formatCount: {got['counts']}"
    assert got["deltas"] == ["±0", "+2.50 k", "±0 B", f"{MINUS}1.00 KiB"], got["deltas"]
    assert got["noEntries"] is None, f"file without rentries: {got['noEntries']}"
    assert got["old"] == [True, None, False], f"file with rbytes only: {got['old']}"
    assert got["rentries"]["A/c"] == [999] * 3, "rentries scalars not expanded"
    rb = got["rbytes"]
    assert rb["A/c"] == [100 * gib] * 3 and rb["Q/r"] == [10 * gib] * 3, "scalars"
    assert rb["Z/y"][0] is None, "null not kept"
    assert dict(got["children"]) == {
        "A": ["A/b", "A/c"],
        "Z": ["Z/y"],
        caf: [f"{caf}/x"],
    }, f"children: {got['children']}"

    # Depth 0: the set dirs, bottom first: least span first (Q 0, Z 50,
    # café 100, A 200 GiB), so the movers ride on a steady base.
    assert got["d0"] == {"keys": ["Q", "Z", caf, "A"], "cut": 0}, (
        f"depth 0: {got['d0']}"
    )
    b0 = got["bands0"]
    assert b0["A"]["label"] == "A" and b0["A"]["drillable"], f"band A: {b0['A']}"
    assert not b0["Q"]["drillable"], "unsplit Q drillable"
    assert b0["Z"]["values"] == [100 * gib, 100 * gib, 50 * gib], "Z values"

    # Depth 1: split dirs become own files plus subdirs; Q stays whole.
    want = {"A/.", "A/b", "A/c", "Q", "Z/.", "Z/y", f"{caf}/.", f"{caf}/x"}
    assert set(got["d1"]["keys"]) == want, f"depth 1: {got['d1']}"
    b1 = got["bands1"]
    assert b1["A/."]["values"][2] == 250 * gib, f"A/ bytes: {b1['A/.']}"
    assert b1["Z/."]["values"][0] == 100 * gib, f"Z/ bytes with absent Z/y: {b1['Z/.']}"
    assert b1["Z/y"]["values"][0] == 0, "absent not 0"
    assert all(v >= 0 for b in b1.values() for v in b["values"]), "negative value"
    assert b1["A/."]["label"] == "A/ bytes" and b1["A/."]["own"], f"own: {b1['A/.']}"
    assert not b1["A/."]["drillable"], "own-files band drillable"
    assert b1["A/b"]["label"] == "A/b", f"label relative to the root: {b1['A/b']}"
    labels = {k: v["label"] for k, v in got["inA"].items()}
    assert labels == {"A/.": "A/ bytes", "A/b": "b", "A/c": "c"}, f"in A: {labels}"

    # Top, and hiding a dir (its subtree) or only its own files.
    assert len(got["top2"]["keys"]) == 2 and got["top2"]["cut"] == 2, got["top2"]
    assert got["keepQ"] == {"keys": ["Q", "A"], "cut": 2}, f"keep Q: {got['keepQ']}"
    assert got["keepPastTop"] == {"keys": ["Z"], "cut": 3}, got["keepPastTop"]
    by_top2 = {by: r["keys"] for by, r in got["byTop2"].items()}
    assert by_top2 == {
        "": [caf, "A"],
        "size": [caf, "A"],
        "-size": ["Q", "Z"],
        "-delta": ["Q", "Z"],
        "name": ["Q", "A"],
        "-name": ["Z", caf],
        "bogus": [caf, "A"],
    }, f"Top by legend sort: {by_top2}"
    assert all(r["cut"] == 2 for r in got["byTop2"].values()), got["byTop2"]
    assert got["keepBy"] == {"keys": ["Q", "A"], "cut": 2}, (
        f"pick kept: {got['keepBy']}"
    )
    # Filters (peaks A 700, café 200, Z 100, Q 40 GiB; Q unchanged), picks
    # exempt. Filtered bands are counted apart from those past Top.
    assert got["unchanged"] == {"keys": ["Z", caf, "A"], "cut": 0}, got["unchanged"]
    small = got["small"]
    assert [b["key"] for b in small["bands"]] == ["A"], f"min: {small}"
    assert (small["cut"], small["filtered"]) == (1, 2), f"min counts: {small}"
    assert got["keepSmall"]["keys"] == ["Q", caf, "A"], got["keepSmall"]
    assert got["passes"] == [True, False, False], f"passes: {got['passes']}"
    assert got["reasons"] == [
        "",
        "unchanged in this range, or under 1.00 GiB at peak",
        "unchanged in this range",
    ], f"filter reasons: {got['reasons']}"

    # Legend orders, over the range: change A +200, café +100, Q 0, Z -50
    # GiB; size A 700, café 200, Z 50, Q 40 GiB. Chart order is the top of
    # the stack first (the biggest movers); an unknown sort is chart order.
    chart = ["A", caf, "Z", "Q"]
    assert got["legend"] == {
        "": chart,
        "delta": ["A", caf, "Q", "Z"],
        "-delta": ["Z", "Q", caf, "A"],
        "size": ["A", caf, "Z", "Q"],
        "name": ["A", "Q", "Z", caf],
        "-name": [caf, "Z", "Q", "A"],
        "bogus": chart,
    }, f"legend orders: {got['legend']}"
    assert got["legendFirst"] == [["Z", "Q", "A", caf], ["Q", "Z", "A", caf]], (
        f"ticked first: {got['legendFirst']}"
    )
    assert not {"A/.", "A/b", "A/c"} & set(got["hideA"]["keys"]), got["hideA"]
    assert "Q" in got["hideA"]["keys"], f"hiding A hid Q: {got['hideA']}"
    assert set(got["hideOwnA"]["keys"]) == want - {"A/."}, got["hideOwnA"]
    assert got["zLessY"] == [100 * gib, 20 * gib, 20 * gib], f"Z - Z/y: {got['zLessY']}"
    assert got["aLessOwn"] == [350 * gib, 400 * gib, 450 * gib], (
        f"A - A/ bytes: {got['aLessOwn']}"
    )
    # ROOT 300 = a 100 (a/x 40, a/ bytes 60) + b + ROOT/ bytes; nothing twice;
    # unknown dirs and unsplit own-files keys subtract nothing.
    assert got["rootLess"] == [
        [260] * 3,
        [200] * 3,
        [200] * 3,
        [240] * 3,
        [200] * 3,
        [150, 200, 250],
        [300] * 3,
    ], f"ROOT less hidden: {got['rootLess']}"

    assert (got["maxTop"], got["maxPartial"]) == (1, 0), "maxDepth"
    assert got["root"]["./."]["label"] == "/fixture/ bytes", f"ROOT: {got['root']}"
    assert got["root"]["./."]["values"] == [150, 100, 50], f"ROOT: {got['root']}"
    assert got["setDirOf"] == ["A", ".", None], f"setDirOf: {got['setDirOf']}"
    assert got["reachable"] == ["A", "A/b", f"{caf}/x", "Q"], got["reachable"]

    check_rates(page)

    cards = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const data = {times: ["t0", "t1", "t2"],
              rbytes: {a: [10, 30, 20], gone: [5, null, null], flat: [7, 7, 7]}};
            return m.trendCards(data, ["a", "gone", "flat"], [0, 2]);
        }"""
    )
    assert [c["path"] for c in cards] == ["a", "gone", "flat"], "cards out of order"
    a, gone, flat = cards
    assert (a["start"], a["end"], a["change"]) == (10, 20, 10), f"change: {a}"
    assert (a["lo"], a["hi"]) == (10, 30), f"scale: {a}"
    assert (gone["end"], gone["change"]) == (0, -5), f"absent end: {gone}"
    assert flat["lo"] == flat["hi"] == 7, f"flat scale: {flat}"
    assert (a["peak"], a["span"]) == (30, 20), f"peak and span: {a}"
    assert (gone["peak"], gone["span"]) == (5, 5), f"span of a deleted dir: {gone}"
    assert flat["span"] == 0, f"span of a constant dir: {flat}"
    zoomed = page.evaluate(
        """async () => (await import("./js/model.js")).trendCards(
            {times: ["t0", "t1", "t2"], rbytes: {a: [10, 30, 20]}}, ["a"], [1, 2])"""
    )
    assert (zoomed[0]["lo"], zoomed[0]["change"]) == (20, -10), "scale not by range"
    absent = page.evaluate(
        """async () => (await import("./js/model.js")).trendCards(
            {times: ["t0", "t1"], rbytes: {x: [null, null]}}, ["x"], [0, 1])"""
    )
    assert absent[0]["lo"] is None and absent[0]["hi"] is None, f"absent: {absent}"

    # Unchanged: constant or absent throughout the range; a dir that appears
    # or disappears in it changed.
    kept = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const data = {times: ["t0", "t1", "t2"],
              rbytes: {const: [7, 7, 7], never: [null, null, null], new: [null, 5, 5],
                gone: [5, null, null], back: [5, null, 5], up: [5, 5, 6]}};
            return m.trendCards(data, Object.keys(data.rbytes), [0, 2])
              .filter((c) => !m.unchanged(c))
              .map((c) => c.path);
        }"""
    )
    assert kept == ["new", "gone", "back", "up"], f"unchanged rule: {kept}"

    # Every sort puts growing cards first, then shrinking, then flat (end =
    # start), and orders within each group: d shrank more than anything grew,
    # c is largest, and both still come after the growers.
    orders = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const data = {times: ["t0", "t1"],
              rbytes: {a: [10, 40], b: [50, 20], c: [90, 90], B: [10, 40],
                d: [100, 0], x: [null, null]}};
            const cards = m.trendCards(data, ["b", "a", "c", "B", "d", "x"], [0, 1]);
            return Object.fromEntries(Object.keys(m.CARD_SORTS).map(
              (k) => [k, m.sortCards(cards, k).map((c) => c.path)]));
        }"""
    )
    assert orders == {
        "span": ["B", "a", "d", "b", "c", "x"],
        "peak": ["B", "a", "d", "b", "c", "x"],
        "size": ["B", "a", "b", "d", "c", "x"],
        "name": ["B", "a", "b", "d", "c", "x"],
    }, f"card orders: {orders}"

    sparks = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const t = await import("./js/charts/trendCard.js");
            const data = {times: ["2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z",
                "2026-10-01T03:00:00Z", "2026-10-01T04:00:00Z"],
              rbytes: {up: [0, 10, 20, 30], flat: [7, 7, 7, 7], gap: [1, 2, null, 3],
                never: [null, null, null, null]}};
            const paths = Object.keys(data.rbytes);
            const cards = m.trendCards(data, paths, [0, 3]);
            const xs = t.sparkXs(data.times, [0, 3]);
            return {
              paths: cards.map((c) => t.sparkPaths(xs, c, [0, 3])),
              at: [0, 0.3, 0.6, 1].map((f) => t.sparkPointAt(xs, [0, 3], f)),
              zoomed: t.sparkPaths(t.sparkXs(data.times, [1, 2]),
                m.trendCards(data, paths, [1, 2])[0], [1, 2]),
              zoomedAt: t.sparkPointAt(t.sparkXs(data.times, [1, 2]), [1, 2], 1),
            };
        }"""
    )
    up, flat_, gap, never = sparks["paths"]
    # x by time (the third point is 3 h of 4 in), y from the bottom up.
    assert up["line"] == "M 0 53 L 250 36.3 L 750 19.7 L 1000 3", f"line: {up}"
    assert up["fill"] == "M 0 53 L 0 53 L 250 36.3 L 750 19.7 L 1000 3 L 1000 53 Z"
    assert flat_["line"] == "M 0 28 L 250 28 L 750 28 L 1000 28", "flat mid-height"
    assert gap["line"] == "M 0 53 L 250 28", f"gap or lone point: {gap}"
    assert never == {"line": "", "fill": ""}, f"absent throughout: {never}"
    assert sparks["zoomed"]["line"] == "M 0 53 L 1000 3", "scale not by range"
    assert [i for i, _ in sparks["at"]] == [0, 1, 2, 3], f"nearest: {sparks['at']}"
    assert sparks["zoomedAt"] == [2, 1000], f"nearest when zoomed: {sparks['zoomedAt']}"

    # gapWidths, bottom band first, at 1 px per unit (MIN_GAPPED_PX is 4).
    gaps = page.evaluate(
        """async () => {
            const m = await import("./js/charts/stackedArea.js");
            const fit = (rows, range = [0, 2], px = 1) =>
              m.gapWidths(rows.map((values) => ({values})), range, px);
            const thick = [10, 10, 10], thin = [1, 1, 1], absent = [0, 0, 0];
            return {
              min: m.MIN_GAPPED_PX,
              none: fit([]),
              mixed: fit([thick, thin, thick, thick]),
              edge: [fit([[4, 4, 4]]), fit([[3.99, 4, 4]])],
              zeros: fit([[0, 10, null], thick]),
              absentBetween: fit([thick, absent, thick]),
              absentAboveThin: fit([thin, absent, thick]),
              allAbsent: fit([absent, absent]),
              ranged: [fit([[1, 10, 10]], [1, 2]), fit([[1, 10, 10]], [0, 2])],
              scaled: fit([[100, 100, 100]], [0, 2], 0.03),
            };
        }"""
    )
    assert gaps["min"] == 4, f"MIN_GAPPED_PX changed: {gaps['min']}; update these cases"
    assert gaps["none"] == [], f"no bands: {gaps['none']}"
    # A thin band's edges have no gap: its own (top) and the one below's.
    assert gaps["mixed"] == [0, 0, 2, 2], f"thin between thick: {gaps['mixed']}"
    assert gaps["edge"] == [[2], [0]], f"4 and 3.99 px tall: {gaps['edge']}"
    assert gaps["zeros"] == [2, 2], f"absent points make a band thin: {gaps['zeros']}"
    assert gaps["absentBetween"] == [2, 0, 2], f"absent band: {gaps['absentBetween']}"
    got = gaps["absentAboveThin"]
    assert got == [0, 0, 2], f"thin under absent: {got}"
    assert gaps["allAbsent"] == [0, 0], f"all absent: {gaps['allAbsent']}"
    assert gaps["ranged"] == [[2], [0]], f"not by range: {gaps['ranged']}"
    assert gaps["scaled"] == [0], f"3 px at 0.03 px per unit: {gaps['scaled']}"


def check_rates(page: Page) -> None:
    """Change mode's model: rates per day, lag smoothing, stack order."""
    got = page.evaluate(
        """async () => {
            const m = await import("./js/model.js");
            const raw = await (await fetch("data/tree-1.json")).json();
            const hours = (...hs) => hs.map((h) => new Date(Date.UTC(2026, 9, 1, h)).toISOString());
            const tree = (times, series, paths, split) => m.normalize({...raw, times,
              series: Object.fromEntries(Object.entries(series).map(([p, v]) => [p, [v, 1]])),
              sets: [{name: "S", paths}], split});
            const lag = (parent, kid) => m.smoothLag(parent.map((v, i) => v - kid[i]), [kid]);
            const lagTree = tree(hours(0, 1, 2), {".": [100, 100, 200], k: [90, 190, 190]},
              ["."], ["."]);
            const own = (smooth) => m.stackBands(lagTree, {roots: ["."], base: null, depth: 1,
              top: 64, hidden: [], range: [0, 2], flat: true, smooth}).bands
              .find((b) => b.key === "./.").values;
            const moves = tree(hours(0, 1, 2, 3, 4),
              {s: [0, 10, 20, 30, 40], j: [0, 0, 30, 30, 30]}, ["s", "j"], []);
            const run = (o) => m.stackBands(moves, {roots: ["s", "j"], base: null, depth: 0,
              top: 64, hidden: [], range: [0, 4], ...o}).bands;
            const keys = (o) => run(o).map((b) => b.key);
            return {
              rates: m.rates([0, 10, 20, 30], hours(0, 1, 3, 4)),
              ratesAbsent: m.rates([null, 5, null], hours(0, 1, 2)),
              formatRate: [m.formatRate(2400 * 2 ** 30), m.formatRate(0),
                m.formatRate(-1000, m.formatCount)],
              dip: lag([100, 100, 110, 110], [50, 60, 60, 60]),
              bump: lag([100, 100, 90, 90], [50, 40, 40, 40]),
              ownStep: lag([100, 100, 110, 110], [50, 50, 50, 50]),
              bothStep: lag([100, 120, 120, 120], [50, 60, 60, 60]),
              lastPoint: lag([100, 100, 100], [50, 50, 60]),
              ownRaw: own(false),
              ownSmooth: own(true),
              hiddenSmooth: m.stackBands(lagTree, {roots: ["."], base: null, depth: 0,
                top: 64, hidden: ["k"], range: [0, 2], flat: true, smooth: true}).bands[0].values,
              sizeOrder: keys({}),
              changeOrder: keys({change: true}),
              changeTop1: keys({change: true, top: 1}),
              steadyKept: keys({change: true, flat: false}),
              sRates: run({change: true}).find((b) => b.key === "s").rates,
              onePointOrder: keys({change: true, range: [2, 2], flat: true}),
            };
        }"""
    )
    assert got["rates"] == [None, 240, 120, 240], f"rates: {got['rates']}"
    assert got["ratesAbsent"] == [None, 120, -120], f"absent: {got['ratesAbsent']}"
    want = ["+2.34 TiB/d", "±0 B/d", f"{MINUS}1.00 k/d"]
    assert got["formatRate"] == want, f"formatRate: {got['formatRate']}"
    # A lag dip and a lag bump go; real steps stay; the last point is kept.
    assert got["dip"] == [50, 50, 50, 50], f"dip: {got['dip']}"
    assert got["bump"] == [50, 50, 50, 50], f"bump: {got['bump']}"
    assert got["ownStep"] == [50, 50, 60, 60], f"own step: {got['ownStep']}"
    assert got["bothStep"] == [50, 60, 60, 60], f"both step: {got['bothStep']}"
    assert got["lastPoint"] == [50, 50, 40], f"last point: {got['lastPoint']}"
    # ROOT/ bytes = [10, -90, 10]: clamped, or smoothed before the clamp.
    assert got["ownRaw"] == [10, 0, 10], f"own, smooth off: {got['ownRaw']}"
    assert got["ownSmooth"] == [10, 10, 10], f"own, smooth on: {got['ownSmooth']}"
    assert got["hiddenSmooth"] == [10, 10, 10], f"hidden k: {got['hiddenSmooth']}"
    # Steady s (span 40, rate span 0) and jumpy j (span 30, rate span 720/d).
    assert got["sizeOrder"] == ["j", "s"], f"size order: {got['sizeOrder']}"
    assert got["changeOrder"] == ["s", "j"], f"change order: {got['changeOrder']}"
    assert got["changeTop1"] == ["s"], f"Top 1 by size span: {got['changeTop1']}"
    assert got["steadyKept"] == ["s", "j"], f"steady unchanged? {got['steadyKept']}"
    assert got["sRates"] == [None, 240, 240, 240, 240], f"s rates: {got['sRates']}"
    assert got["onePointOrder"] == ["j", "s"], f"one point: {got['onePointOrder']}"


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


def check_trends(page: Page, base: str) -> None:
    """The Trends view: switching, order, click-through, shared range."""
    go(page, f"{base}#tree=1&set=Mixed")
    act(page, lambda: page.click("#view button[data-view=trends]"))
    assert "view=trends" in state(page), "switching did not change the URL"
    names = cards(page)
    # Largest span first: A 200 GiB, café 100 GiB, Z/y 80 GiB (absent = 0).
    assert names[0] == "A" and names[2] == "Z/y", f"card order: {names}"
    assert names[1].startswith("caf"), "non-UTF-8 set dir not shown"
    for stack_only in ("#depth", "#top", "#smin", "#sflat", "#reset", "#legend"):
        assert page.is_hidden(stack_only), f"{stack_only} shown in the Trends view"
    assert page.is_visible("#set"), "Set hidden in the Trends view"
    a = card(page, "A")
    assert "+200 GiB" in a.inner_text(), f"change of A: {a.inner_text()}"
    # The scale's span shows even when its ends round alike (e.g. 108 TiB).
    assert "span 200 GiB" in a.inner_text(), f"scale of A: {a.inner_text()}"
    # The absolute path is the name's tooltip, not the card's, which would
    # cover the sparkline's own tooltip.
    assert a.get_attribute("title") is None, "card-wide title over the sparkline"
    assert a.locator(".name").get_attribute("title") == "/fixture/A", "name title"

    # The sparkline's tooltip: time and size at the pointer.
    spark = a.locator(".spark").bounding_box()
    page.mouse.move(spark["x"] + spark["width"] - 2, spark["y"] + spark["height"] / 2)
    tip = page.inner_text("#spark-tip")
    assert page.is_visible("#spark-tip") and "700 GiB" in tip, f"tooltip: {tip}"
    page.mouse.move(0, 0)
    assert page.is_hidden("#spark-tip"), "tooltip left after the pointer"

    # A slider drag renders once, on release: per-move renders of 100 cards
    # over 400 points take seconds.
    before = page.inner_text("#trends-range")
    box = page.locator("#trends-slider").bounding_box()
    y = box["y"] + box["height"] - 24
    page.mouse.move(box["x"] + 25, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.7, y, steps=5)
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#trends-range") == before, "rendered during the drag"
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#trends-range") != before, "no render on release"
    go(page, f"{base}#tree=1&view=trends&set=Mixed")

    # Zooming here is the Stack view's range too.
    times = json.loads((FIXTURE / "tree-1.json").read_text())["times"]
    act(page, lambda: zoom(page, "trends-slider", times[1], times[2]))
    assert "+100 GiB" in a.inner_text(), f"zoomed change of A: {a.inner_text()}"
    act(page, page.reload)
    assert len(cards(page)) == 3, "view lost on reload"

    # A card opens the Stack view in its dir: Depth 1 if it is split, else 0.
    act(page, lambda: card(page, "Z/y").click())
    assert "view=" not in state(page) and "in=Z/y" in state(page), state(page)
    assert "depth=" not in state(page), f"unsplit Z/y at Depth 1: {state(page)}"
    assert page.inner_text("#crumbs") == "Set Mixed / Z/y", page.inner_text("#crumbs")
    # The drilled-into dir is named, not "."; Z/y is unsplit: one band.
    assert bands(page) == ["y"], f"bands of Z/y: {bands(page)}"
    go(page, f"{base}#tree=1&view=trends&flat=1")
    act(page, lambda: card(page, "Q").click())
    assert "in=Q" in state(page) and page.input_value("#depth") == "0", state(page)

    # Enter on a focused card works like a click.
    go(page, f"{base}#tree=1&view=trends")
    card(page, "A").focus()
    act(page, lambda: page.keyboard.press("Enter"))
    assert "in=A" in state(page) and "depth=1" in state(page), f"Enter: {state(page)}"

    # Changing the tree keeps the view.
    go(page, f"{base}#tree=1&view=trends")
    act(page, lambda: page.select_option("#tree", "2"))
    assert "view=trends" in state(page), "changing the tree left the Trends view"
    assert cards(page) == ["solo"], f"tree 2: {cards(page)}"

    # The Stack view shows a range zoomed in the Trends view.
    go(page, f"{base}#tree=1&view=trends")
    act(page, lambda: zoom(page, "trends-slider", times[1], times[2]))
    act(page, lambda: page.click("#view button[data-view=stack]"))
    start, end = page.evaluate(
        "(ts) => ts.map((t) => new Date(t).toLocaleString())", [times[1], times[2]]
    )
    shown = page.inner_text("#table-range")
    assert shown == f"{start} \u2013 {end}", f"range after switching: {shown}"
    for trends_only in ("#sort", "#min", "#flat", "#cards"):
        assert page.is_hidden(trends_only), f"{trends_only} shown in the Stack view"


def check_sets(page: Page, base: str) -> None:
    """The Set menu: one set at a time in both views, kept in the URL."""

    def options() -> list[str]:
        return page.eval_on_selector_all("#set option", "os => os.map(o => o.text)")

    go(page, f"{base}#tree=1&sflat=1&smin=0&in=A&depth=1")
    assert options() == [
        "Top level — 4 dirs",
        "Mixed — 3 dirs",
        "Partial — 1 dir",
        "Empty — 0 dirs",
    ], f"sets: {options()}"
    assert page.input_value("#set") == "Top level", "not on the first set"

    # Changing the set clears the drilled-into dir; the set survives a reload.
    act(page, lambda: page.select_option("#set", "Mixed"))
    assert "set=Mixed" in state(page) and "in" not in keys(page), state(page)
    act(page, page.reload)
    assert page.input_value("#set") == "Mixed", "set lost on reload"
    names = sorted(bands(page))
    assert names[:4] == ["A/ bytes", "A/b", "A/c", "Z/y"], f"Mixed at Depth 1: {names}"
    assert len(names) == 6, f"caf at Depth 1: {names}"

    # Partial: a stored subdir of an unsplit dir.
    go(page, f"{base}#tree=1&sflat=1&smin=0&set=Partial")
    assert bands(page) == ["Q/r"], f"Partial: {bands(page)}"
    assert page.get_attribute("#depth", "max") == "0", "Depth max for Partial"

    # A set matching nothing says so, in both views.
    for view in ("stack", "trends"):
        go(page, f"{base}#tree=1&sflat=1&smin=0&set=Empty&view={view}")
        assert page.inner_text("#stack-empty") == (
            "No directories match this set's patterns."
        ), f"{view}: no empty-set note"
        assert page.is_visible("#stack-empty"), f"{view}: empty-set note hidden"
    assert cards(page) == [] and page.is_hidden("#trends-range"), "range for Empty"
    act(page, lambda: page.click("#view button[data-view=stack]"))
    assert page.is_hidden("#chart"), "empty chart shown"
    assert page.is_hidden("#legend"), "legend shown for an empty set"
    act(page, lambda: page.select_option("#set", "Top level"))
    assert page.is_hidden("#stack-empty") and page.is_visible("#chart"), "note stuck"

    # An unknown set falls back to the first.
    go(page, f"{base}#tree=1&sflat=1&smin=0&view=trends&set=nope")
    assert page.input_value("#set") == "Top level", "unknown set not ignored"
    assert len(cards(page)) == 3, "unknown set shows no cards"

    # Set names belong to a tree: changing the tree drops the set.
    act(page, lambda: page.select_option("#set", "Mixed"))
    act(page, lambda: page.select_option("#tree", "2"))
    assert "set=" not in state(page), f"set kept across trees: {state(page)}"
    assert options() == ["Top level — 1 dir"], f"tree 2 sets: {options()}"
    act(page, lambda: page.select_option("#tree", "1"))
    assert page.input_value("#set") == "Top level", "tree 1 not back on its first set"


def check_sort_and_unchanged(page: Page, base: str) -> None:
    """Sorting cards, and hiding unchanged and small ones unless asked."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["series"]["Z/y"][0] = 2**30
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    go(page, f"{base}#tree=1&view=trends&set=Mixed")
    assert len(cards(page)) == 2, f"unchanged card shown: {cards(page)}"
    hidden = page.inner_text("#trends-hidden")
    want = (
        "1 directory filtered out (unchanged in this range, or under 1.00 GiB at peak)."
    )
    assert hidden == want, hidden

    act(page, lambda: page.check("#flat"))
    assert "flat=1" in state(page) and "Z/y" in cards(page), (
        "Show unchanged did nothing"
    )
    assert page.is_hidden("#trends-hidden"), "hidden count shown with none hidden"

    # Min GiB compares each card's peak in the range (Z/y 1, café 200 GiB).
    act(page, lambda: page.fill("#min", "1.5"))
    act(page, lambda: page.press("#min", "Enter"))
    assert "min=1.5" in state(page) and "Z/y" not in cards(page), cards(page)
    hidden = page.inner_text("#trends-hidden")
    assert hidden == "1 directory filtered out (under 1.50 GiB at peak).", hidden
    act(page, lambda: page.fill("#min", "200"))
    act(page, lambda: page.press("#min", "Enter"))
    assert len(cards(page)) == 2, f"peak of 200 GiB filtered: {cards(page)}"
    act(page, lambda: page.fill("#min", ""))
    act(page, lambda: page.press("#min", "Enter"))
    assert "min" not in keys(page) and "Z/y" in cards(page), state(page)

    act(page, lambda: page.select_option("#sort", "name"))
    # Z/y is unchanged (flat), so it comes after the others even by name.
    names = cards(page)
    assert names[0] == "A" and names[2] == "Z/y" and len(names) == 3, (
        f"by name: {names}"
    )
    act(page, lambda: page.select_option("#sort", "size"))
    assert "sort=size" in state(page), f"sort not in the URL: {state(page)}"
    act(page, page.reload)
    assert page.input_value("#sort") == "size" and page.is_checked("#flat"), (
        "lost on reload"
    )
    assert cards(page)[0] == "A" and cards(page)[2] == "Z/y", f"by size: {cards(page)}"

    # With every card unchanged, the count says where they went.
    tree["series"]["A"][0] = 2**40
    caf = next(p for p in tree["series"] if p.startswith("caf") and "/" not in p)
    tree["series"][caf][0] = 2**30
    page.goto(f"{base}#tree=1&view=trends&set=Mixed")
    act(page, page.reload)  # a hash change alone keeps the loaded file
    text = page.inner_text("#trends-hidden")
    assert cards(page) == [] and "Show unchanged" in text, f"all hidden: {text}"
    go(page, f"{base}#tree=1&view=trends&sort=size&flat=1&min=2")

    # Reset is the Stack view's: it keeps the Trends view's state.
    act(page, lambda: page.click("#view button[data-view=stack]"))
    act(page, lambda: page.click("#reset"))
    assert {"sort", "flat", "min"} <= keys(page), state(page)


def check_metric(page: Page, base: str) -> None:
    """The Bytes / Entries toggle: both views chart rentries, with their own
    Min filters, and the rest of the view state survives the switch.

    Tree 1's rentries: A 3000 and Q 40000, constant; Z 500 -> 300; café
    10000 -> 15000; own entries A/ 1, Z/ 500, 100, 101.
    """
    caf = "caf�"  # Playwright's view of café's name
    go(page, f"{base}#tree=1")
    act(page, lambda: page.click("#metric button[data-metric=rentries]"))
    assert "metric=rentries" in state(page), state(page)
    # Min entries, default 1000: A and Q unchanged, Z under 1000 at peak.
    assert bands(page) == [caf], f"default entries filters: {bands(page)}"
    summary = page.inner_text("#summary")
    want = "3 filtered out (unchanged in this range, or under 1.00 k entries at peak)"
    assert "15.0 k entries in 1 band" in summary and want in summary, summary
    assert page.input_value("#smin") == "1000", page.input_value("#smin")
    assert "Min entries" in page.inner_text("#controls"), "Min not labeled entries"
    head = page.inner_text("#legend-head")
    assert "Entries" in head and "Size" not in head, head
    assert page.is_visible("text=rentries: rfiles + rsubdirs"), "header not switched"

    # Min entries is its own: changing it keeps Min GiB, and the reverse.
    act(page, lambda: page.fill("#smin", "250"))
    act(page, lambda: page.press("#smin", "Enter"))
    assert "sminent=250" in state(page) and "smin=" not in state(page), state(page)
    assert bands(page) == [caf, "Z"], f"Min entries 250: {bands(page)}"
    act(page, lambda: page.click("#metric button[data-metric=rbytes]"))
    assert "metric" not in keys(page) and "sminent=250" in state(page), state(page)
    assert page.input_value("#smin") == "1", page.input_value("#smin")
    assert "Min GiB" in page.inner_text("#controls"), "Min not labeled GiB"
    assert bands(page) == ["A", caf, "Z"], f"back to bytes: {bands(page)}"

    # Own-files bands and legend values are counts.
    go(page, f"{base}#tree=1&metric=rentries&sflat=1&sminent=0&in=A&depth=1")
    assert set(bands(page)) == {"A/ entries", "b", "c"}, f"in A: {bands(page)}"
    values = page.eval_on_selector_all(
        "#legend li", "els => els.map(e => e.querySelector('.value').textContent)"
    )
    assert sorted(values) == ["1", "2.00 k", "999"], f"legend values: {values}"
    assert "±0" in page.inner_text("#legend"), page.inner_text("#legend")

    # Switching keeps the tree, set, drill, Depth, hides and picks.
    kept = "tree=1&set=Mixed&in=A&depth=1&hide=A/c&pick=A/b&sflat=1&smin=0&sminent=0"
    go(page, f"{base}#{kept}")
    act(page, lambda: page.click("#metric button[data-metric=rentries]"))
    pairs = set(state(page).lstrip("#").split("&"))
    assert pairs == {*kept.split("&"), "metric=rentries"}, f"switch: {state(page)}"
    assert list(series(page)) == ["A/b"], f"pick lost: {series(page)}"
    assert "A/c" in page.inner_text("#hidden-list"), "hide lost"

    # The Trends view: entry counts, with Min entries of its own (minent).
    go(page, f"{base}#tree=1&view=trends&metric=rentries")
    assert cards(page) == [caf], f"entry cards: {cards(page)}"
    card_text = page.locator("#cards .trend-card").first.inner_text()
    assert "15.0 k" in card_text and "+5.00 k" in card_text, card_text
    hidden = page.inner_text("#trends-hidden")
    want = (
        "3 directories filtered out (unchanged in this range, or under 1.00 k"
        " entries at peak)."
    )
    assert hidden == want, hidden
    act(page, lambda: page.check("#flat"))
    act(page, lambda: page.fill("#min", "0"))
    act(page, lambda: page.press("#min", "Enter"))
    assert "minent=0" in state(page) and "min=" not in state(page), state(page)
    assert len(cards(page)) == 4, f"all entry cards: {cards(page)}"

    # A data file written before rentries (with an rbytes map, not series
    # pairs): Entries is disabled, and a URL asking for it shows bytes.
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["rbytes"] = {p: pair[0] for p, pair in tree.pop("series").items()}
    del tree["metrics"]
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    page.goto(f"{base}#tree=1&metric=rentries")
    act(page, page.reload)  # a hash change alone keeps the loaded file
    assert page.is_disabled("#metric button[data-metric=rentries]"), "not disabled"
    assert bands(page) == ["A", caf, "Z"], f"old file: {bands(page)}"
    assert "Min GiB" in page.inner_text("#controls"), "old file: Min not GiB"


def check_trends_resize(page: Page, base: str) -> None:
    """Sparklines take their card's width after resizes while hidden."""
    go(page, f"{base}#tree=1&view=trends")
    act(page, lambda: page.click("#view button[data-view=stack]"))
    act(page, lambda: page.set_viewport_size({"width": 900, "height": 700}))
    act(page, lambda: page.click("#view button[data-view=trends]"))
    widths = page.eval_on_selector_all(
        "#cards .spark",
        """els => els.map((e) => [e.previousElementSibling, e].map(
            (x) => Math.round(x.getBoundingClientRect().width)))""",
    )
    assert widths and all(head == svg for head, svg in widths), f"sparks: {widths}"


def check_absent_in_range(page: Page, base: str) -> None:
    """A dir absent over the shown range (unchanged and under any Min, so
    shown only on request) says so, not that it never existed."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["series"]["Z/y"][0] = [2**30, None, None]
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    times = tree["times"]
    go(page, f"{base}#tree=1&view=trends&set=Mixed&flat=1&min=0")
    act(page, lambda: zoom(page, "trends-slider", times[1], times[2]))
    text = card(page, "Z/y").inner_text()
    assert "absent in this range" in text, f"absent card: {text}"


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
    assert bands(page) == ["solo"], f"a stale tree replaced tree 2: {bands(page)}"


def check_slow_index(page: Page, base: str) -> None:
    """A URL change while the tree index loads waits for it."""
    slow_fetch(page, "trees.json")
    page.goto(base)
    page.evaluate("location.hash = 'tree=2'")
    page.wait_for_timeout(3 * SETTLE_MS)
    assert page.is_hidden("#error"), "a URL change before the index loaded failed"
    assert bands(page) == ["solo"], f"tree 2 not shown: {bands(page)}"


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
    tree["sets"].append({"name": "New", "paths": ["A"]})  # e.g. an edited sets file
    tree["generated"] = "2026-10-01T04:00:00Z"
    index = json.loads((FIXTURE / "trees.json").read_text())
    index[0]["generated"] = tree["generated"]

    page.clock.install()
    go(page, f"{base}#tree=1")
    refresh_with(page, index, tree)
    sets = page.eval_on_selector_all("#set option", "os => os.map(o => o.value)")
    assert "New" in sets, f"regenerated file not fetched: {sets}"


def check_refresh_keeps_zoom(page: Page, base: str) -> None:
    """A zoomed start keeps its date when a refresh drops older points."""
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    times = tree["times"]  # hourly points 00:00, 01:00, 02:00
    later = "2026-10-01T03:00:00Z"
    tree["times"] = [*times[1:], later]  # 00:00 merged away, 03:00 added
    for pair in tree["series"].values():
        for series in pair:
            if isinstance(series, list):  # else one number: the same at every point
                series[:] = [*series[1:], series[-1]]
    index = json.loads((FIXTURE / "trees.json").read_text())
    tree["generated"] = index[0]["generated"] = "2026-10-01T04:00:00Z"
    index[0]["last_sample"] = later

    page.clock.install()
    go(page, f"{base}#tree=1")
    act(page, lambda: zoom(page, "chart", times[1], times[-1]))
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
    tree["series"] = {
        p: [v, 1] for p, v in {"G": g, "G/x": g, "H": h, "H/y": h}.items()
    }
    tree["sets"] = [{"name": "Top level", "paths": ["G", "H"]}]
    tree["split"] = ["G", "H"]
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    go(page, f"{base}#tree=1&sflat=1&smin=0")
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
    act(page, lambda: page.mouse.click(x, y))
    assert "in=H" in state(page), f"click between points drilled to {state(page)}"
    assert "depth=1" in state(page), f"chart drill not at Depth 1: {state(page)}"


def check_many_bands(page: Page, base: str, tree: int) -> None:
    """Top 512 at the deepest Depth: every band has a color, colors repeat
    only past the palette, also after fewer bands replace many, and the
    tooltip stays short."""
    deepest = page.get_attribute("#depth", "max")
    go(page, f"{base}#tree={tree}&depth={deepest}&top=512")
    colors = list(series(page).values())
    drawn = re.search(r" in (\d+) bands?", page.inner_text("#summary"))
    assert drawn and int(drawn[1]) == len(colors) > 0, (
        f"tree {tree}: {len(colors)} series, summary {page.inner_text('#summary')}"
    )
    assert len(colors) <= 512, f"tree {tree}: {len(colors)} bands drawn"
    assert all(colors), f"tree {tree}: a band has no color"
    assert len(set(colors)) == min(len(colors), 64), (
        f"tree {tree}: {len(set(colors))} colors for {len(colors)} bands"
    )
    rows = page.evaluate(
        """() => {
            const chart = echarts.getInstanceByDom(document.getElementById("chart"));
            const html = chart.getOption().tooltip[0].formatter([{dataIndex: 0}]);
            return (html.match(/class="tt-row"/g) ?? []).length;
        }"""
    )
    # 30 bands, one row for the rest, and the total.
    assert rows <= min(len(colors), 30) + 2, f"tree {tree}: tooltip has {rows} rows"

    # Without a reload (colors are kept in memory): the 64 most shrunk.
    act(
        page,
        lambda: page.evaluate(
            "location.hash = "
            "location.hash.replace('top=512', 'top=64') + '&lsort=-delta'"
        ),
    )
    colors = list(series(page).values())
    assert len(set(colors)) == len(colors) <= 64, (
        f"tree {tree}: {len(set(colors))} colors for {len(colors)} bands after Top 512"
    )


def gap_widths(page: Page) -> dict[str, float]:
    """The Stack chart's gap line widths, bottom first, as {band key: px}."""
    return page.evaluate(
        """() => {
            const chart = echarts.getInstanceByDom(document.getElementById("chart"));
            return Object.fromEntries(
                chart.getOption().series.map((s) => [s.id, s.lineStyle.width]));
        }"""
    )


def check_gaps(page: Page, base: str, scale: int = 1) -> None:
    """Thin bands lose the gaps on their edges, by the zoomed range, so the
    gaps never paint over them; edges between thick bands keep theirs.

    Tree 1 with Q at 4 GiB and café at 4 GiB at the first point: at about
    0.3 px per GiB, each is 1-2 px tall where it is that small. SCALE
    multiplies every size, e.g. to petabytes, where px per byte is tiny.
    """
    caf = "caf\ufffd"  # Playwright's view of café's name
    tree = json.loads((FIXTURE / "tree-1.json").read_text())
    tree["series"]["Q"][0] = 4 * 2**30
    tree["series"]["caf\udce9"][0][0] = 4 * 2**30
    for sizes in tree["series"].values():
        v = sizes[0]
        sizes[0] = [x and x * scale for x in v] if isinstance(v, list) else v * scale
    page.route("**/data/tree-1.json", lambda r: r.fulfill(json=tree))
    go(page, f"{base}#tree=1&sflat=1&smin=0")

    def want(thin: set[str]) -> dict[str, int]:
        order = list(gap_widths(page))
        return {
            key: 2 if key not in thin and (above is None or above not in thin) else 0
            for key, above in zip(order, [*order[1:], None], strict=True)
        }

    got = gap_widths(page)
    assert set(got) == {"A", "Q", "Z", caf}, f"series: {list(got)}"
    assert got == want({"Q", caf}), f"full range: {got}"
    times = tree["times"]
    act(page, lambda: zoom(page, "chart", times[1], times[2]))
    got = gap_widths(page)
    assert got == want({"Q"}), f"zoomed past café's small point: {got}"


def check_tree(page: Page, base: str, tree: int, shots: Path | None) -> None:
    """Generic checks of one tree of real data."""
    go(page, f"{base}#tree={tree}")
    if shots:
        page.screenshot(path=shots / f"tree-{tree}.png")
    assert page.input_value("#depth") == "0", f"tree {tree}: Depth not 0"
    n = len(bands(page))
    assert n <= 64, f"tree {tree}: {n} bands drawn"
    assert n or page.is_visible("#stack-empty"), f"tree {tree}: no bands, no note"
    if int(page.get_attribute("#depth", "max")) >= 1:
        go(page, f"{base}#tree={tree}&depth=1")
        assert page.input_value("#depth") == "1", f"tree {tree}: Depth 1 not shown"
        assert bands(page), f"tree {tree}: no bands at Depth 1"
        if shots:
            page.screenshot(path=shots / f"tree-{tree}-depth-1.png")
        go(page, f"{base}#tree={tree}")
    drillable = page.locator("#legend button.link")
    if drillable.count():
        act(page, drillable.first.click)
        assert "in" in keys(page), f"tree {tree}: drilling did not change the view"
        assert page.input_value("#depth") == "1", f"tree {tree}: drilled not at Depth 1"
        assert bands(page), f"tree {tree}: no bands after drilling"
    hide = page.locator("#legend button.hide")
    if hide.count():
        act(page, hide.first.click)
        assert "hide=" in state(page), f"tree {tree}: hiding did not change the view"
        act(page, page.reload)
        assert page.locator("#hidden-list button").count() == 1, "hide not restored"
        act(page, page.locator("#hidden-list button").first.click)
        assert "hide=" not in state(page), f"tree {tree}: unhiding failed"
    if n:
        # None, then tick the legend's first row: one band drawn of n.
        go(page, f"{base}#tree={tree}")
        act(page, lambda: page.click("#pick-none"))
        act(page, page.locator("#legend input.pick").first.click)
        assert len(series(page)) == 1, f"tree {tree}: picking drew {len(series(page))}"
        assert len(bands(page)) == n, f"tree {tree}: picking changed the list"
        check_many_bands(page, base, tree)

    # Entries: the same view, of rentries, under their own Min filter.
    go(page, f"{base}#tree={tree}&metric=rentries")
    assert page.is_enabled("#metric button[data-metric=rentries]"), (
        f"tree {tree}: no rentries in its file"
    )
    n = len(bands(page))
    assert n <= 64, f"tree {tree}: {n} entry bands drawn"
    assert (
        n
        or page.is_visible("#stack-empty")
        or "filtered out" in page.inner_text("#summary")
    ), f"tree {tree}: no entry bands, no note"
    if shots:
        page.screenshot(path=shots / f"tree-{tree}-entries.png")

    entries = page.evaluate("fetch('data/trees.json').then((r) => r.json())")
    sets = next(t["sets"] for t in entries if t["id"] == tree)
    go(page, f"{base}#tree={tree}&view=trends")
    if shots:
        page.screenshot(path=shots / f"tree-{tree}-trends.png", full_page=True)
    for w in sets:
        # Unchanged and small cards are filtered out, and counted, unless
        # asked for.
        go(page, f"{base}#tree={tree}&view=trends")
        act(page, lambda name=w["name"]: page.select_option("#set", name))
        shown = page.locator("#cards .trend-card").count()
        hidden = page.eval_on_selector(
            "#trends-hidden",
            "e => e.hidden ? 0 : parseInt(e.textContent.match(/\\d+/))",
        )
        act(page, lambda: page.check("#flat"))
        act(page, lambda: page.fill("#min", "0"))
        act(page, lambda: page.press("#min", "Enter"))
        n = page.locator("#cards .trend-card").count()
        where = f"tree {tree}, set {w['name']!r}"
        assert n == w["dirs"], f"{where}: {n} cards, trees.json says {w['dirs']}"
        assert shown + hidden == n, f"{where}: {shown} shown + {hidden} hidden != {n}"
        if not n:
            assert page.is_visible("#stack-empty"), f"{where}: no note without cards"
    if not any(w["dirs"] for w in sets):
        return
    # The rest needs cards: the first set with any.
    first = next(w["name"] for w in sets if w["dirs"])
    go(page, f"{base}#tree={tree}&view=trends&flat=1")
    act(page, lambda: page.select_option("#set", first))

    # Drag the slider's left handle a third of the way; cards follow.
    before = page.inner_text("#trends-range")
    box = page.locator("#trends-slider").bounding_box()
    y = box["y"] + box["height"] - 24
    page.mouse.move(box["x"] + 25, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 3, y, steps=10)
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MS)
    assert page.inner_text("#trends-range") != before, f"tree {tree}: drag did nothing"

    other = next((t["id"] for t in entries if t["id"] != tree), None)
    if other is not None:
        act(page, lambda: page.select_option("#tree", str(other)))
        assert "view=trends" in state(page), f"tree {tree}: tree change left Trends"
        go(page, f"{base}#tree={tree}&view=trends&flat=1")
        act(page, lambda: page.select_option("#set", first))
    act(page, page.locator("#cards .trend-card").first.click)
    assert "view=" not in state(page), f"tree {tree}: click-through stayed in Trends"
    assert bands(page), f"tree {tree}: Stack view not shown"


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
    p.add_argument("--shots", type=Path, help="save screenshots per real tree here")
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
            check_trends,
            check_sets,
            check_sort_and_unchanged,
            check_trends_resize,
            check_absent_in_range,
            check_slow_tree,
            check_slow_index,
            check_refresh_after_regeneration,
            check_refresh_keeps_zoom,
            check_click_between_points,
            check_gaps,
            functools.partial(check_gaps, scale=2**14),  # about 16 PiB
            check_picks,
            check_metric,
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
