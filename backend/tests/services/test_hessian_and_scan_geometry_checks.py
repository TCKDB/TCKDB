"""A Hessian's geometry and a scan point's geometry are held to the subject (#680).

``calculation_geometry_composition_mismatch`` and
``calculation_geometry_isotope_mismatch`` ran only on the calculation's
input/output geometry links. The two geometry-bearing children that hold their
own ``geometry_id`` -- ``calc_hessian`` and ``calc_scan_point`` -- were never
checked, and the Hessian one is the one that bites: ``hessian_reanalysis`` takes
its atomic masses from that geometry, so a deuterated or wrong-element Hessian
geometry under a protium species yielded wrong frequencies with no refusal.

Each refusal test names the geometry's own path in ``field`` and has a matching
acceptance, so none of them passes by refusing everything. The best-effort
extraction path is exercised separately: there a refusal must NOT fail the
upload.
"""

from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.scan import CalculationScanPointCreate

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationScanPoint,
)
from app.db.models.common import ArtifactKind, CalculationType, HessianSource
from app.db.models.geometry import GeometryAtom
from app.schemas.entities.calculation import CalculationScanResultCreate
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest
from app.services import hessian_extraction
from app.services.calc_isotopes import assert_isotopes
from app.services.calculation_resolution import persist_calculation_result
from app.services.calculation_scan_resolution import persist_calculation_scan
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.hessian_parsing import ParsedHessian
from app.services.hessian_reanalysis import ReanalysisStatus, reanalyse_calculation
from app.workflows.computed_species import persist_computed_species_upload
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_USER_ID = 50_680
_ISOTOPE = "calculation_geometry_isotope_mismatch"
_COMPOSITION = "calculation_geometry_composition_mismatch"

_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
#: Same atom count as H2, wrong element: composition H+N, not H2.
_HN = "2\nHN\nH 0.0 0.0 0.0\nN 0.0 0.0 1.0"
_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
#: Methane with the carbon swapped for nitrogen: wrong element, same atom count.
_NH4 = _CH4.replace("C  0.000", "N  0.000", 1)

# 2 atoms -> 3N = 6 -> packed lower triangle of length 21.
_TRIANGLE = [float(i) for i in range(21)]


@contextmanager
def _isolated_session(db_conn) -> Iterator[Session]:
    session = Session(bind=db_conn, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="hessian_scan_geometry_checks"))
        session.flush()
        yield session
    finally:
        session.close()


def _geom(xyz: str, isotopes: dict[int, int] | None = None) -> dict:
    out: dict = {"xyz_text": xyz}
    if isotopes is not None:
        out["isotopes"] = isotopes
    return out


def _bundle(
    *,
    smiles: str,
    multiplicity: int,
    conformer_xyz: str,
    conformer_isotopes: dict[int, int] | None = None,
    additional: list[dict] | None = None,
) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": "c0",
                "geometry": _geom(conformer_xyz, conformer_isotopes),
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
                "additional_calculations": additional or [],
            }
        ],
    }


def _hessian_calc(geometry: dict) -> dict:
    return {
        "key": "freq0",
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.01},
        "hessian": {
            "geometry": geometry,
            "lower_triangle_hartree_bohr2": _TRIANGLE,
            "source": "parsed_fchk",
        },
    }


def _h2(hessian_geometry: dict) -> dict:
    return _bundle(
        smiles="[H][H]",
        multiplicity=1,
        conformer_xyz=_H2,
        additional=[_hessian_calc(hessian_geometry)],
    )


def _d2(hessian_geometry: dict) -> dict:
    return _bundle(
        smiles="[2H][2H]",
        multiplicity=1,
        conformer_xyz=_H2,
        conformer_isotopes={1: 2, 2: 2},
        additional=[_hessian_calc(hessian_geometry)],
    )


