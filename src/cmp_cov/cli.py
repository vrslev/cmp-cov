"""Compare local pytest coverage against a saved baseline.

Subcommands:
  save-baseline - convert current .coverage (in git toplevel of CWD) to XML and
                  store as the baseline for this project in ~/.cache/cmp-coverage/.
  diff          - convert current .coverage to XML and diff against the saved
                  baseline.

One baseline per project path. `save-baseline` overwrites the previous baseline.
"""

import argparse
import contextlib
import datetime
import difflib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import typing
import urllib.parse
import xml.etree.ElementTree as ElementTree

import coverage


CACHE_DIR: typing.Final = pathlib.Path.home() / ".cache" / "cmp-coverage"
COVERAGE_EPSILON: typing.Final = 0.01
MAX_RUN_GAP: typing.Final = 2
ABS_PATH_PREFIX: typing.Final = "_abs"

BUCKET_REGRESSION: typing.Final = "↓ covered → uncovered"
BUCKET_NEW_UNCOVERED: typing.Final = "+ new uncovered"
BUCKET_DELETED_COVERED: typing.Final = "↓ covered line removed"
BUCKET_IMPROVEMENT: typing.Final = "↑ uncovered → covered"
BUCKET_NEW_COVERED: typing.Final = "+ new covered"
BUCKET_REMOVED_LINE: typing.Final = "- removed line"
BUCKET_UNMEASURED_UNCOVERED: typing.Final = "! unmeasured in baseline (uncovered)"
BUCKET_UNMEASURED_COVERED: typing.Final = "! unmeasured in baseline (covered)"

BUCKETS_ORDER: typing.Final[tuple[str, ...]] = (
    BUCKET_REGRESSION,
    BUCKET_NEW_UNCOVERED,
    BUCKET_DELETED_COVERED,
    BUCKET_IMPROVEMENT,
    BUCKET_NEW_COVERED,
    BUCKET_REMOVED_LINE,
    BUCKET_UNMEASURED_UNCOVERED,
    BUCKET_UNMEASURED_COVERED,
)
COMPACT_BUCKETS: typing.Final[frozenset[str]] = frozenset({BUCKET_UNMEASURED_COVERED})
REGRESSION_BUCKETS: typing.Final[frozenset[str]] = frozenset({
    BUCKET_REGRESSION,
    BUCKET_NEW_UNCOVERED,
    BUCKET_DELETED_COVERED,
})


def find_project_root() -> pathlib.Path:
    process_result: typing.Final = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        text=True,
        capture_output=True,
    )
    if process_result.returncode != 0:
        sys.exit(f"FAIL: not inside a git repo ({process_result.stderr.strip()})")
    return pathlib.Path(process_result.stdout.strip())


def build_cache_dir(project_root: pathlib.Path) -> pathlib.Path:
    return CACHE_DIR / urllib.parse.quote(str(project_root), safe="")


def build_cache_xml_path(project_root: pathlib.Path) -> pathlib.Path:
    return build_cache_dir(project_root) / "coverage.xml"


def build_cache_sources_dir(project_root: pathlib.Path) -> pathlib.Path:
    return build_cache_dir(project_root) / "sources"


def make_safe_subpath(filename: str) -> pathlib.Path:
    filename_path: typing.Final = pathlib.Path(filename)
    if filename_path.is_absolute():
        return pathlib.Path(ABS_PATH_PREFIX, *filename_path.parts[1:])
    return filename_path


def resolve_source_path(project_root: pathlib.Path, filename: str) -> pathlib.Path:
    filename_path: typing.Final = pathlib.Path(filename)
    return filename_path if filename_path.is_absolute() else project_root / filename_path


