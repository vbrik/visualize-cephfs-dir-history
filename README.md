# dirhistory — CephFS directory size history charts

A **static** website that charts how the sizes and file counts of directories
on a CephFS filesystem change over time. It shows which directories grow and
shrink, using the samples that [trackdir](https://github.com/vbrik/track-cephfs-dir-sizes)
records in PostgreSQL from CephFS recursive statistics (`ceph.dir.rbytes`,
`ceph.dir.rfiles`, `ceph.dir.rsubdirs`).

- `dirhistory-generate` reads the trackdir database and writes JSON data
  files. Run it periodically.
- `web/` is the page, per tree, with two views of a **set** of directories:
  *Stack*, a stacked area chart of the set's directories, split into their
  subdirectories by Depth, with drill-down, hiding, a zoomable time range and
  a table; and *Trends*, a card per directory with its size over time. Any
  static web server can serve it.

Sets are lists of directories you choose in a TOML file (`--sets`); every tree
also has the set **Top level**, its top-level directories. Stack bands are the
directories' rbytes, the size of the whole subtree; `dir/ bytes` is the
files directly in a split directory. Bands add up, unless a set holds a
directory and one of its own subdirectories: those bytes can count twice.
The *Entries* toggle charts rentries instead, the number of files and
directories in the subtree (rfiles + rsubdirs, the directory itself
included), in both views. *Change* charts how fast each changes, per day,
instead of how big it is: in the Stack view growth stacks above zero and
shrinkage below, and Trends cards share one scale.
See [docs/terms.md](docs/terms.md) for the terms and
[docs/design.md](docs/design.md) for the design, the data format and the
known risks.

## Requirements

- Python 3.12+ and a trackdir database at schema version 4.
- A browser with ES module support. No internet access is needed: ECharts
  is vendored in `web/vendor/`.

## Install

    git clone <repository-url> && cd visualize-cephfs-dir-history
    python3 -m venv venv && . venv/bin/activate
    pip install -e '.[dev]'    # or `pip install -e .` without the test tools

## Generate and view

    export TRACKDIR_DB=postgresql://trackdir@db.example.org/trackdir
    cat > sets.toml <<'EOF'                   # or copy deploy/sets.toml.example and edit
    budget = 100000
    [tree."/fs/path".set."Top"]
    dirs = [{ match = ["*"], depth = 1 }]
    EOF
    dirhistory-generate --sets sets.toml -v   # writes web/data/
    python -m http.server -d web 8000         # then open http://localhost:8000/

Put the database password in `~/.pgpass` or `PGPASSWORD`, never in `--db`.
The database is only read. See `dirhistory-generate --help` for the options:
sets file, trees, output directory, and time resolution.

`--sets FILE` is required and names the trees to generate; other trees are
skipped. Per tree, it gives sets of directories (patterns relative to the
tree root), how many levels below each the page can split and drill, and a
**budget**: the most directories the tree's data file stores, set at the top
of the file for every tree or in a tree's own section. See
[deploy/sets.toml.example](deploy/sets.toml.example). A tree whose sets
cover more directories than its budget is stored breadth-first, every level
from the tree root whole until the budget runs out, and saved partial, with
a warning; the page marks a directory whose subdirectories were left out
with `*`.

A tree has at most 5000 time points (`--max-points`). By default the whole
history gets one bucket width, the finest of 1h, 6h, 1d, 2d or 1w that fits:
hourly for about 7 months, then 6-hourly to about 3.4 years, then daily.
`--recent` keeps that much recent history hourly instead.

The page needs HTTP: opened as a `file://` URL, it cannot fetch its data.

## Deploy

1. Install as above into `/opt/dirhistory`.
2. Create the service's account, its data directory and its password file.
   The data directory must exist before the service first starts: systemd
   fails a unit whose `ReadWritePaths` is missing, and `ProtectSystem=strict`
   keeps the rest of `/opt` read-only. libpq ignores a password file that
   group or others can read.

       useradd --system --no-create-home --shell /usr/sbin/nologin dirhistory
       install -d -o dirhistory -g dirhistory -m 755 /opt/dirhistory/web/data
       install -d -m 755 /etc/dirhistory
       install -o dirhistory -g dirhistory -m 600 /dev/null /etc/dirhistory/pgpass
       # then add a line to it: db.example.org:5432:trackdir:trackdir:PASSWORD
       install -m 644 deploy/sets.toml.example /etc/dirhistory/sets.toml
       # then edit it: a section naming no tree is an error, so replace the
       # example's trees with yours (only they are generated), each with a
       # budget; the service passes --sets, and fails if the file is missing

3. Copy [deploy/dirhistory.service](deploy/dirhistory.service) and
   [deploy/dirhistory.timer](deploy/dirhistory.timer) to
   `/etc/systemd/system/`, set the database URL and password file, and run
   `systemctl enable --now dirhistory.timer`. It regenerates the data hourly.
4. Serve `/opt/dirhistory/web` with any web server; see
   [deploy/nginx.conf.example](deploy/nginx.conf.example). Put access
   control in front of it: directory names and sizes are internal data.
5. Upgrading an install that passes `--watch`: edit `ExecStart` to pass
   `--sets` (and `systemctl daemon-reload`), and convert the file: a
   `watch = [...]` list becomes a set's
   `dirs = [{ match = [...], depth = 0 }]` (the generator's error shows the
   form). Then run the service once for all trees (no `--tree`). Until it
   succeeds, the new page cannot read the old data files, and a `--tree N`
   run leaves the other trees' old files.
6. Upgrading an install from before budgets: add `budget = N` to the sets
   file (at the top, or per tree; `--max-dirs` was 100000), give every tree
   to chart a section (an empty `[tree."/fs/path".set]` gives it only Top
   level), and drop `--max-dirs` from any script: it is no longer an option.
   Until then every run fails.

## Using the page

| Control | Effect |
|---|---|
| Tree | Which sampled tree to show. Changing it shows the tree's first set. |
| View | *Stack*: the stacked chart of the set's directories. *Trends*: a card per set directory, each on its own scale; click one to open it in Stack. |
| Bytes, Entries | Both views: chart rbytes (sizes) or rentries (files plus directories, each directory counting itself; the own-files band is then `dir/ entries`, the files directly in a directory plus 1). Everything else stays as it is; Min has a separate value per metric. Entries is disabled for a data file written before it existed, until the next `dirhistory-generate`. |
| Set | Which of the tree's sets to show, with its number of directories: Top level, then the sets of the `--sets` file. |
| Depth | Stack: split each directory this many levels into its subdirectories; the files directly in a split directory are the band `dir/ bytes`. The most is the levels the data file stores below the shown directories. A name ending in `*` cannot be split: the tree's budget ran out before its subdirectories. |
| Top | Stack: draw only this many bands (1–512, default 64): the first in the legend's sort, by default those whose size changed most in the shown range; the summary counts the rest. Past 64 bands, colors repeat. |
| Legend tick boxes, None, All | Stack: draw only the ticked bands; *None* unticks all, to tick just the few to compare. Unticked bands stay listed, below the ticked ones, with their size and change; the chart's scale, the summary and the table follow the ticked ones. Changing Tree, Set or Depth, or drilling, ticks all again. |
| Legend Dir, Size (Entries), Δ | Stack: sort the legend by name, by value at the shown range's end, or by change (Δ) over the shown range; click again to reverse, a third time for chart order (the top of the stack first: the chart stacks the least changed at the bottom, the biggest movers on top). When there are more bands than Top, the sort also chooses which are drawn: e.g. Δ reversed draws the most shrunk. The chart's stacking does not change. |
| Band (legend or chart) | Stack: click a band of a directory that can be split to show only what is inside it, one level down; the path above the chart goes back. |
| hide | Stack: remove a band's bytes from the chart, at every Depth: a directory's, with everything in it, or `dir/ bytes`, the files directly in it. Click the chip to bring it back. |
| Min GiB, Min entries | Both views, each its own, and per metric: hide bands (Stack) or cards (Trends) whose largest value in the shown range is under this many GiB (default 1) or entries (default 1000); 0 shows all. Ticked bands are never hidden. The summary (Stack) or the line above the cards (Trends) counts what was filtered out. |
| Show unchanged | Both views, each its own: also show bands or cards whose value did not change in the shown range; off by default. |
| Reset | Stack: restore Depth, Top, Min GiB and Min entries, Show unchanged, drill-down, hiding, ticks and the legend's order. |
| Sort | Trends: card order in the shown range, growing dirs first, then shrinking, then those that end where they started; within each, largest first by *Span* (max − min), *Peak* (max) or *Current size* (at the range's end), or by *Name*. |
| Slider under the chart | Zoom the time range; ranking, the table and changes follow it. Both views share the range. |

The URL keeps the view, except the zoomed time range, so it can be bookmarked
or shared.

## Development

    pytest                          # tests; no database needed
    ruff format && ruff check
    python scripts/check_web.py     # headless Chrome checks of web/: fixture + web/data

Conventions are in [docs/conventions.md](docs/conventions.md). The sets
file's format and pattern matching are in `src/dirhistory/sets.py`. To add a
view, add a chart module under `web/js/charts/` that builds an ECharts
option (or, for many small charts, SVG path data, as `trendCard.js` does)
from `model.js` output, as `stackedArea.js` does, and a switch in
`web/js/main.js`.

## License

MIT; ECharts is Apache-2.0 (`web/vendor/LICENSE-echarts.txt`).
