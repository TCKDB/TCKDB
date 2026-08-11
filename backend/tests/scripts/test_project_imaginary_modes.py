"""What the corpus sweep counts, and what it refuses to count.

The read API answers one calculation; this script answers "across the
whole corpus, how many imaginary modes are rigid-body residue, how many
are torsions, how many are neither, and where does a determination
contradict a declaration". It is the tool ADR 0013 says a future
implementation should provide -- "if the projections are implemented in
five years, every record in the corpus can be re-decided against them" --
so the properties worth pinning are the same ones
``verify_artifact_integrity.py`` holds: a run that measured nothing must
not read as a run that found nothing, and a finding an operator has to
look at must not exit 0.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import pytest

from app.db.models.common import CalculationType, ImaginaryModeDisposition
from app.services.hessian_parsing import parse_hessian_from_artifact
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_atoms,
    attach_hessian,
    attach_input_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_SWEEP = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "project_imaginary_modes.py"
FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def sweep():
    spec = importlib.util.spec_from_file_location("project_imaginary_modes", _SWEEP)
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


def _run_main(sweep, monkeypatch, db_session, argv):
    import app.api.deps as deps

    monkeypatch.setattr("sys.argv", ["project_imaginary_modes.py", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    return sweep.main()


def _orca_transition_state(session, *, dispositions=None):
    """A real 6-atom transition state: matrix, frame and spectrum together."""
    text = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=True)
    assert parsed is not None and parsed.reference_coords_angstrom is not None

    species = make_species(session, smiles="C", inchi_key=next_inchi_key("SWEEP"))
    entry = make_species_entry(session, species)
    calc = make_calculation(session, type=CalculationType.freq, species_entry_id=entry.id)
    geometry = make_geometry(session, natoms=parsed.natoms)
    attach_geometry_atoms(
        session,
        geometry=geometry,
        symbols=[row[0] for row in parsed.reference_coords_angstrom],
        coords=[list(row[1:]) for row in parsed.reference_coords_angstrom],
    )
    attach_input_geometry(session, calculation=calc, geometry=geometry)
    attach_freq_result(
        session,
        calculation=calc,
        frequencies_cm1=[-503.235928, 1074.813628],
        imaginary_dispositions=dispositions,
    )
    attach_hessian(
        session,
        calculation=calc,
        geometry=geometry,
        natoms=parsed.natoms,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )
    return calc


def test_a_corpus_with_no_imaginary_mode_is_not_a_clean_sweep(db_session, sweep, monkeypatch, capsys):
    """Nothing in scope is a scope error, not a result.

    A summary printed over zero records is the exact way a gate goes
    green having looked at nothing, so the empty scope exits non-zero and
    says the word "NOT PROJECTED". ``--allow-empty`` is the explicit
    opt-out for a deployment that genuinely has no imaginary modes.
    """
    code = _run_main(sweep, monkeypatch, db_session, ["--all"])
    assert code == sweep.EXIT_EMPTY_SCOPE
    assert "NOT PROJECTED" in capsys.readouterr().out

    code = _run_main(sweep, monkeypatch, db_session, ["--all", "--allow-empty"])
    assert code == sweep.EXIT_OK


def test_a_record_with_no_conflict_sweeps_clean(db_session, sweep, monkeypatch, capsys):
    _orca_transition_state(db_session)
    code = _run_main(sweep, monkeypatch, db_session, ["--all", "--verbose"])
    out = capsys.readouterr().out

    assert code == sweep.EXIT_OK
    assert "1 calculation(s) with at least one imaginary mode" in out
    assert "internal_vibration" in out
    assert "no determination contradicts a declaration" in out


def test_a_determination_contradicting_a_declaration_does_not_exit_zero(db_session, sweep, monkeypatch, capsys):
    """The finding an operator has to look at.

    A depositor declaring ``torsion`` on a reaction coordinate is what
    ADR 0013 said TCKDB could not check. The sweep names the record, both
    readings, and exits 1 -- and changes nothing, because under ADR 0008 a
    projection is an expectation and a conflict is a curation question
    rather than a validation failure.
    """
    calc = _orca_transition_state(db_session, dispositions=[ImaginaryModeDisposition.torsion, None])
    code = _run_main(sweep, monkeypatch, db_session, ["--all"])
    out = capsys.readouterr().out

    assert code == sweep.EXIT_CONFLICT
    assert "1 determination(s) contradict a declaration" in out
    assert calc.public_ref in out
    assert "declared torsion" in out
    assert "RESULT: CONFLICT" in out


def test_a_record_with_no_hessian_is_counted_as_not_determinable(db_session, sweep, monkeypatch, capsys):
    """The half of the corpus that cannot be checked is reported as such.

    Counting these anywhere near "no residue found" would restate, in a
    summary table, the defect the artifact-integrity sweep was rebuilt to
    remove.
    """
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("SWNH"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=[-1200.0, 800.0])

    code = _run_main(sweep, monkeypatch, db_session, ["--all"])
    out = capsys.readouterr().out

    assert code == sweep.EXIT_OK
    assert "hessian_not_stored" in out
    assert "carry no Hessian" in out
    assert "not\n  determinable here" in out or "not determinable here" in out


def test_the_sweep_writes_nothing(db_session, sweep, monkeypatch):
    """It is a read of a computation, and there is nothing for it to store.

    ADR 0013's objection is that a projection is an inference and TCKDB
    records observations. Running the projections over the whole corpus
    must therefore leave the corpus exactly as it was.
    """
    _orca_transition_state(db_session)
    db_session.flush()
    before = set(db_session.identity_map.values())
    _run_main(sweep, monkeypatch, db_session, ["--all"])
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
    assert set(db_session.identity_map.values()) >= before
