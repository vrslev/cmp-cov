import pathlib
import urllib.parse

from tests.conftest import write_coverage_data, write_source


SOURCE_FIVE_LINES = "x = 1\ny = 2\nz = 3\nprint(x)\nprint(y)\n"
SOURCE_THREE_LINES = "x = 1\ny = 2\nz = 3\n"


def cache_subdir(cache_dir: pathlib.Path, project_dir: pathlib.Path) -> pathlib.Path:
    return cache_dir / urllib.parse.quote(str(project_dir), safe="")


def baseline_subdir(
    cache_dir: pathlib.Path, project_dir: pathlib.Path, baseline_name: str = "default",
) -> pathlib.Path:
    return cache_subdir(cache_dir, project_dir) / urllib.parse.quote(baseline_name, safe="")


def test_save_baseline_creates_cache_files(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})

    exit_code, stdout, _ = cli_runner(project_dir, "save-baseline")

    assert exit_code == 0
    baseline = baseline_subdir(cache_dir, project_dir)
    assert (baseline / "coverage.xml").is_file()
    assert (baseline / "sources" / "foo.py").is_file()
    assert (baseline / "sources" / "foo.py").read_text() == SOURCE_FIVE_LINES
    assert "Saved baseline 'default'" in stdout
    assert "total:   100.00%" in stdout


def test_save_baseline_with_explicit_name(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})

    exit_code, stdout, _ = cli_runner(project_dir, "save-baseline", "main")

    assert exit_code == 0
    assert "Saved baseline 'main'" in stdout
    assert (baseline_subdir(cache_dir, project_dir, "main") / "coverage.xml").is_file()
    assert not baseline_subdir(cache_dir, project_dir, "default").exists()


def test_named_baselines_coexist(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline", "main")

    write_coverage_data(project_dir, {"foo.py": [1, 2]})
    cli_runner(project_dir, "save-baseline", "feature")

    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    main_exit, main_stdout, _ = cli_runner(project_dir, "diff", "main")
    feature_exit, feature_stdout, _ = cli_runner(project_dir, "diff", "feature")

    assert main_exit == 0
    assert "Total: 100.00% → 100.00%" in main_stdout
    assert feature_exit == 0
    assert "Total: 40.00% → 100.00%" in feature_stdout


def test_save_baseline_overwrites_existing(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    write_coverage_data(project_dir, {"foo.py": [1, 2, 3]})
    exit_code, stdout, _ = cli_runner(project_dir, "save-baseline")

    assert exit_code == 0
    assert "total:   60.00%" in stdout


def test_save_baseline_outside_git_repo_fails(tmp_path, cache_dir, cli_runner):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    exit_code, _, stderr = cli_runner(not_a_repo, "save-baseline")

    assert exit_code == 1
    assert "not inside a git repo" in stderr


def test_save_baseline_no_coverage_file_fails(project_dir, cache_dir, cli_runner):
    exit_code, _, stderr = cli_runner(project_dir, "save-baseline")

    assert exit_code == 1
    assert "no .coverage" in stderr


def test_diff_no_baseline_fails(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})

    exit_code, _, stderr = cli_runner(project_dir, "diff")

    assert exit_code == 1
    assert "no baseline 'default'" in stderr


def test_diff_unknown_name_lists_available(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline", "main")

    exit_code, _, stderr = cli_runner(project_dir, "diff", "missing")

    assert exit_code == 1
    assert "no baseline 'missing'" in stderr
    assert "'main'" in stderr


def test_diff_no_changes_exits_zero(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 0
    assert "No per-line changes." in stdout
    assert "Total: 100.00% → 100.00% (+0.00)" in stdout


def test_diff_regression_covered_to_uncovered(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    write_coverage_data(project_dir, {"foo.py": [1, 2, 3]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 1
    assert "↓ covered → uncovered" in stdout
    assert "foo.py:4-5" in stdout
    assert "Total: 100.00% → 60.00% (-40.00)" in stdout


def test_diff_new_uncovered_lines(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_THREE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3]})
    cli_runner(project_dir, "save-baseline")

    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 1
    assert "+ new uncovered" in stdout
    assert "foo.py:4-5" in stdout


def test_diff_improvement_uncovered_to_covered(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2]})
    cli_runner(project_dir, "save-baseline")

    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 0
    assert "↑ uncovered → covered" in stdout
    assert "foo.py:3-5" in stdout


def test_diff_unmeasured_in_baseline_does_not_fail(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_THREE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3]})
    cli_runner(project_dir, "save-baseline")

    write_source(project_dir, "bar.py", SOURCE_THREE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3], "bar.py": [1, 2, 3]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 0
    assert "! unmeasured in baseline (covered)" in stdout
    assert "bar.py: 3 lines" in stdout
    assert "! unmeasured in baseline (uncovered)" not in stdout


def test_diff_deleted_covered_line(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    write_source(project_dir, "foo.py", "x = 1\ny = 2\nprint(x)\nprint(y)\n")
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 1
    assert "↓ covered line removed" in stdout
    assert "sources/foo.py:3" in stdout


def test_diff_handles_source_line_shift(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    write_source(project_dir, "foo.py", "\n\n\n" + SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [4, 5, 6, 7, 8]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 0, stdout
    assert "↓ covered → uncovered" not in stdout
    assert "+ new uncovered" not in stdout
    assert "↓ covered line removed" not in stdout


def test_diff_merges_runs_within_max_gap(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [4]})
    cli_runner(project_dir, "save-baseline")

    write_coverage_data(project_dir, {"foo.py": [1, 3, 4]})
    _, stdout, _ = cli_runner(project_dir, "diff")

    assert "↑ uncovered → covered (2 lines, 1 runs)" in stdout
    assert "foo.py:1-3" in stdout


def test_diff_total_drop_via_unmeasured_file_fails(project_dir, cache_dir, cli_runner):
    write_source(project_dir, "foo.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5]})
    cli_runner(project_dir, "save-baseline")

    write_source(project_dir, "bar.py", SOURCE_FIVE_LINES)
    write_coverage_data(project_dir, {"foo.py": [1, 2, 3, 4, 5], "bar.py": [1]})
    exit_code, stdout, _ = cli_runner(project_dir, "diff")

    assert exit_code == 1
    assert "Total: 100.00% → 60.00% (-40.00)" in stdout
    assert "↓ covered → uncovered" not in stdout
    assert "+ new uncovered" not in stdout
    assert "! unmeasured in baseline (uncovered)" in stdout


def test_help_lists_subcommands(project_dir, cache_dir, cli_runner):
    exit_code, stdout, _ = cli_runner(project_dir, "--help")

    assert exit_code == 0
    assert "save-baseline" in stdout
    assert "diff" in stdout
