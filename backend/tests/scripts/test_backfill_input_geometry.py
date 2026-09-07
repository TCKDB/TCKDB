"""``backfill_input_geometry.py``: dry-run, apply, idempotence, and the guard.

Mirrors the dynamic-import / ``_SessionProxy`` pattern
``test_backfill_assumed_tau.py`` uses to run an ops script's ``main()``
against the pytest transaction, so nothing here writes to a real database.
Storage round-trips are stubbed via ``load_artifact_bytes``
(``test_extract_calculation_parameters_script.py``'s pattern) so the
suite does not require MinIO.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import select

from app.db.models.calculation import CalculationInputGeometry
from app.db.models.common import ArtifactKind, CalculationInputGeometrySource, CalculationType
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_atoms,
    attach_input_geometry,
    attach_output_geometry,
    make_calculation,
    make_geometry,
    make_software_release,
    make_species,
    make_species_entry,
)

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "backfill_input_geometry.py"

_GAUSSIAN_GJF_FIXTURE = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "input_geometry"
    / "gaussian_opt_input.gjf"
).read_bytes()
_GAUSSIAN_GJF_SHA = hashlib.sha256(_GAUSSIAN_GJF_FIXTURE).hexdigest()

_ZMATRIX_GJF_FIXTURE = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "input_geometry"
    / "gaussian_opt_input_zmatrix.gjf"
).read_bytes()
_ZMATRIX_GJF_SHA = hashlib.sha256(_ZMATRIX_GJF_FIXTURE).hexdigest()


@pytest.fixture(scope="module")
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_input_geometry", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    """The test's own session, wearing the shape ``main`` expects."""

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