def _scan_calc(point_geometries: list[dict]) -> dict:
    points = len(point_geometries)
    return {
        "key": "scan0",
        "type": "scan",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "scan_result": {
            "dimension": 1,
            "is_relaxed": True,
            "coordinates": [
                {
                    "coordinate_index": 1,
                    "coordinate_kind": "dihedral",
                    "atom1_index": 2,
                    "atom2_index": 1,
                    "atom3_index": 3,
                    "atom4_index": 4,
                    "step_count": points,
                    "step_size": 60.0,
                    "start_value": 0.0,
                    "end_value": 60.0 * (points - 1),
                    "value_unit": "degree",
                    "resolution_degrees": 60.0,
                    "symmetry_number": 3,
                }
            ],
            "points": [
                {
                    "point_index": i + 1,
                    "electronic_energy_hartree": -40.5 - 1e-4 * i,
                    "geometry": geometry,
                    "coordinate_values": [
                        {
                            "coordinate_index": 1,
                            "coordinate_value": 60.0 * i,
                            "value_unit": "degree",
                        }
                    ],
                }
                for i, geometry in enumerate(point_geometries)
            ],
        },
    }


def _ch4(point_geometries: list[dict]) -> dict:
    return _bundle(
        smiles="C",
        multiplicity=1,
        conformer_xyz=_CH4,
        additional=[_scan_calc(point_geometries)],
    )


def _upload(session: Session, payload: dict):
    return persist_computed_species_upload(
        session, ComputedSpeciesUploadRequest(**payload), created_by=_USER_ID
    )


def _refused(db_conn, payload: dict, code: str) -> CodedValueError:
    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload(session, payload)
    assert excinfo.value.code == code
    return excinfo.value


def _accepted(db_conn, payload: dict) -> None:
    with _isolated_session(db_conn) as session:
        _upload(session, payload)
        session.flush()


# ---------------------------------------------------------------------------
# Hessian from an upload payload
# ---------------------------------------------------------------------------


def test_a_hessian_geometry_with_the_wrong_element_is_refused(db_conn) -> None:
    error = _refused(db_conn, _h2(_geom(_HN)), _COMPOSITION)
    assert error.context["field"] == "hessian.geometry"


def test_a_deuterated_hessian_geometry_under_protium_is_refused(db_conn) -> None:
    """The issue's shape: deuterium masses reach reanalysis under a protium record."""

    error = _refused(db_conn, _h2(_geom(_H2, {1: 2, 2: 2})), _ISOTOPE)
    assert error.context["field"] == "hessian.geometry"
    assert error.context["geometry_substitutions"] == "2Hx2"
    assert error.context["subject_substitutions"] == "none (all standard isotopes)"


def test_a_protium_hessian_geometry_under_a_deuterated_species_is_refused(
    db_conn,
) -> None:
    error = _refused(db_conn, _d2(_geom(_H2)), _ISOTOPE)
    assert error.context["field"] == "hessian.geometry"


def test_a_wrong_isotope_count_on_the_hessian_geometry_is_refused(db_conn) -> None:
    """D2 species, HD Hessian geometry: same element, different count."""

    error = _refused(db_conn, _d2(_geom(_H2, {1: 2})), _ISOTOPE)
    assert error.context["geometry_substitutions"] == "2Hx1"
    assert error.context["subject_substitutions"] == "2Hx2"


