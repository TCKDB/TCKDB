"""DB-level tests: a malformed/adversarial artifact must never mint a
wrong ``Geometry`` row, nor orphan one.

Reproduces two adversarial findings:

* A Gaussian log truncated mid-table still yields a non-empty (but short)
  atom list from ``gaussian_output_parser.extract_first_geometry`` (see
  that function's docstring -- a malformed block does not raise and does
  not fall through to "Standard orientation", it just returns whatever it
  parsed before hitting the truncation). Before the fix,
  ``resolve_geometry_payload`` minted a new ``Geometry``/``GeometryAtom``
  row for that wrong-atom-count geometry *before* any check could reject
  it, and a later composition/atom-count mismatch left that row orphaned
  (no ``calculation_input_geometry`` link). ``TestTruncatedLogNeverOrphansAGeometry``
  asserts the ``geometry`` table's row count is unchanged by a rejected
  extraction, for both the ordinary case and a ``pseudo``-species owner
  where the composition check itself is skipped entirely -- the atom-count
  guard must not depend on it.
* A Gaussian deck naming a dummy/ghost atom (``X``, ``Bq``) or nonsense
  (``Xx``) in its element column has exactly the shape of a plain element
  symbol; before the element-token validation fix, it was accepted as-is
  and minted into a ``Geometry``/``GeometryAtom`` row carrying that fake
  "element". ``TestBadElementTokenNeverMintsAWrongGeometry`` reproduces
  the reviewer's own end-to-end case -- an "O X H" deck against a
  ``pseudo``-owned water calculation, where the composition check would
  not have caught it either -- and asserts nothing is minted or linked.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.common import ArtifactKind, CalculationType, MoleculeKind
from app.db.models.geometry import Geometry, GeometryAtom
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


# A Gaussian input deck naming a dummy atom ("X") in the element column of
# an otherwise well-formed 3-atom Cartesian block -- the reviewer's own
# reproduction. The pseudo species owner is deliberate: the composition
# check (assert_calculation_geometry_composition) silently declines to
# judge a pseudo owner, so it cannot be what stops this -- only the
# element-token validation in _normalize_element_token can.
_BAD_ELEMENT_GAUSSIAN_GJF = """\
%chk=water.chk
# opt b3lyp/6-31g(d)

water with a dummy atom

0 1
O 0.000000 0.000000 0.118351
X 0.000000 0.761187 -0.469725
H 0.000000 -0.761187 -0.469725

"""


class TestBadElementTokenNeverMintsAWrongGeometry:
    def test_dummy_atom_token_is_not_determinable_and_mints_nothing(self, db_session):
        calc = _water_opt_calc(db_session, species_kind=MoleculeKind.pseudo)
        before = _geometry_count(db_session)

        outcome = extract_and_link_input_geometry(
            db_session,
            calc,
            candidates=[(ArtifactKind.input, _BAD_ELEMENT_GAUSSIAN_GJF)],
        )

        assert outcome.kind is InputGeometryOutcomeKind.not_determinable
        assert _geometry_count(db_session) == before
        # No geometry_atom row anywhere carries the fake "X" element --
        # not just "no new geometry row": nothing was minted at all.
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(GeometryAtom)
                .where(GeometryAtom.element == "X")
            )
            or 0
        ) == 0