@contextlib.contextmanager
def change_directory(target_dir: pathlib.Path) -> typing.Iterator[None]:
    previous_dir: typing.Final = pathlib.Path.cwd()
    os.chdir(target_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def generate_coverage_xml(project_root: pathlib.Path, dest_path: pathlib.Path) -> None:
    coverage_db: typing.Final = project_root / ".coverage"
    if not coverage_db.exists():
        sys.exit(f"FAIL: no .coverage in {project_root}; run pytest --cov first.")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with change_directory(project_root):
        coverage_obj: typing.Final = coverage.Coverage(data_file=str(coverage_db))
        coverage_obj.load()
        coverage_obj.xml_report(outfile=str(dest_path), ignore_errors=False)


def parse_coverage_xml(xml_path: pathlib.Path) -> tuple[float, dict[str, dict[int, int]]]:
    tree_root: typing.Final = ElementTree.parse(xml_path).getroot()
    line_rate: typing.Final = tree_root.get("line-rate")
    if line_rate is None:
        sys.exit(f"FAIL: malformed coverage XML at {xml_path} (no line-rate attribute)")
    total_pct: typing.Final = float(line_rate) * 100
    file_to_lines: dict[str, dict[int, int]] = {}
    for class_element in tree_root.iter("class"):
        filename = class_element.get("filename")
        if not filename:
            continue
        lineno_to_hits: dict[int, int] = {}
        for line_element in class_element.iter("line"):
            line_number = int(line_element.get("number", "0"))
            if line_number:
                lineno_to_hits[line_number] = int(line_element.get("hits", "0"))
        file_to_lines[filename] = lineno_to_hits
    return total_pct, file_to_lines


def snapshot_sources_to_cache(
    project_root: pathlib.Path,
    filenames: typing.Iterable[str],
    dest_dir: pathlib.Path,
) -> int:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)
    snapshot_count = 0
    for filename in filenames:
        source_path = resolve_source_path(project_root, filename)
        if not source_path.exists():
            continue
        target_path = dest_dir / make_safe_subpath(filename)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        snapshot_count += 1
    return snapshot_count


def map_baseline_lines(baseline_src: pathlib.Path, head_src: pathlib.Path) -> dict[int, int | None]:
    baseline_lines: typing.Final = baseline_src.read_text(errors="replace").splitlines()
    head_lines: typing.Final = head_src.read_text(errors="replace").splitlines()
    matcher: typing.Final = difflib.SequenceMatcher(a=baseline_lines, b=head_lines, autojunk=False)
    lineno_mapping: dict[int, int | None] = {}
    for opcode_kind, base_start, base_end, head_start, _ in matcher.get_opcodes():
        if opcode_kind == "equal":
            for offset in range(base_end - base_start):
                lineno_mapping[base_start + offset + 1] = head_start + offset + 1
        else:
            for base_index in range(base_start, base_end):
                lineno_mapping[base_index + 1] = None
    return lineno_mapping


def translate_baseline(
    baseline_files: dict[str, dict[int, int]],
    sources_dir: pathlib.Path,
    project_dir: pathlib.Path,
) -> tuple[dict[str, dict[int, int]], dict[str, list[int]], list[str]]:
    translated_files: dict[str, dict[int, int]] = {}
    deleted_covered: dict[str, list[int]] = {}
    files_without_snapshot: list[str] = []
    for filename, lineno_to_hits in baseline_files.items():
        baseline_src = sources_dir / make_safe_subpath(filename)
        head_src = resolve_source_path(project_dir, filename)
        if not baseline_src.exists() or not head_src.exists():
            files_without_snapshot.append(filename)
            translated_files[filename] = dict(lineno_to_hits)
            continue
        lineno_mapping = map_baseline_lines(baseline_src, head_src)
        translated_lines: dict[int, int] = {}
        deleted_lines: list[int] = []
        for baseline_lineno, hit_count in lineno_to_hits.items():
            mapped_lineno = lineno_mapping.get(baseline_lineno)
            if mapped_lineno is not None:
                translated_lines[mapped_lineno] = hit_count
            elif hit_count > 0:
                deleted_lines.append(baseline_lineno)
        translated_files[filename] = translated_lines
        if deleted_lines:
            deleted_covered[filename] = sorted(deleted_lines)
    return translated_files, deleted_covered, files_without_snapshot


