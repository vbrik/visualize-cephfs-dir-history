# Conventions

- The database is read only by the reading rules of trackdir's
  [docs/schema.md](https://github.com/vbrik/track-cephfs-dir-sizes/blob/master/docs/schema.md);
  `db.SCHEMA_VERSION` must match `trackdir.meta`. dirhistory does not import
  trackdir: that schema is the only contract.
- Paths are `bytes` relative to ROOT (`b"."`), joined with `/`. They become
  `str` only in JSON, through `os.fsdecode`, written with
  `ensure_ascii=True`. Terminal messages use `generate.display`
  (backslashreplace), never fsdecode'd text.
- Sizes are integer bytes everywhere; the page formats them in IEC units
  (KiB … PiB), like `trackdir db summary`.
- Times in JSON are UTC ISO 8601 to the second (`2026-10-21T19:19:45Z`).
- `history.py` is pure (no database, no I/O) and holds the logic;
  `db.py` only queries; `generate.py` wires them and writes files. Tests
  target `history.py` and the pure parts of `generate.py` and need no
  database.
- `dirhistory-generate` prints the files it wrote to stdout; notes,
  `WARNING:` and `ERROR:` messages go to stderr.
- Data files are replaced atomically and world-readable (subject to umask).
- The page has no build step: plain ES modules, ECharts vendored in
  `web/vendor/` at a pinned version. Directory names are untrusted: insert
  them with `textContent`, or `escapeHtml` in ECharts tooltip HTML.
- `web/js/model.js` holds the page's pure data shaping; chart modules in
  `web/js/charts/` turn its output into ECharts options; `main.js` owns
  state (the URL hash) and the DOM.
- Colors come from CSS custom properties in `web/style.css`. Series use the
  validated 8-slot categorical palette in fixed order; never cycle it, and
  keep a band's color while filters change.
- Python: ruff (`ruff format`, `ruff check`) with the settings in
  `pyproject.toml`. JS: 2-space indent, double quotes, as in the existing
  files.
- Before committing page changes, run `scripts/check_web.py` (headless
  Chrome smoke test). There are no JS unit tests.
- Commit messages record the reasoning and design choices behind a change,
  so later contradictions can be spotted.
