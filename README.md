# cmp-cov

Compare local pytest coverage against a saved baseline.

## Usage

```bash
# After running pytest --cov, save current coverage as the baseline:
uvx cmp-cov save-baseline

# Later, after another pytest --cov run, diff against the baseline:
uvx cmp-cov diff
```

Both subcommands take an optional baseline name (default `default`); each name
is a separate slot, so multiple baselines per project coexist:

```bash
cmp-cov save-baseline main
cmp-cov save-baseline before-refactor
cmp-cov diff main
```

`save-baseline` snapshots the current `.coverage` (SQLite) and source files into
`~/.cache/cmp-coverage/<encoded-project-path>/<name>/`. `diff` regenerates current
coverage XML, translates baseline line numbers through the snapshotted sources
(so source edits between save and diff are handled), and prints per-line diffs
grouped by category. Exit code is non-zero if total coverage dropped or any
regression / new uncovered line was found.

Each output location is rendered as `path:line` so it is clickable in editors
that support terminal links (e.g. VSCode).

## Output categories

- `↓ covered → uncovered` — lines that were covered in baseline and are not
  anymore.
- `+ new uncovered` — lines added since baseline that are not covered.
- `↓ covered line removed` — lines that were covered in baseline and have been
  deleted from the source. Path points at the cached baseline snapshot.
- `↑ uncovered → covered` — lines that were not covered in baseline and are
  covered now.
- `+ new covered` — lines added since baseline that are covered.
- `- removed line` — non-covered lines that were removed.
- `! unmeasured in baseline (uncovered)` — lines in files that were not
  measured by the baseline run, currently not covered.
- `! unmeasured in baseline (covered)` — same, but covered now (compact view).
