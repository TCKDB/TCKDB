"""The Arkane replay's deck, assembled from a statmech record this suite builds.

Arkane itself is not run here: the deck assembly is the part of the replay
that reads the database, and it is testable without ``rmg_env``. A
statmech record is built from the Gaussian frequency fixture (12 atoms,
30 printed frequencies, an N-N-C-C-C chain with a methyl rotor), and the
deck the script renders is checked for the modes, the symmetry number,
the optical-isomer count and the rotor definition that record implies. A
record with no frequencies yields a skip reason rather than a deck, the
batch mode reports every record, and the empty scope exits 2.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import pathlib
import sys

import pytest
from sqlalchemy import select

from app.db.models.calculation import (
    Calculation,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
)
from app.db.models.common import (
    CalculationType,
    CoordinateUnit,
    ScanCoordinateKind,
    StatmechCalculationRole,
    TorsionTreatmentKind,
)
from app.db.models.energy_correction import FrequencyScaleFactor
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_atoms,
    attach_input_geometry,
    attach_sp_result,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    attach_thermo_nasa,
    make_calculation,
    make_frequency_scale_factor,
    make_geometry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
)
from tests.services.test_normal_modes import GAUSSIAN_INPUT_ORIENTATION, _split

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "validation" / "arkane_statmech_roundtrip.py"
FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location("arkane_statmech_roundtrip", _SCRIPT)
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


def _run_main(replay, monkeypatch, db_session, argv):
    import app.api.deps as deps

    monkeypatch.setattr("sys.argv", ["arkane_statmech_roundtrip.py", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    return replay.main()


def _printed_frequencies() -> list[float]:
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
    printed = [float(x) for line in text.splitlines() if "Frequencies --" in line for x in line.split("--")[1].split()]
    assert len(printed) == 30
    return printed


#: The methyl rotor of the fixture: C5-C4 is the pivot bond, C5 (the first pivot, hence the top side) carries H10, H11, H12.
METHYL_PIVOTS = (5, 4)
METHYL_TOP = [5, 10, 11, 12]


def _scan_calculation(session, species_entry, *, n_points: int = 45, barrier_kj_mol: float = 6.0) -> Calculation:
    """A 45-point (8 degree) dihedral scan about the methyl rotor with a threefold
    potential -- the corpus resolution, and the coarsest rmgpy's Fourier fit accepts."""

    calc = Calculation(type=CalculationType.scan, species_entry_id=species_entry.id)
    session.add(calc)
    session.flush()
    session.add(
        CalculationScanCoordinate(
            calculation_id=calc.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.dihedral,
            atom1_index=10,
            atom2_index=METHYL_PIVOTS[0],
            atom3_index=METHYL_PIVOTS[1],
            atom4_index=3,
            step_count=n_points,
            step_size=360.0 / n_points,
            start_value=0.0,
            end_value=360.0 - 360.0 / n_points,
            value_unit=CoordinateUnit.degree,
        )
    )
    for i in range(n_points):
        angle = i * 360.0 / n_points
        session.add(
            CalculationScanPoint(
                calculation_id=calc.id,
                point_index=i + 1,
                relative_energy_kj_mol=0.5 * barrier_kj_mol * (1.0 - math.cos(math.radians(3.0 * angle))),
            )
        )
        session.add(
            CalculationScanPointCoordinateValue(
                calculation_id=calc.id,
                point_index=i + 1,
                coordinate_index=1,
                coordinate_value=angle,
                value_unit=CoordinateUnit.degree,
            )
        )
    session.flush()
    return calc


