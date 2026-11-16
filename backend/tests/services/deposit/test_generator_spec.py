"""The generator registry is operator-supplied: nothing runs without one."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.services.deposit import DepositError
from app.services.deposit.expected_outputs import write_expected_outputs
from app.services.deposit.generator_spec import load_generators
from tests.services.deposit.sample_generators.registry import GENERATORS

BACKEND_ROOT = Path(__file__).resolve().parents[3]
SPEC = "tests.services.deposit.sample_generators.registry:GENERATORS"


def _load_script(name: str):
    path = BACKEND_ROOT / "scripts" / "ops" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_ops_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_write_expected_outputs_refuses_an_empty_registry(db_session, tmp_path):
    with pytest.raises(ValueError, match="no generators registered"):
        write_expected_outputs(db_session, tmp_path, {})


def test_load_generators_resolves_registry_and_source_directory():
    registry, directory = load_generators(SPEC)
    assert registry is GENERATORS
    assert directory == Path(__file__).parent / "sample_generators"


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (None, "generators_required"),
        ("", "generators_required"),
        ("no_colon", "generators_spec_invalid"),
        ("no_such_module_xyz:GENERATORS", "generators_spec_invalid"),
        ("tests.services.deposit.sample_generators.registry:MISSING", "generators_spec_invalid"),
        ("tests.services.deposit.sample_generators.registry:row_counts", "generators_spec_invalid"),
    ],
)
def test_load_generators_refuses_with_a_code(spec, code):
    with pytest.raises(DepositError, match=code):
        load_generators(spec)


@pytest.mark.parametrize(
    ("script", "argv"),
    [
        ("build_publication_deposit", ["build", "--release", "x", "--output", "out"]),
        ("generate_expected_outputs", ["--output-dir", "out"]),
    ],
)
def test_command_line_refuses_without_generators(script, argv, capsys, tmp_path):
    module = _load_script(script)
    argv = [str(tmp_path / a) if a == "out" else a for a in argv]
    assert module.main(argv) == DepositError.exit_code
    err = capsys.readouterr().err
    assert "refused: generators_required" in err
    assert not (tmp_path / "out").exists()
