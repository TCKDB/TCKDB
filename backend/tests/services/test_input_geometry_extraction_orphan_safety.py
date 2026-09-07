"""DB-level tests: a malformed/truncated artifact must never orphan a
``Geometry`` row.

Reproduces the adversarial finding: a Gaussian log truncated mid-table
still yields a non-empty (but short) atom list from
``gaussian_output_parser.extract_first_geometry`` (see that function's
docstring -- a malformed block does not raise and does not fall through to
"Standard orientation", it just returns whatever it parsed before hitting
the truncation). Before the fix, ``resolve_geometry_payload`` minted a new
``Geometry``/``GeometryAtom`` row for that wrong-atom-count geometry
*before* any check could reject it, and a later composition/atom-count
mismatch left that row orphaned (no ``calculation_input_geometry`` link).
These tests assert the ``geometry`` table's row count is unchanged by a
rejected extraction, for both the ordinary case (finding 2) and a
``pseudo``-species owner where the composition check itself is skipped
entirely (finding 3) -- the atom-count guard must not depend on it.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.common import ArtifactKind, CalculationType, MoleculeKind
from app.db.models.geometry import Geometry
from app.services.input_geometry_extraction import (
    InputGeometryOutcomeKind,
    extract_and_link_input_geometry,
)
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    attach_output_geometry,
    make_calculation,
    make_geometry,
    make_software_release,
    make_species,
    make_species_entry,
)

# A Gaussian log with a well-formed "Input orientation:" header but only
# ONE data row before EOF -- truncated mid-table, exactly the shape
# extract_first_geometry documents returning without raising.
_TRUNCATED_GAUSSIAN_LOG = """\
 Entering Gaussian System, Link 0=g16
                          Input orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          8           0        0.000000    0.000000    0.118351
"""

_WATER_ELEMENTS = ["O", "H", "H"]
_WATER_COORDS = [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [10.0, 0.0, 0.0]]


def _geometry_count(session) -> int:
    return session.scalar(select(func.count()).select_from(Geometry)) or 0


def _water_opt_calc(session, *, species_kind: MoleculeKind = MoleculeKind.molecule):
    species = make_species(
        session, smiles="O", kind=species_kind, charge=0, multiplicity=1
    )
    entry = make_species_entry(session, species)
    release = make_software_release(session, name="Gaussian", version="16")
    calc = make_calculation(
        session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        software_release_id=release.id,
    )
    output_geom = make_geometry(session, natoms=3)
    attach_geometry_atoms(
        session, geometry=output_geom, symbols=_WATER_ELEMENTS, coords=_WATER_COORDS
    )
    attach_output_geometry(session, calculation=calc, geometry=output_geom)
    session.flush()
    return calc


class TestTruncatedLogNeverOrphansAGeometry:
    def test_truncated_log_is_not_determinable_and_mints_nothing(self, db_session):
        calc = _water_opt_calc(db_session)
        before = _geometry_count(db_session)

        outcome = extract_and_link_input_geometry(
            db_session,
            calc,
            candidates=[(ArtifactKind.output_log, _TRUNCATED_GAUSSIAN_LOG)],
        )

        assert outcome.kind is InputGeometryOutcomeKind.not_determinable
        assert _geometry_count(db_session) == before

    def test_pseudo_species_owner_truncated_log_mints_nothing(self, db_session):
        # The composition check itself is skipped entirely for a pseudo
        # owner (_reference_for returns None) -- the atom-count guard must
        # still catch the truncated log regardless.
        calc = _water_opt_calc(db_session, species_kind=MoleculeKind.pseudo)
        before = _geometry_count(db_session)

        outcome = extract_and_link_input_geometry(
            db_session,
            calc,
            candidates=[(ArtifactKind.output_log, _TRUNCATED_GAUSSIAN_LOG)],
        )

        assert outcome.kind is InputGeometryOutcomeKind.not_determinable
        assert _geometry_count(db_session) == before