def _statmech_record(
    session,
    *,
    external_symmetry: int = 1,
    point_group: str = "C1",
    optical_isomers: int | None = None,
    with_frequencies: bool = True,
    with_rotor: bool = True,
    scale_factor: float = 0.97,
):
    """A statmech record for the Gaussian fixture molecule, sources and all."""

    elements, coords = _split(GAUSSIAN_INPUT_ORIENTATION)
    species = make_species(session, inchi_key=next_inchi_key("ARKN"))
    entry = make_species_entry(session, species)

    freq_calc = make_calculation(session, type=CalculationType.freq, species_entry_id=entry.id)
    geometry = make_geometry(session, natoms=len(elements))
    attach_geometry_atoms(session, geometry=geometry, symbols=elements, coords=coords.tolist())
    attach_input_geometry(session, calculation=freq_calc, geometry=geometry)
    if with_frequencies:
        attach_freq_result(session, calculation=freq_calc, frequencies_cm1=_printed_frequencies(), zpe_hartree=0.1)
    sp_calc = make_calculation(session, type=CalculationType.sp, species_entry_id=entry.id)
    attach_sp_result(session, calculation=sp_calc, electronic_energy_hartree=-250.0)

    # The scale factor's public ref is content-addressed, so two records in
    # one test must share the row rather than insert it twice.
    fsf = session.scalar(select(FrequencyScaleFactor).where(FrequencyScaleFactor.value == scale_factor))
    if fsf is None:
        fsf = make_frequency_scale_factor(session, value=scale_factor)
    statmech = make_statmech(
        session,
        species_entry=entry,
        external_symmetry=external_symmetry,
        point_group=point_group,
        is_linear=False,
        optical_isomers=optical_isomers,
        frequency_scale_factor_id=fsf.id,
    )
    attach_statmech_source_calculation(session, statmech=statmech, calculation=freq_calc, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(session, statmech=statmech, calculation=sp_calc, role=StatmechCalculationRole.sp)
    if with_rotor:
        scan = _scan_calculation(session, entry)
        attach_statmech_torsion(
            session,
            statmech=statmech,
            torsion_index=1,
            treatment_kind=TorsionTreatmentKind.hindered_rotor,
            symmetry_number=3,
            source_scan_calculation=scan,
            atoms=(10, METHYL_PIVOTS[0], METHYL_PIVOTS[1], 3),
        )
    thermo = make_thermo_scalar(session, species_entry=entry, s298_j_mol_k=300.0, h298_kj_mol=-20.0, statmech_id=statmech.id)
    attach_thermo_nasa(session, thermo=thermo)
    session.expire_all()
    return statmech


def _inject_rotor_terms(data: dict) -> None:
    """What ``compute_rotor_terms`` would fill in from rmgpy, without rmgpy."""

    for rotor in data["rotors"]:
        rotor["inertia_amu_a2"] = 2.5
        rotor["fourier_kj_mol"] = [[0.0, 0.0, -3.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0, 0.0]]


def test_the_gathered_record_carries_every_deck_input(db_session, replay):
    statmech = _statmech_record(db_session)

    data = replay.gather_species(db_session, statmech)

    assert data["statmech_ref"] == statmech.public_ref
    assert len(data["frequencies_cm1"]) == 30
    assert data["external_symmetry"] == 1
    assert data["multiplicity"] == 1
    assert data["freq_scale_factor"] == pytest.approx(0.97)
    assert data["is_linear"] is False
    # NULL optical_isomers, C1 point group: derived as chiral.
    assert data["optical_isomers_stored"] is None
    assert data["optical_isomers"] == 2
    assert "point_group=C1" in data["sources"]["optical_isomers"]
    assert data["e0_kj_mol"] == pytest.approx((-250.0 + 0.1) * replay.HARTREE_TO_KJMOL)
    assert data["stored_s298"] == 300.0
    assert data["stored_nasa"]["t_mid"] == 1000.0
    (rotor,) = data["rotors"]
    assert rotor["pivots"] == list(METHYL_PIVOTS)
    assert rotor["top"] == METHYL_TOP
    assert rotor["symmetry"] == 3
    assert rotor["n_scan_points"] == 45
    # 45 points at 8 degrees do not land on the 60 degree maximum exactly.
    assert 5.9 < rotor["barrier_kj_mol"] <= 6.0
    assert "top_description NULL" in data["sources"]["rotor_top"]


@pytest.mark.parametrize("external_symmetry", [1, 2], ids=["sigma_1", "sigma_2"])
def test_the_deck_carries_the_stored_symmetry_number_and_optical_isomers(db_session, replay, external_symmetry):
    statmech = _statmech_record(db_session, external_symmetry=external_symmetry)
    data = replay.gather_species(db_session, statmech)
    _inject_rotor_terms(data)

    deck = replay.build_arkane_input(data)

    assert f"symmetry={external_symmetry})" in deck
    assert "NonlinearRotor(" in deck and "LinearRotor(" not in deck
    assert "opticalIsomers = 2," in deck
    assert "spinMultiplicity = 1," in deck
    assert "useHinderedRotors = True" in deck
    assert "useAtomCorrections = False" in deck and "useBondCorrections = False" in deck
    assert "HinderedRotor(inertia=(2.5000000000, 'amu*angstrom^2'), symmetry=3," in deck
    assert "pivots=[5, 4] top=[5, 10, 11, 12]" in deck
    assert "thermo('SPC', 'NASA')" in deck


def test_the_deck_drops_the_lowest_frequency_for_the_rotor_and_scales_the_rest(db_session, replay):
    statmech = _statmech_record(db_session)
    data = replay.gather_species(db_session, statmech)
    _inject_rotor_terms(data)

    deck = replay.build_arkane_input(data)
    summary = replay.deck_summary(data)

    printed = sorted(_printed_frequencies())
    assert summary["n_frequencies_stored"] == 30
    assert summary["n_harmonic_oscillators"] == 29
    assert summary["dropped_frequencies_cm1"] == [round(printed[0], 4)]
    assert len(data["_scaled_freqs"]) == 29
    assert data["_scaled_freqs"] == [round(f * 0.97, 4) for f in printed[1:]]
    assert str(round(printed[1] * 0.97, 4)) in deck
    assert str(round(printed[0] * 0.97, 4)) not in deck


def test_a_stored_optical_isomer_count_beats_the_derived_one(db_session, replay):
    statmech = _statmech_record(db_session, optical_isomers=1)
    data = replay.gather_species(db_session, statmech)
    _inject_rotor_terms(data)

    deck = replay.build_arkane_input(data)

    assert data["optical_isomers"] == 1
    assert data["sources"]["optical_isomers"] == "statmech.optical_isomers"
    assert "opticalIsomers = 1," in deck


def test_a_record_without_rotors_needs_no_rotor_terms(db_session, replay):
    statmech = _statmech_record(db_session, with_rotor=False)
    data = replay.gather_species(db_session, statmech)

    deck = replay.build_arkane_input(data)

    assert data["rotors"] == []
    assert "useHinderedRotors = False" in deck
    assert "HinderedRotor(" not in deck
    assert len(data["_scaled_freqs"]) == 30


def test_a_record_with_no_frequencies_is_a_skip_reason_not_a_deck(db_session, replay):
    statmech = _statmech_record(db_session, with_frequencies=False)

    with pytest.raises(replay.Skip, match="no_frequencies"):
        replay.gather_species(db_session, statmech)

    record = replay.replay_record(
        db_session,
        statmech,
        rmg={"env": "rmg_env", "available": False, "reason": "not probed"},
        skip_arkane=True,
        keep_dir=None,
        s298_tolerance=0.5,
        cp_tolerance_percent=1.0,
    )
    assert record["status"] == "skipped"
    assert record["skip_reason"] == "no_frequencies"
    assert record["deck"] is None
    assert record["comparison"] is None


def test_batch_mode_reports_every_record_and_exits_two_without_arkane(db_session, replay, monkeypatch, capsys, tmp_path):
    good = _statmech_record(db_session)
    bare = _statmech_record(db_session, with_frequencies=False)
    json_path = tmp_path / "replay.json"

    code = _run_main(replay, monkeypatch, db_session, ["--all-statmech", "--skip-arkane", "--json-out", str(json_path), "--quiet"])
    out = capsys.readouterr().out

    assert code == replay.EXIT_NOTHING_COMPARED
    assert "NOTHING COMPARED" in out
    summary = json.loads(json_path.read_text())
    assert summary["generator"] == "arkane_statmech_replay"
    assert summary["rmg"]["available"] is False
    assert summary["rmg"]["rmgpy_version"] is None
    assert len(summary["approximations"]) == 2
    assert summary["scope"]["statmech_count"] == 2
    assert summary["scope"]["by_status"] == {"arkane_skipped": 1, "skipped": 1}
    by_ref = {r["statmech_ref"]: r for r in summary["records"]}
    assert by_ref[good.public_ref]["status"] == "arkane_skipped"
    assert by_ref[good.public_ref]["skip_reason"] == "--skip-arkane"
    assert by_ref[good.public_ref]["deck"]["rotors"][0]["symmetry"] == 3
    assert by_ref[bare.public_ref]["status"] == "skipped"
    assert by_ref[bare.public_ref]["skip_reason"] == "no_frequencies"
    refs = [r["statmech_ref"] for r in summary["records"]]
    assert refs == sorted(refs)


def test_an_empty_scope_exits_two(db_session, replay, monkeypatch, capsys):
    code = _run_main(replay, monkeypatch, db_session, ["--all-statmech", "--skip-arkane", "--quiet"])
    out = capsys.readouterr().out

    assert code == replay.EXIT_NOTHING_COMPARED
    assert "0 statmech record(s) in scope" in out


def test_one_record_can_be_named_by_statmech_or_species_entry_ref(db_session, replay):
    statmech = _statmech_record(db_session)
    _statmech_record(db_session)

    by_statmech = replay.statmech_scope(db_session, statmech_ref=statmech.public_ref)
    by_entry = replay.statmech_scope(db_session, species_entry_ref=statmech.species_entry.public_ref)

    assert [sm.id for sm in by_statmech] == [statmech.id]
    assert [sm.id for sm in by_entry] == [statmech.id]


def test_the_replay_writes_nothing(db_session, replay, monkeypatch, capsys):
    _statmech_record(db_session)
    db_session.flush()
    before = set(db_session.identity_map.values())

    _run_main(replay, monkeypatch, db_session, ["--all-statmech", "--skip-arkane", "--quiet"])
    capsys.readouterr()

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
    assert set(db_session.identity_map.values()) >= before
