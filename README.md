# dirhistory — CephFS directory size history charts

A static website that charts how the sizes of directories on a CephFS
filesystem change over time. It shows which directories grow and shrink,
using the samples that [trackdir](https://github.com/vbrik/track-cephfs-dir-sizes)
records in PostgreSQL from CephFS recursive statistics (`ceph.dir.rbytes`).

- `dirhistory-generate` reads the trackdir database and writes JSON data
  files. Run it periodically.
- `web/` is the page: a stacked area chart per tree, with grouping,
  drill-down, hiding, filters, a zoomable time range and a table view.
  Any static web server can serve it.

Charts stack the **own size** of **units**. A directory's own size is the
bytes of the files directly in it: its rbytes minus its subdirectories'
rbytes. A unit is a directory whose own size changed by at least a threshold
(default 10 GiB). Own sizes never overlap, so the bands add up and no growth
is counted twice. See [docs/terms.md](docs/terms.md) for the terms and
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
    dirhistory-generate -v                    # writes web/data/
    python -m http.server -d web 8000         # then open http://localhost:8000/

Put the database password in `~/.pgpass` or `PGPASSWORD`, never in `--db`.
The database is only read. See `dirhistory-generate --help` for the options
(threshold, unit limit, trees, output directory).

The page needs HTTP: opened as a `file://` URL, it cannot fetch its data.

## Deploy

1. Install as above into `/opt/dirhistory`.
2. Copy [deploy/dirhistory.service](deploy/dirhistory.service) and
   [deploy/dirhistory.timer](deploy/dirhistory.timer) to
   `/etc/systemd/system/`, set the database URL and password file, and run
   `systemctl enable --now dirhistory.timer`. It regenerates the data hourly.
3. Serve `/opt/dirhistory/web` with any web server; see
   [deploy/nginx.conf.example](deploy/nginx.conf.example). Put access
   control in front of it: directory names and sizes are internal data.

## Using the page

| Control | Effect |
|---|---|
| Tree | Which sampled tree to show. |
| Group by | Sum units into their ancestor N levels down; click a group (in the legend or the chart) to show its units, and the path above the chart to go back. |
| Bands | How many bands to name (up to 8); the rest are *Smaller units*. |
| Min peak | Bands whose peak in the shown range is smaller join *Smaller units* (e.g. `1TiB`). |
| Rest of tree | Also draw the bytes in no unit. Usually far larger than the units. |
| hide | Remove a band's bytes from the chart; click the chip to bring it back. |
| Slider under the chart | Zoom the time range; ranking, the table and changes follow it. |

The URL keeps the view, so it can be bookmarked or shared.

## Development

    pytest                          # tests; no database needed
    ruff format && ruff check
    python scripts/check_web.py     # headless Chrome smoke test of web/ (needs data)

Conventions are in [docs/conventions.md](docs/conventions.md). To add a
view, add a chart module under `web/js/charts/` that builds an ECharts
option from `model.js` output, as `stackedArea.js` does, and a switch in
`web/js/main.js`.

## License

MIT; ECharts is Apache-2.0 (`web/vendor/LICENSE-echarts.txt`).