def _run_main(backfill, monkeypatch, db_session, argv, *, db_name: str = "tckdb_test_ingeom"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr("sys.argv", ["backfill_input_geometry.py", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    return backfill.main()


@pytest.fixture
def stub_load_artifact_bytes(monkeypatch) -> dict[str, bytes]:
    """In-memory replacement for ``load_artifact_bytes``.

    Patches the symbol as imported into ``input_geometry_extraction`` (a
    bridge module that took a function reference at import time), the
    same device ``test_extract_calculation_parameters_script.py`` uses.
    """
    store: dict[str, bytes] = {}

    def _fake_load(sha256: str, *, client=None, bucket=None) -> bytes:
        try:
            return store[sha256]
        except KeyError as exc:
            from app.services.artifact_storage import ArtifactStorageUnavailable

            raise ArtifactStorageUnavailable(
                f"test stub: no content registered for sha={sha256}"
            ) from exc

    monkeypatch.setattr(
        "app.services.input_geometry_extraction.load_artifact_bytes", _fake_load
    )
    return store


_WATER_ELEMENTS = ["O", "H", "H"]
_WATER_OUTPUT_COORDS = [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
_WATER_REAL_INPUT_COORDS = [[1.0, 1.0, 1.0], [2.0, 1.0, 1.0], [1.0, 2.0, 1.0]]


def _water_calc(session, *, calc_type=CalculationType.opt):
    species = make_species(session, smiles="O", charge=0, multiplicity=1)
    entry = make_species_entry(session, species)
    release = make_software_release(session, name="Gaussian", version="09")
    calc = make_calculation(
        session,
        type=calc_type,
        species_entry_id=entry.id,
        software_release_id=release.id,
    )
    output_geom = make_geometry(session, natoms=3)
    attach_geometry_atoms(
        session, geometry=output_geom, symbols=_WATER_ELEMENTS, coords=_WATER_OUTPUT_COORDS
    )
    attach_output_geometry(session, calculation=calc, geometry=output_geom)
    return calc, output_geom


@pytest.fixture
def scope(db_session):
    """Five opt calcs (absent / degenerate / real-distinct / no-artifact /
    unparseable) plus one sp calc, out of the ``type='opt'`` scope."""
    rows = {}

    # A: absent input geometry, one input artifact -> extracted.
    calc_a, geom_a = _water_calc(db_session)
    attach_artifact(
        db_session,
        calculation=calc_a,
        kind=ArtifactKind.input,
        filename="input.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    rows["a"] = (calc_a, geom_a)

    # B: input == output (the ARC-deposit degenerate case) -> replaced.
    calc_b, geom_b = _water_calc(db_session)
    attach_input_geometry(db_session, calculation=calc_b, geometry=geom_b)
    attach_artifact(
        db_session,
        calculation=calc_b,
        kind=ArtifactKind.input,
        filename="input.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    rows["b"] = (calc_b, geom_b)

    # C: a real, distinct input already recorded -> untouched.
    calc_c, geom_c = _water_calc(db_session)
    real_input_geom = make_geometry(db_session, natoms=3)
    attach_geometry_atoms(
        db_session,
        geometry=real_input_geom,
        symbols=_WATER_ELEMENTS,
        coords=_WATER_REAL_INPUT_COORDS,
    )
    attach_input_geometry(db_session, calculation=calc_c, geometry=real_input_geom)
    attach_artifact(
        db_session,
        calculation=calc_c,
        kind=ArtifactKind.input,
        filename="input.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    rows["c"] = (calc_c, real_input_geom)

    # D: absent input, but no artifact on file at all -> no_artifact.
    calc_d, geom_d = _water_calc(db_session)
    rows["d"] = (calc_d, geom_d)

    # E: absent input, artifact is a Z-matrix deck -> not_determinable.
    calc_e, geom_e = _water_calc(db_session)
    attach_artifact(
        db_session,
        calculation=calc_e,
        kind=ArtifactKind.input,
        filename="input.gjf",
        sha256=_ZMATRIX_GJF_SHA,
        bytes_=len(_ZMATRIX_GJF_FIXTURE),
    )
    rows["e"] = (calc_e, geom_e)

    # F: an sp calc -- out of the type='opt' scope entirely.
    calc_f, geom_f = _water_calc(db_session, calc_type=CalculationType.sp)
    attach_input_geometry(db_session, calculation=calc_f, geometry=geom_f)
    attach_artifact(
        db_session,
        calculation=calc_f,
        kind=ArtifactKind.input,
        filename="input.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    rows["f"] = (calc_f, geom_f)

    db_session.flush()
    return rows


def _input_links(db_session, calc_id: int) -> list[CalculationInputGeometry]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == calc_id
            )
        ).all()
    )


def test_dry_run_changes_nothing(
    backfill, monkeypatch, db_session, scope, stub_load_artifact_bytes, capsys
):
    stub_load_artifact_bytes[_GAUSSIAN_GJF_SHA] = _GAUSSIAN_GJF_FIXTURE
    stub_load_artifact_bytes[_ZMATRIX_GJF_SHA] = _ZMATRIX_GJF_FIXTURE

    rc = _run_main(backfill, monkeypatch, db_session, [])
    assert rc == 0

    calc_a, geom_a = scope["a"]
    assert _input_links(db_session, calc_a.id) == []  # still absent

    calc_b, geom_b = scope["b"]
    links_b = _input_links(db_session, calc_b.id)
    assert len(links_b) == 1
    assert links_b[0].geometry_id == geom_b.id  # still the degenerate placeholder

    calc_c, real_input_geom = scope["c"]
    links_c = _input_links(db_session, calc_c.id)
    assert len(links_c) == 1
    assert links_c[0].geometry_id == real_input_geom.id

    out = capsys.readouterr().out
    assert "Would write an extracted input geometry on 2 of 5" in out
    assert "extracted: 2" in out
    assert "already_distinct: 1" in out
    assert "no_artifact: 1" in out
    assert "not_determinable: 1" in out
    assert "Dry run" in out
    # sp calc (f) never appears -- it is out of the type='opt' scope.
    assert "5 'opt' calculation(s) examined" in out


def test_apply_extracts_and_replaces(
    backfill, monkeypatch, db_session, scope, stub_load_artifact_bytes, capsys
):
    stub_load_artifact_bytes[_GAUSSIAN_GJF_SHA] = _GAUSSIAN_GJF_FIXTURE
    stub_load_artifact_bytes[_ZMATRIX_GJF_SHA] = _ZMATRIX_GJF_FIXTURE

    rc = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert rc == 0

    calc_a, geom_a = scope["a"]
    links_a = _input_links(db_session, calc_a.id)
    assert len(links_a) == 1
    assert links_a[0].source is CalculationInputGeometrySource.extracted_from_artifact
    assert links_a[0].geometry_id != geom_a.id  # a new, distinct geometry

    calc_b, geom_b = scope["b"]
    links_b = _input_links(db_session, calc_b.id)
    assert len(links_b) == 1  # replaced in place, not appended
    assert links_b[0].input_order == 1
    assert links_b[0].source is CalculationInputGeometrySource.extracted_from_artifact
    assert links_b[0].geometry_id != geom_b.id

    # A and B extract to the SAME Geometry row (identical .gjf content,
    # deduped by hash).
    assert links_a[0].geometry_id == links_b[0].geometry_id

    calc_c, real_input_geom = scope["c"]
    links_c = _input_links(db_session, calc_c.id)
    assert len(links_c) == 1
    assert links_c[0].geometry_id == real_input_geom.id  # untouched
    assert links_c[0].source is CalculationInputGeometrySource.deposited

    calc_d, _ = scope["d"]
    assert _input_links(db_session, calc_d.id) == []  # no artifact -> untouched

    calc_e, _ = scope["e"]
    assert _input_links(db_session, calc_e.id) == []  # Z-matrix -> rejected

    out = capsys.readouterr().out
    assert "Wrote an extracted input geometry on 2 of 5" in out


def test_a_second_apply_is_idempotent(
    backfill, monkeypatch, db_session, scope, stub_load_artifact_bytes, capsys
):
    stub_load_artifact_bytes[_GAUSSIAN_GJF_SHA] = _GAUSSIAN_GJF_FIXTURE
    stub_load_artifact_bytes[_ZMATRIX_GJF_SHA] = _ZMATRIX_GJF_FIXTURE

    first = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert first == 0
    capsys.readouterr()

    calc_a, _ = scope["a"]
    after_first = _input_links(db_session, calc_a.id)
    assert len(after_first) == 1
    geom_after_first = after_first[0].geometry_id

    second = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert second == 0
    out = capsys.readouterr().out
    # Now already_distinct (a and b both carry a real, distinct input);
    # no calculation is tallied as "extracted" a second time.
    assert "extracted:" not in out
    assert "Wrote an extracted input geometry on 0 of 5" in out

    after_second = _input_links(db_session, calc_a.id)
    assert len(after_second) == 1
    assert after_second[0].geometry_id == geom_after_first  # unchanged


def test_apply_refuses_a_non_test_database_without_the_override(
    backfill, monkeypatch, db_session, scope, stub_load_artifact_bytes, capsys
):
    rc = _run_main(
        backfill, monkeypatch, db_session, ["--apply"], db_name="tckdb_prod"
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--i-know-this-is-deployed" in err

    calc_a, _ = scope["a"]
    assert _input_links(db_session, calc_a.id) == []


def test_apply_and_dry_run_together_is_rejected(
    backfill, monkeypatch, db_session, scope, capsys
):
    rc = _run_main(backfill, monkeypatch, db_session, ["--apply", "--dry-run"])
    assert rc == 2


def test_verbose_names_public_refs_not_ids(
    backfill, monkeypatch, db_session, scope, stub_load_artifact_bytes, capsys
):
    stub_load_artifact_bytes[_GAUSSIAN_GJF_SHA] = _GAUSSIAN_GJF_FIXTURE
    stub_load_artifact_bytes[_ZMATRIX_GJF_SHA] = _ZMATRIX_GJF_FIXTURE

    rc = _run_main(backfill, monkeypatch, db_session, ["--verbose"])
    assert rc == 0
    out = capsys.readouterr().out
    calc_a, _ = scope["a"]
    assert calc_a.public_ref in out
    assert f"  {calc_a.public_ref}: extracted" in out


def test_one_row_raising_does_not_kill_the_batch(
    backfill, monkeypatch, db_session, stub_load_artifact_bytes, capsys
):
    """A row whose extraction raises (any exception, not just the specific
    element-canonicality AttributeError this module's own parser hardening
    is meant to prevent) must not abort the run -- it is recorded as
    ``not_determinable`` and the batch continues to the next calculation."""
    calc_bad, _ = _water_calc(db_session)
    attach_artifact(
        db_session,
        calculation=calc_bad,
        kind=ArtifactKind.input,
        filename="bad.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    calc_good, _ = _water_calc(db_session)
    attach_artifact(
        db_session,
        calculation=calc_good,
        kind=ArtifactKind.input,
        filename="good.gjf",
        sha256=_GAUSSIAN_GJF_SHA,
        bytes_=len(_GAUSSIAN_GJF_FIXTURE),
    )
    db_session.flush()
    stub_load_artifact_bytes[_GAUSSIAN_GJF_SHA] = _GAUSSIAN_GJF_FIXTURE

    import app.services.input_geometry_extraction as ige_module

    real_extract = ige_module.extract_and_link_input_geometry_from_stored_artifacts

    def _raise_for_bad_calc(session, calc):
        if calc.id == calc_bad.id:
            raise RuntimeError("simulated unexpected failure")
        return real_extract(session, calc)

    monkeypatch.setattr(
        "app.services.input_geometry_extraction.extract_and_link_input_geometry_from_stored_artifacts",
        _raise_for_bad_calc,
    )
    # The backfill script imported the name directly; patch its own binding too.
    monkeypatch.setattr(
        backfill, "extract_and_link_input_geometry_from_stored_artifacts", _raise_for_bad_calc
    )

    rc = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert rc == 0

    # The good calc after the bad one was still processed.
    good_links = _input_links(db_session, calc_good.id)
    assert len(good_links) == 1
    assert good_links[0].source is CalculationInputGeometrySource.extracted_from_artifact

    # The bad calc got no link (the raise was absorbed as not_determinable).
    assert _input_links(db_session, calc_bad.id) == []

    out = capsys.readouterr().out
    assert "not_determinable: 1" in out
    assert "extracted: 1" in out