def compute_line_runs(lineno_list: list[int]) -> list[tuple[int, int]]:
    if not lineno_list:
        return []
    sorted_linenos: typing.Final = sorted(lineno_list)
    runs_acc: list[tuple[int, int]] = []
    run_start = run_prev = sorted_linenos[0]
    for current_lineno in sorted_linenos[1:]:
        if current_lineno - run_prev - 1 <= MAX_RUN_GAP:
            run_prev = current_lineno
            continue
        runs_acc.append((run_start, run_prev - run_start + 1))
        run_start = run_prev = current_lineno
    runs_acc.append((run_start, run_prev - run_start + 1))
    return runs_acc


def categorize_changes(
    baseline_files: dict[str, dict[int, int]],
    head_files: dict[str, dict[int, int]],
    deleted_covered: dict[str, list[int]],
) -> dict[str, dict[str, list[int]]]:
    buckets: dict[str, dict[str, list[int]]] = {bucket_label: {} for bucket_label in BUCKETS_ORDER}
    for filename, deleted_lines in deleted_covered.items():
        buckets[BUCKET_DELETED_COVERED][filename] = sorted(deleted_lines)
    for filename in sorted(set(baseline_files) | set(head_files)):
        baseline_lines = baseline_files.get(filename, {})
        head_lines = head_files.get(filename, {})
        if filename not in baseline_files:
            uncovered_linenos = sorted(lineno for lineno, hit_count in head_lines.items() if hit_count == 0)
            covered_linenos = sorted(lineno for lineno, hit_count in head_lines.items() if hit_count > 0)
            if uncovered_linenos:
                buckets[BUCKET_UNMEASURED_UNCOVERED][filename] = uncovered_linenos
            if covered_linenos:
                buckets[BUCKET_UNMEASURED_COVERED][filename] = covered_linenos
            continue
        common_linenos = set(baseline_lines) & set(head_lines)
        head_only_linenos = set(head_lines) - set(baseline_lines)
        per_category: dict[str, list[int]] = {
            BUCKET_REGRESSION:    [lineno for lineno in common_linenos if baseline_lines[lineno] > 0 and head_lines[lineno] == 0],
            BUCKET_NEW_UNCOVERED: [lineno for lineno in head_only_linenos if head_lines[lineno] == 0],
            BUCKET_IMPROVEMENT:   [lineno for lineno in common_linenos if baseline_lines[lineno] == 0 and head_lines[lineno] > 0],
            BUCKET_NEW_COVERED:   [lineno for lineno in head_only_linenos if head_lines[lineno] > 0],
            BUCKET_REMOVED_LINE:  list(set(baseline_lines) - set(head_lines)),
        }
        for bucket_label, lineno_list in per_category.items():
            if lineno_list:
                buckets[bucket_label][filename] = sorted(lineno_list)
    return buckets


def render_buckets(
    buckets: dict[str, dict[str, list[int]]],
    sources_dir: pathlib.Path | None,
) -> None:
    for bucket_label in BUCKETS_ORDER:
        files_to_linenos = buckets[bucket_label]
        if not files_to_linenos:
            continue
        total_lines = sum(len(lineno_list) for lineno_list in files_to_linenos.values())
        if bucket_label in COMPACT_BUCKETS:
            print(f"\n{bucket_label} ({total_lines} lines in {len(files_to_linenos)} files):")
            for filename, lineno_list in files_to_linenos.items():
                print(f"  {filename}: {len(lineno_list)} lines")
            continue
        path_prefix = sources_dir if bucket_label == BUCKET_DELETED_COVERED else None
        runs_per_file = [
            (filename, run_start, run_length)
            for filename, lineno_list in files_to_linenos.items()
            for run_start, run_length in compute_line_runs(lineno_list)
        ]
        print(f"\n{bucket_label} ({total_lines} lines, {len(runs_per_file)} runs):")
        for filename, run_start, run_length in runs_per_file:
            span = str(run_start) if run_length == 1 else f"{run_start}-{run_start + run_length - 1}"
            display_path = (
                str(path_prefix / make_safe_subpath(filename))
                if path_prefix is not None
                else filename
            )
            print(f"  {display_path}:{span}")


