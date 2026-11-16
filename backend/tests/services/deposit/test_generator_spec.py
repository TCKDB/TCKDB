"""The generator registry is operator-supplied: nothing runs without one, and it must reproduce from the deposit."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

from app.services.deposit import DepositError
from app.services.deposit.expected_outputs import write_expected_outputs
from app.services.deposit.generator_spec import assert_sources_within, load_deposit_generators, load_generators

BACKEND_ROOT = Path(__file__).resolve().parents[3]
_counter = itertools.count()


def _package(root: Path, body: str) -> str:
    """Write a uniquely named registry package under ``root``; return its ``module:attr`` spec."""
    name = f"gen_pkg_{next(_counter)}"
    (root / name).mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (root / name / "registry.py").write_text(body)
    return f"{name}.registry:R"


def _load_script(name: str):
    path = BACKEND_ROOT / "scripts" / "ops" / f"{name}.py"
    module_name = f"_ops_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


@pytest.fixture
def ops_script():
    loaded: list[str] = []

    def load(name: str):
        module = _load_script(name)
        loaded.append(module.__name__)
        return module

    yield load
    for name in loaded:
        sys.modules.pop(name, None)


def test_write_expected_outputs_refuses_an_empty_registry(db_session, tmp_path):
    with pytest.raises(ValueError, match="no generators registered"):
        write_expected_outputs(db_session, tmp_path, {})


def test_deposit_loader_returns_registry_and_the_package_tree_with_layout(tmp_path):
    spec = _package(tmp_path, "R = {'a': lambda session: {}}\n")
    package = spec.partition(".")[0]
    (tmp_path / package / "sub").mkdir()
    (tmp_path / package / "sub" / "extra.py").write_text("")
    (tmp_path / package / "data.txt").write_text("not python")
    (tmp_path / "unrelated.py").write_text("")
    registry, files = load_deposit_generators(spec, tmp_path)
    assert set(registry) == {"a"}
    assert [relative for _, relative in files] == [
        f"{package}/__init__.py",
        f"{package}/registry.py",
        f"{package}/sub/extra.py",
    ]
    assert all(path.is_relative_to(tmp_path.resolve()) for path, _ in files)


def test_runner_loader_imports_from_the_current_path(tmp_path, monkeypatch):
    spec = _package(tmp_path, "R = {'a': lambda session: {}}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert set(load_generators(spec)) == {"a"}


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (None, "generators_required"),
        ("", "generators_required"),
        ("no_colon", "generators_spec_invalid"),
        ("pkg.registry:", "generators_spec_invalid"),
        ("a-b.registry:R", "generators_spec_invalid"),
        ("pkg.1registry:R", "generators_spec_invalid"),
        ("pkg.registry:R!", "generators_spec_invalid"),
        ("pkg registry:R", "generators_spec_invalid"),
    ],
)
def test_loader_refuses_a_malformed_spec(spec, code, tmp_path):
    with pytest.raises(DepositError, match=code):
        load_deposit_generators(spec, tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "R = {}\n",
        "R = {'a': 1}\n",
        "R = {1: lambda session: {}}\n",
        "R = 3\n",
        "raise RuntimeError('boom')\n",
        "import module_that_does_not_exist_xyz\nR = {}\n",
        "OTHER = {'a': lambda session: {}}\n",
    ],
)
def test_loader_refuses_a_registry_that_is_not_a_nonempty_callable_mapping(body, tmp_path):
    spec = _package(tmp_path, body)
    with pytest.raises(DepositError, match="generators_spec_invalid"):
        load_deposit_generators(spec, tmp_path)


def test_loader_refuses_a_missing_module(tmp_path):
    with pytest.raises(DepositError, match="generators_spec_invalid"):
        load_deposit_generators("no_such_module_xyz.registry:R", tmp_path)


def test_deposit_loader_requires_a_root():
    with pytest.raises(DepositError, match="generators_root_required"):
        load_deposit_generators("gen_pkg.registry:R", None)


@pytest.mark.parametrize(
    "top_level", ["scripts", "app", "tests", "alembic", "tckdb_schemas", "sqlalchemy", "turtle", "json"]
)
def test_deposit_loader_refuses_a_top_level_name_that_already_names_a_module(top_level, tmp_path):
    (tmp_path / top_level).mkdir()
    (tmp_path / top_level / "__init__.py").write_text("")
    (tmp_path / top_level / "registry.py").write_text("R = {'a': lambda session: {}}\n")
    before = sys.modules.get(top_level)
    with pytest.raises(DepositError, match="generators_name_collision"):
        load_deposit_generators(f"{top_level}.registry:R", tmp_path)
    assert sys.modules.get(top_level) is before, "the process's own module must not be evicted"


def test_deposit_loader_accepts_a_reload_from_the_same_root(tmp_path):
    spec = _package(tmp_path, "R = {'a': lambda session: {}}\n")
    load_deposit_generators(spec, tmp_path)
    registry, _ = load_deposit_generators(spec, tmp_path)
    assert set(registry) == {"a"}


def test_deposit_loader_refuses_a_package_importable_from_elsewhere(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    root = tmp_path / "root"
    root.mkdir()
    spec = _package(elsewhere, "R = {'a': lambda session: {}}\n")
    monkeypatch.syspath_prepend(str(elsewhere))
    with pytest.raises(DepositError, match="generators_name_collision"):
        load_deposit_generators(spec, root)


def test_deposit_loader_refuses_a_file_that_resolves_outside_the_root(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("")
    root = tmp_path / "root"
    spec = _package(root, "R = {'a': lambda session: {}}\n")
    (root / spec.partition(".")[0] / "linked.py").symlink_to(outside)
    with pytest.raises(DepositError, match="generators_outside_root"):
        load_deposit_generators(spec, root)


def test_sources_imported_from_outside_the_package_are_refused(tmp_path):
    (tmp_path / "helpers.py").write_text("VALUE = 1\n")
    spec = _package(tmp_path, "import helpers\n\nR = {'a': lambda session: {'v': helpers.VALUE}}\n")
    _, files = load_deposit_generators(spec, tmp_path)
    try:
        with pytest.raises(DepositError, match="generators_outside_package"):
            assert_sources_within(files, tmp_path)
    finally:
        sys.modules.pop("helpers", None)


def test_sources_all_inside_the_package_pass(tmp_path):
    spec = _package(tmp_path, "R = {'a': lambda session: {}}\n")
    _, files = load_deposit_generators(spec, tmp_path)
    assert_sources_within(files, tmp_path)


@pytest.mark.parametrize(
    ("script", "argv"),
    [
        ("build_publication_deposit", ["build", "--release", "x", "--output", "out"]),
        ("generate_expected_outputs", ["--output-dir", "out"]),
    ],
)
def test_command_line_refuses_without_generators(script, argv, capsys, tmp_path, ops_script):
    module = ops_script(script)
    argv = [str(tmp_path / a) if a == "out" else a for a in argv]
    assert module.main(argv) == DepositError.exit_code
    assert "refused: generators_required" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_deposit_command_line_refuses_without_a_generator_root(capsys, tmp_path, ops_script):
    module = ops_script("build_publication_deposit")
    argv = ["build", "--release", "x", "--output", str(tmp_path / "out"), "--generators", "gen_pkg.registry:R"]
    assert module.main(argv) == DepositError.exit_code
    assert "refused: generators_root_required" in capsys.readouterr().err