def test_a_matching_hessian_geometry_is_accepted(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        _upload(session, _h2(_geom(_H2)))
        session.flush()
        assert session.scalar(select(func.count()).select_from(CalculationHessian)) == 1
    _accepted(db_conn, _d2(_geom(_H2, {1: 2, 2: 2})))


# ---------------------------------------------------------------------------
# Scan points
# ---------------------------------------------------------------------------


def test_a_scan_point_geometry_with_the_wrong_element_is_refused(db_conn) -> None:
    error = _refused(db_conn, _ch4([_geom(_CH4), _geom(_NH4)]), _COMPOSITION)
    assert error.context["field"] == "scan_result.points[2].geometry"


def test_a_scan_point_geometry_with_the_wrong_isotope_is_refused(db_conn) -> None:
    error = _refused(
        db_conn, _ch4([_geom(_CH4), _geom(_CH4, {2: 2})]), _ISOTOPE
    )
    assert error.context["field"] == "scan_result.points[2].geometry"
    assert error.context["geometry_substitutions"] == "2Hx1"


def test_matching_scan_point_geometries_are_accepted(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        _upload(session, _ch4([_geom(_CH4), _geom(_CH4)]))
        session.flush()
        assert session.scalar(select(func.count()).select_from(CalculationScanPoint)) == 2


def test_a_scan_point_without_a_geometry_is_unaffected(db_conn) -> None:
    """Points are allowed to carry no geometry; there is nothing to check."""

    _accepted(db_conn, _ch4([None, None]))


# ---------------------------------------------------------------------------
# The service enforces it, not the schema
# ---------------------------------------------------------------------------


def _protium_calc(session: Session, calc_type: str) -> Calculation:
    _upload(session, _h2(_geom(_H2)))
    session.flush()
    return session.scalars(
        select(Calculation).where(
            Calculation.created_by == _USER_ID, Calculation.type == calc_type
        )
    ).one()


@pytest.mark.parametrize(
    ("xyz", "isotopes", "code"),
    [(_H2, {1: 2, 2: 2}, _ISOTOPE), (_HN, None, _COMPOSITION)],
    ids=["wrong_isotope", "wrong_element"],
)
def test_scan_service_refuses_a_by_id_point_on_an_unvalidated_payload(
    db_conn, xyz, isotopes, code
) -> None:
    """``model_construct`` skips every validator, and a ``geometry_id`` point skips
    the inline-geometry branch; the service still refuses on either."""

    with _isolated_session(db_conn) as session:
        calc = _protium_calc(session, "opt")
        geometry = resolve_geometry_payload(
            session, GeometryPayload.model_construct(xyz_text=xyz, isotopes=isotopes)
        )
        point = CalculationScanPointCreate.model_construct(
            point_index=1,
            geometry=None,
            geometry_id=geometry.id,
            electronic_energy_hartree=None,
            relative_energy_kj_mol=None,
            note=None,
            coordinate_values=[],
        )
        payload = CalculationScanResultCreate.model_construct(
            dimension=1,
            is_relaxed=None,
            zero_energy_reference_hartree=None,
            note=None,
            coordinates=[],
            constraints=[],
            points=[point],
        )
        with pytest.raises(CodedValueError) as excinfo:
            persist_calculation_scan(session, calc.id, payload)
        assert excinfo.value.code == code
        assert excinfo.value.context["field"] == "scan_result.points[1].geometry"


# ---------------------------------------------------------------------------
# Best-effort extraction from an artifact
# ---------------------------------------------------------------------------


def _patch_parser(monkeypatch) -> None:
    parsed = ParsedHessian(
        natoms=2,
        lower_triangle_hartree_bohr2=list(_TRIANGLE),
        source=HessianSource.parsed_log,
    )
    monkeypatch.setattr(
        hessian_extraction, "parse_hessian_from_artifact", lambda *a, **k: parsed
    )


def _artifact() -> ArtifactIn:
    return ArtifactIn(
        kind=ArtifactKind.output_log,
        filename="freq.log",
        content_base64=base64.b64encode(b"not parsed; the parser is stubbed").decode(),
    )


def _freq_calc_with_one_input_geometry(session: Session) -> Calculation:
    """An H2 freq calculation linked to a single H2 input geometry."""

    _upload(
        session,
        _bundle(
            smiles="[H][H]",
            multiplicity=1,
            conformer_xyz=_H2,
            additional=[
                {
                    "key": "freq0",
                    "type": "freq",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_result": {"n_imag": 0, "zpe_hartree": 0.01},
                    "input_geometries": [_geom(_H2)],
                }
            ],
        ),
    )
    session.flush()
    return session.scalars(
        select(Calculation).where(
            Calculation.created_by == _USER_ID, Calculation.type == "freq"
        )
    ).one()


def _relink_input_geometry(session: Session, calc: Calculation, xyz: str, isotopes) -> None:
    """Point the calculation's input link at a different stored geometry.

    The link was checked when it was made, so the extraction hook's own check
    is only reachable by a link that has drifted from its subject; this
    simulates that without going around the hook under test.
    """

    geometry = resolve_geometry_payload(
        session, GeometryPayload.model_construct(xyz_text=xyz, isotopes=isotopes)
    )
    link = session.scalars(
        select(CalculationInputGeometry).where(
            CalculationInputGeometry.calculation_id == calc.id
        )
    ).one()
    link.geometry_id = geometry.id
    session.flush()


def _run_hook(session: Session, calc: Calculation) -> None:
    hessian_extraction.try_extract_hessian_from_artifact_upload(session, calc, _artifact())


def _hessian_rows(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalculationHessian))


def test_the_artifact_hook_still_stores_a_hessian_on_a_matching_geometry(
    db_conn, monkeypatch
) -> None:
    """The harness reaches the insert, so the refusals below are not vacuous."""

    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        _run_hook(session, calc)
        assert _hessian_rows(session) == 1


@pytest.mark.parametrize(
    ("xyz", "isotopes", "label"),
    [
        (_H2, {1: 2, 2: 2}, "isotope"),
        (_HN, None, "element"),
    ],
    ids=["wrong_isotope", "wrong_element"],
)
def test_a_refusal_in_the_artifact_hook_does_not_fail_the_upload(
    db_conn, monkeypatch, caplog, xyz, isotopes, label
) -> None:
    """Same path as any other failure inside the hook: savepoint rolled back, a
    warning logged, no ``calc_hessian`` row -- and the session stays usable."""

    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        _relink_input_geometry(session, calc, xyz, isotopes)
        calc_id = calc.id
        with caplog.at_level(logging.WARNING, logger="app.services.hessian_extraction"):
            _run_hook(session, calc)  # must not raise

        assert _hessian_rows(session) == 0
        assert any(
            "hessian insert skipped" in record.getMessage()
            and "CodedValueError" in record.getMessage()
            for record in caplog.records
        )
        # The caller's transaction is alive: the calculation is still there
        # and can be written to and read back.
        session.flush()
        assert session.get(Calculation, calc_id) is not None


# ---------------------------------------------------------------------------
# End to end: reanalysis cannot be fed wrong masses through the Hessian geometry
# ---------------------------------------------------------------------------


def test_reanalysis_is_never_handed_a_wrong_isotope_hessian_geometry(db_conn) -> None:
    """The deposit is refused, so no ``calc_hessian`` row exists for reanalysis.

    Before #680 this payload was accepted and ``reanalyse_calculation`` then took
    deuterium masses from the Hessian's geometry. A calculation filed under the
    same protium species with no Hessian reads as ``hessian_not_stored``: there
    is nothing to mis-mass.
    """

    payload = _h2(_geom(_H2, {1: 2, 2: 2}))
    _refused(db_conn, payload, _ISOTOPE)

    with _isolated_session(db_conn) as session:
        _upload(session, _h2(_geom(_H2)))
        session.flush()
        freq = session.scalars(
            select(Calculation).where(
                Calculation.created_by == _USER_ID, Calculation.type == "freq"
            )
        ).one()
        accepted = reanalyse_calculation(session, freq)
        # A matching Hessian is on file and is analysed (or refused for its own
        # numerical reasons) -- but never as a different isotopologue.
        assert accepted.status is not ReanalysisStatus.hessian_not_stored
        assert accepted.status is not ReanalysisStatus.isotope_identity_conflict


# ---------------------------------------------------------------------------
# Legacy species: isotope labels on species.smiles, NULL isotope_key (#680)
# ---------------------------------------------------------------------------


def _h2_geometry_row(session: Session, *, mass_numbers: tuple[int | None, int | None]) -> int:
    geometry = make_geometry(session, natoms=2)
    atoms = attach_geometry_atoms(
        session,
        geometry=geometry,
        symbols=["H", "H"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]],
    )
    for atom, mass_number in zip(atoms, mass_numbers, strict=True):
        atom.isotope_mass_number = mass_number
    session.flush()
    return geometry.id


