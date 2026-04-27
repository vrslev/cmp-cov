import os
import pathlib
import subprocess
import sys
import typing

import coverage as coverage_module
import pytest

import cmp_cov.cli


@pytest.fixture
def project_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    (project / ".coveragerc").write_text("[run]\nrelative_files = True\n")
    return project


@pytest.fixture
def cache_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    cache = tmp_path / "cache"
    monkeypatch.setattr(cmp_cov.cli, "CACHE_DIR", cache)
    return cache


def write_source(project_dir: pathlib.Path, relative_path: str, contents: str) -> None:
    target = project_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents)


def write_coverage_data(project_dir: pathlib.Path, executed_lines: dict[str, list[int]]) -> None:
    resolved_root = project_dir.resolve()
    previous_dir = os.getcwd()
    os.chdir(resolved_root)
    try:
        data = coverage_module.CoverageData(basename=str(resolved_root / ".coverage"))
        data.add_lines(executed_lines)
        data.write()
    finally:
        os.chdir(previous_dir)


@pytest.fixture
def cli_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> typing.Callable[..., tuple[int, str, str]]:
    def run(working_dir: pathlib.Path, *args: str) -> tuple[int, str, str]:
        monkeypatch.chdir(working_dir)
        monkeypatch.setattr(sys, "argv", ["cmp-cov", *args])
        try:
            exit_code: int = cmp_cov.cli.main()
        except SystemExit as exc:
            if exc.code is None:
                exit_code = 0
            elif isinstance(exc.code, int):
                exit_code = exc.code
            else:
                sys.stderr.write(str(exc.code) + "\n")
                exit_code = 1
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    return run