def handle_save_baseline() -> int:
    project_root: typing.Final = find_project_root()
    xml_output_path: typing.Final = build_cache_xml_path(project_root)
    generate_coverage_xml(project_root, xml_output_path)
    total_pct, baseline_files = parse_coverage_xml(xml_output_path)
    sources_dir: typing.Final = build_cache_sources_dir(project_root)
    snapshot_count: typing.Final = snapshot_sources_to_cache(project_root, baseline_files.keys(), sources_dir)
    print(f"Saved baseline for {project_root}")
    print(f"  path:    {xml_output_path}")
    print(f"  sources: {snapshot_count} files in {sources_dir}")
    print(f"  total:   {total_pct:.2f}%")
    return 0


def handle_diff() -> int:
    project_root: typing.Final = find_project_root()
    baseline_xml_path: typing.Final = build_cache_xml_path(project_root)
    if not baseline_xml_path.exists():
        sys.exit(f"FAIL: no baseline for {project_root}. Run `save-baseline` first.")
    sources_dir: typing.Final = build_cache_sources_dir(project_root)
    with tempfile.TemporaryDirectory(prefix="cmp-cov-") as tmp_dir:
        head_xml_path = pathlib.Path(tmp_dir) / "head.xml"
        generate_coverage_xml(project_root, head_xml_path)
        baseline_total, baseline_files_raw = parse_coverage_xml(baseline_xml_path)
        head_total, head_files = parse_coverage_xml(head_xml_path)

    if sources_dir.exists():
        baseline_files, deleted_covered, files_without_snapshot = translate_baseline(
            baseline_files_raw, sources_dir, project_root,
        )
    else:
        baseline_files = baseline_files_raw
        deleted_covered = {}
        files_without_snapshot = list(baseline_files_raw)

    saved_at: typing.Final = datetime.datetime.fromtimestamp(baseline_xml_path.stat().st_mtime)
    print(f"Project:  {project_root}")
    print(f"Baseline: {baseline_xml_path}")
    print(f"Saved:    {saved_at:%Y-%m-%d %H:%M:%S}")

    total_delta: typing.Final = head_total - baseline_total
    print(f"\nTotal: {baseline_total:.2f}% → {head_total:.2f}% ({total_delta:+.2f})")
    if files_without_snapshot:
        print(
            f"WARN: {len(files_without_snapshot)} file(s) had no source snapshot; "
            f"using naive line compare for them.",
            file=sys.stderr,
        )

    buckets: typing.Final = categorize_changes(baseline_files, head_files, deleted_covered)
    has_any_changes: typing.Final = any(buckets.values())
    render_buckets(buckets, sources_dir if sources_dir.exists() else None)
    if not has_any_changes:
        print("\nNo per-line changes.")

    has_regression: typing.Final = any(buckets[bucket_label] for bucket_label in REGRESSION_BUCKETS)
    is_passing: typing.Final = total_delta >= -COVERAGE_EPSILON and not has_regression
    return 0 if is_passing else 1


def main() -> int:
    arg_parser: typing.Final = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subcommands: typing.Final = arg_parser.add_subparsers(dest="cmd", required=True)
    subcommands.add_parser("save-baseline", help="Save current coverage as the project baseline.")
    subcommands.add_parser("diff", help="Diff current coverage against the saved baseline.")
    cli_args: typing.Final = arg_parser.parse_args()
    return handle_save_baseline() if cli_args.cmd == "save-baseline" else handle_diff()


if __name__ == "__main__":
    sys.exit(main())
