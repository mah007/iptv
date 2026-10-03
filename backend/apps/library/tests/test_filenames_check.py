"""backend/tests/data/check_filenames.py, the validator of the parser's ground truth, runs
with the backend tests (SPEC §15): the CSV must stay well-formed and self-consistent."""

import importlib.util
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[3] / "tests" / "data" / "check_filenames.py"


def test_filenames_csv_passes_its_checker(capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location("check_filenames", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["check_filenames.py"]) == 0, capsys.readouterr().err
    assert capsys.readouterr().out.rstrip().endswith("OK")