def _legacy_calc(session: Session, smiles: str) -> Calculation:
    """A calculation under a species stored the way it was before #66.

    ``species.smiles`` carries the label and ``species_entry.isotope_key`` is
    NULL: the migration that added the column deliberately did not backfill it.
    """

    species = make_species(session, smiles=smiles, inchi_key=next_inchi_key("LGCY"))
    entry = make_species_entry(session, species)
    assert entry.isotope_key is None
    return make_calculation(
        session, type=CalculationType.freq, species_entry_id=entry.id
    )


def test_a_legacy_label_on_species_smiles_is_the_entrys_isotope_content(
    db_session,
) -> None:
    calc = _legacy_calc(db_session, "[2H][2H]")
    deuterium = _h2_geometry_row(db_session, mass_numbers=(2, 2))
    protium = _h2_geometry_row(db_session, mass_numbers=(None, None))

    assert_isotopes(db_session, calc=calc, geometry_id=deuterium, field="hessian.geometry")
    with pytest.raises(CodedValueError) as excinfo:
        assert_isotopes(db_session, calc=calc, geometry_id=protium, field="hessian.geometry")
    assert excinfo.value.code == _ISOTOPE
    assert excinfo.value.context["subject_substitutions"] == "2Hx2"


def test_an_unlabelled_legacy_species_is_still_all_standard(db_session) -> None:
    """The fallback reads a label if there is one; an ordinary SMILES gives the
    empty mapping, so ordinary entries are judged exactly as before."""

    calc = _legacy_calc(db_session, "[H][H]")
    protium = _h2_geometry_row(db_session, mass_numbers=(None, None))
    deuterium = _h2_geometry_row(db_session, mass_numbers=(2, 2))

    assert_isotopes(db_session, calc=calc, geometry_id=protium, field="hessian.geometry")
    with pytest.raises(CodedValueError):
        assert_isotopes(db_session, calc=calc, geometry_id=deuterium, field="hessian.geometry")


def test_reanalysis_reads_a_legacy_entry_the_way_the_upload_check_does(db_session) -> None:
    """Write and read agree (#680): upload accepts a deuterated Hessian under a
    legacy ``[2H][2H]`` NULL-key species, so reanalysis must not then call that
    entry protium and refuse the same Hessian as an isotope conflict."""

    from app.services.hessian_reanalysis import _declares_isotope_under_protium

    geometry_id = _h2_geometry_row(db_session, mass_numbers=(2, 2))
    deuterium_atoms = list(
        db_session.scalars(select(GeometryAtom).where(GeometryAtom.geometry_id == geometry_id))
    )
    legacy = _legacy_calc(db_session, "[2H][2H]")
    ordinary = _legacy_calc(db_session, "[H][H]")

    assert _declares_isotope_under_protium(legacy, deuterium_atoms) is False
    assert _declares_isotope_under_protium(ordinary, deuterium_atoms) is True


# ---------------------------------------------------------------------------
# IRC points: every direction, not only forward/reverse (#680)
# ---------------------------------------------------------------------------


def _irc_payload(direction: str | None, xyz: str, isotopes: dict[int, int] | None) -> CalculationWithResultsPayload:
    point: dict = {"point_index": 0, "geometry": _geom(xyz, isotopes), "is_ts": direction is None}
    if direction is not None:
        point["direction"] = direction
    return CalculationWithResultsPayload.model_validate(
        {
            "type": "irc",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "irc_result": {"points": [point]},
        }
    )


def _irc_calc(session: Session) -> Calculation:
    species = make_species(session, smiles="[H][H]", inchi_key=next_inchi_key("IRCH"))
    entry = make_species_entry(session, species)
    return make_calculation(session, type=CalculationType.irc, species_entry_id=entry.id)


_IRC_DIRECTIONS = [None, "forward", "reverse", "both"]


@pytest.mark.parametrize("direction", _IRC_DIRECTIONS, ids=str)
@pytest.mark.parametrize(
    ("xyz", "isotopes", "code"),
    [(_H2, {1: 2, 2: 2}, _ISOTOPE), (_HN, None, _COMPOSITION)],
    ids=["wrong_isotope", "wrong_element"],
)
def test_an_irc_point_geometry_is_refused_whatever_its_direction(
    db_session, direction, xyz, isotopes, code
) -> None:
    calc = _irc_calc(db_session)
    with pytest.raises(CodedValueError) as excinfo:
        persist_calculation_result(db_session, calc, _irc_payload(direction, xyz, isotopes))
    assert excinfo.value.code == code
    assert excinfo.value.context["field"] == "irc_result.points[0].geometry"


@pytest.mark.parametrize("direction", _IRC_DIRECTIONS, ids=str)
def test_a_matching_irc_point_geometry_is_accepted_whatever_its_direction(
    db_session, direction
) -> None:
    calc = _irc_calc(db_session)
    persist_calculation_result(db_session, calc, _irc_payload(direction, _H2, None))
    db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(CalculationIRCPoint)) == 1
