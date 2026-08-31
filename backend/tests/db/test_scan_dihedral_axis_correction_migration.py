"""Disposable-database contract for the legacy scan-dihedral-axis correction.

``a4f7c2e9d651`` converts ``calc_scan_point_coordinate_value.coordinate_value``
from ADR 0019's superseded "sweep relative to the first point" convention to
ADR 0020's "the coordinate itself": ``coordinate_value := start_value +
coordinate_value``, applied per series only when every one of its points'
own geometry proves the conversion. ``downgrade()`` performs no writes at
all -- see "downgrade() is one-way, by design" in the revision's own module
docstring, and the adversarial review that forced that design: an earlier
draft's margin-based reversal guard corrupted the very series it claimed to
protect (measured on the real corpus shape,
``calc_z63ecgljjdt2dvkqkjadmxkxou``) and was, in general, structurally unable
to tell a converted row apart from a row deposited correctly at an ordinary
anchor. Everything here is a way that proof, or that refusal, could go wrong
while looking perfect against an empty database, which is why none of it is
checked through a service layer -- the same reasoning
``test_conformer_anchor_backfill_migration.py`` gives for ``b8e3f1a7c250``.

Geometry fixtures are built, not guessed: every scan point's four atoms are
placed with the standard NeRF (natural extension reference frame)
construction so that a *chosen* dihedral is exactly reproduced by
``_dihedral_deg`` in the revision under test -- verified once, directly,
in ``test_nerf_fixture_reproduces_its_own_target_dihedral`` below, so a
failure in every other test here cannot be blamed on a bad fixture instead
of the migration.

============================  =====================================
seed series                   what it exists to pin
============================  =====================================
``legacy``                    the population this revision repairs
``legacy_decimal``            conversion is close, not claimed bit-exact
``already_conforms_anchor0``  ADR 0020's ``conforms`` bucket, start≈0
``already_conforms_anchor360``ADR 0020's ``conforms`` bucket, start≈360,
                               seeded as an actual relative sweep (the real
                               shape of ``calc_z63ecgljjdt2dvkqkjadmxkxou``)
``correct_ordinary_anchor``   a CORRECT post-ADR-0020 deposit at a
                               non-trivial anchor -- what the old,
                               since-removed downgrade guard would have
                               corrupted
``geometry_disagrees``        proof fails for every point -> untouched
``one_bad_point``             proof fails for one point (bad distance) ->
                               whole series untouched
``near_collinear_point``      one point's quartet is near-collinear ->
                               ``provable=False`` -> whole series untouched
``degenerate_bond_point``     one point has a near-zero-length wing bond ->
                               ``provable=False`` -> whole series untouched
``missing_atom_point``        one point's geometry is missing atom_index=4
                               -> ``provable=False`` -> whole series untouched
``missing_geometry_point``    one point has ``geometry_id IS NULL`` ->
                               whole series untouched (caught before proof)
``point_unit_mismatch``       one point declares a non-degree ``value_unit``
                               -> whole series untouched (caught before proof)
``improper_kind``             ``coordinate_kind = 'improper'``, otherwise
                               shaped exactly like ``legacy`` -> untouched
``non_dihedral``               ``coordinate_kind = 'bond'`` gate
``coordinate_unit_mismatch``   coordinate-level declared-unit gate
``no_start_value``             ``start_value IS NOT NULL`` gate
============================  =====================================

Only ``legacy`` and ``legacy_decimal`` may change under ``upgrade()``. Every
other row is one clause, or one not-provable point, away from being
eligible -- a predicate whose narrowness nothing tests is a predicate that
has been written down, not established.
"""

from __future__ import annotations

import math
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("a4f7c2e9d651")

_REVISION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "a4f7c2e9d651_convert_legacy_relative_scan_dihedrals.py"
)


# ---------------------------------------------------------------------------
# NeRF placement -- pure geometry, independent of the revision under test.
# ---------------------------------------------------------------------------


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a):
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _place_fourth_atom(a, b, c, bond_length, angle_deg, dihedral_deg_target):
    """Place ``d`` so that ``angle(b, c, d) == angle_deg`` and
    ``dihedral(a, b, c, d) == dihedral_deg_target``, exactly, under the same
    sign convention the revision's own ``_dihedral_deg`` uses.

    Standard NeRF construction (Parsons et al. 2005). Cross-checked against
    the revision's own dihedral/angle formulas in
    ``test_nerf_fixture_reproduces_its_own_target_dihedral``. A small
    ``angle_deg`` is exactly how the ``near_collinear_point`` fixture below
    drives ``sin(theta_234)`` under the revision's 0.05 threshold -- this
    function's ``angle_deg`` parameter *is* that angle by construction.
    """
    theta = math.radians(angle_deg)
    phi = math.radians(dihedral_deg_target)
    d2 = (
        -bond_length * math.cos(theta),
        bond_length * math.sin(theta) * math.cos(phi),
        bond_length * math.sin(theta) * math.sin(phi),
    )
    bc_hat = _unit(_sub(c, b))
    ab = _sub(b, a)
    n_hat = _unit(_cross(ab, bc_hat))
    m_hat = _cross(n_hat, bc_hat)
    return (
        c[0] + bc_hat[0] * d2[0] + m_hat[0] * d2[1] + n_hat[0] * d2[2],
        c[1] + bc_hat[1] * d2[0] + m_hat[1] * d2[1] + n_hat[1] * d2[2],
        c[2] + bc_hat[2] * d2[0] + m_hat[2] * d2[1] + n_hat[2] * d2[2],
    )


_ATOM_A = (0.0, 0.0, 0.0)
_ATOM_B = (1.5, 0.0, 0.0)
_ATOM_C = (2.2, 1.3, 0.0)
_BOND_LENGTH = 1.09
_BOND_ANGLE_DEG = 109.5


def test_nerf_fixture_reproduces_its_own_target_dihedral() -> None:
    """The fixture builder actually builds what it claims to.

    Imports the revision's own dihedral formula, so a fixture that satisfies
    this test is provably read by the migration as the dihedral it was
    built for -- not merely "probably right by construction".
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_scan_axis_revision", _REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for target in (0.0, 8.0, 45.0, 90.0, 123.456, -60.0, 179.9, 359.9994):
        d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, target)
        got = module._dihedral_deg(_ATOM_A, _ATOM_B, _ATOM_C, d)
        expected = module._wrap_deg(target)
        assert abs(module._wrap_deg(got - expected)) < 1e-9, (target, got)


# ---------------------------------------------------------------------------
# Harness -- mirrors test_conformer_anchor_backfill_migration.py exactly.
# ---------------------------------------------------------------------------


class _MigrationHarness:
    """Create a throwaway database and drive alembic against it."""

    def __init__(self, name_prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(name_prefix)
        self.env = _db_env(self.db_name)
        self._database_url = _database_url
        self._admin = create_engine(
            _database_url("postgres"),
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        self._admin_conn = self._admin.connect()
        self._admin_conn.execute(text(f'CREATE DATABASE "{self.db_name}"'))
        self.engine = None
        self.root = Path(__file__).resolve().parents[2]

    def run(self, direction: str, revision: str, *, check: bool = True):
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=self.root,
            env=self.env,
            check=check,
            capture_output=True,
            text=True,
        )
        self.engine = create_engine(self._database_url(self.db_name), pool_pre_ping=True)
        return completed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        try:
            self._admin_conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": self.db_name},
            )
            self._admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{self.db_name}"'))
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _MigrationHarness("scan_axis")
    yield created
    created.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _new_species_entry(conn, smiles: str, inchi_key: str) -> int:
    species_id = conn.scalar(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), :smiles, "
            "        :inchi_key, 0, 1, "
            "        CAST('unspecified' AS stereo_kind)) "
            "RETURNING id"
        ),
        {"smiles": smiles, "inchi_key": inchi_key},
    )
    return conn.scalar(
        text("INSERT INTO species_entry (species_id) VALUES (:sid) RETURNING id"),
        {"sid": species_id},
    )


def _new_geometry(conn, atoms: list[tuple[str, float, float, float]]) -> int:
    geometry_id = conn.scalar(
        text(
            "INSERT INTO geometry (natoms, geom_hash) VALUES (:n, :h) RETURNING id"
        ),
        {"n": len(atoms), "h": uuid.uuid4().hex + uuid.uuid4().hex},
    )
    for index, (element, x, y, z) in enumerate(atoms, start=1):
        conn.execute(
            text(
                "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) "
                "VALUES (:g, :i, :el, :x, :y, :z)"
            ),
            {"g": geometry_id, "i": index, "el": element, "x": x, "y": y, "z": z},
        )
    return geometry_id


def _new_scan_calculation(conn, species_entry_id: int, *, dimension: int = 1) -> int:
    calculation_id = conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id) "
            "VALUES (CAST('scan' AS calc_type), :sid) RETURNING id"
        ),
        {"sid": species_entry_id},
    )
    conn.execute(
        text(
            "INSERT INTO calc_scan_result (calculation_id, dimension) "
            "VALUES (:cid, :dim)"
        ),
        {"cid": calculation_id, "dim": dimension},
    )
    return calculation_id


def _new_quartet_coordinate(
    conn,
    calculation_id: int,
    coordinate_index: int,
    *,
    start_value,
    value_unit: str | None = "degree",
    kind: str = "dihedral",
    atom_indices: tuple[int, int, int, int] = (1, 2, 3, 4),
) -> None:
    """A 4-atom scan coordinate: ``dihedral`` by default, or ``improper`` for
    the fixture that pins the ``coordinate_kind = 'dihedral'`` eligibility
    clause -- both kinds share the same arity and column shape."""
    conn.execute(
        text(
            "INSERT INTO calc_scan_coordinate "
            "(calculation_id, coordinate_index, coordinate_kind, "
            " atom1_index, atom2_index, atom3_index, atom4_index, "
            " start_value, value_unit) "
            "VALUES (:cid, :cidx, CAST(:kind AS scan_coordinate_kind), "
            "        :a1, :a2, :a3, :a4, :start, "
            "        CAST(:unit AS coordinate_unit))"
        ),
        {
            "cid": calculation_id,
            "cidx": coordinate_index,
            "kind": kind,
            "a1": atom_indices[0],
            "a2": atom_indices[1],
            "a3": atom_indices[2],
            "a4": atom_indices[3],
            "start": start_value,
            "unit": value_unit,
        },
    )


def _new_bond_coordinate(conn, calculation_id: int, coordinate_index: int) -> None:
    """A ``bond`` (2-atom) coordinate: exists purely to pin the
    ``coordinate_kind = 'dihedral'`` gate."""
    conn.execute(
        text(
            "INSERT INTO calc_scan_coordinate "
            "(calculation_id, coordinate_index, coordinate_kind, "
            " atom1_index, atom2_index, start_value, value_unit) "
            "VALUES (:cid, :cidx, CAST('bond' AS scan_coordinate_kind), "
            "        1, 2, 1.5, CAST('angstrom' AS coordinate_unit))"
        ),
        {"cid": calculation_id, "cidx": coordinate_index},
    )


def _new_scan_point(
    conn, calculation_id: int, point_index: int, geometry_id: int | None
) -> None:
    conn.execute(
        text(
            "INSERT INTO calc_scan_point (calculation_id, point_index, geometry_id) "
            "VALUES (:cid, :pidx, :gid)"
        ),
        {"cid": calculation_id, "pidx": point_index, "gid": geometry_id},
    )


def _new_coordinate_value(
    conn,
    calculation_id: int,
    point_index: int,
    coordinate_index: int,
    coordinate_value,
    *,
    value_unit: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO calc_scan_point_coordinate_value "
            "(calculation_id, point_index, coordinate_index, coordinate_value, "
            " value_unit) "
            "VALUES (:cid, :pidx, :cidx, :value, CAST(:unit AS coordinate_unit))"
        ),
        {
            "cid": calculation_id,
            "pidx": point_index,
            "cidx": coordinate_index,
            "value": coordinate_value,
            "unit": value_unit,
        },
    )


def _seed_dihedral_series(
    conn,
    *,
    species_entry_id: int,
    start_value,
    stored_values: list[float],
    true_dihedrals: list[float],
    bad_point_index: int | None = None,
    unit: str | None = "degree",
    kind: str = "dihedral",
) -> int:
    """A one-dimensional dihedral (or ``improper``, via ``kind``) scan:
    ``stored_values[i]`` is what is written to ``coordinate_value``,
    ``true_dihedrals[i]`` is the dihedral the point's *geometry* is built to
    actually be. ``bad_point_index`` (1-based) corrupts one point's geometry
    by displacing its fourth atom -- the "one mis-attached geometry" shape,
    as opposed to a series built to disagree throughout.
    """
    assert len(stored_values) == len(true_dihedrals)
    calculation_id = _new_scan_calculation(conn, species_entry_id)
    _new_quartet_coordinate(
        conn, calculation_id, 1, start_value=start_value, value_unit=unit, kind=kind
    )
    for i, (stored, true_dihedral) in enumerate(zip(stored_values, true_dihedrals), start=1):
        d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
        if bad_point_index == i:
            d = (d[0] + 0.7, d[1] - 0.4, d[2] + 0.3)
        geometry_id = _new_geometry(
            conn,
            [
                ("C", *_ATOM_A),
                ("C", *_ATOM_B),
                ("C", *_ATOM_C),
                ("H", *d),
            ],
        )
        _new_scan_point(conn, calculation_id, i, geometry_id)
        _new_coordinate_value(conn, calculation_id, i, 1, stored)
    return calculation_id


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------

#: Legacy relative sweep: 45 degrees is the anchor, and each stored value is
#: a relative offset from it. Absolute (true) dihedrals are start + offset,
#: all comfortably inside (-180, 180] so no wrap ambiguity is in play.
_LEGACY_START = 45.0
_LEGACY_OFFSETS = [0.0, 8.0, 16.0, 24.0, 32.0, 40.0]
_LEGACY_TRUE = [_LEGACY_START + o for o in _LEGACY_OFFSETS]

#: Same shape, at 6-decimal-place deposit precision -- the real corpus's
#: precision (ADR 0020) -- specifically so the conversion test against it
#: cannot claim bit-exactness. See test_upgrade_conversion_is_correct_within
#: _a_stated_tolerance_not_claimed_bit_exact.
_LEGACY_DECIMAL_START = 45.123456
_LEGACY_DECIMAL_OFFSETS = [0.0, 8.123456, 16.246912, 24.370368, 32.493824, 40.61728]
_LEGACY_DECIMAL_TRUE = [_LEGACY_DECIMAL_START + o for o in _LEGACY_DECIMAL_OFFSETS]

#: The real corpus shape for calc_z63ecgljjdt2dvkqkjadmxkxou: a relative
#: sweep stored on an anchor within a few ten-thousandths of a degree of a
#: full turn, NOT a constant series (an earlier draft of this fixture used a
#: constant value here and was flagged in review as misleading). Because
#: start_value is within noise of 360, the physically deposited geometry
#: (built to true_dihedrals below) ends up numerically equal to the stored
#: relative value itself -- which is exactly why the "conforms trivially"
#: bucket exists.
_ANCHOR360_START = 359.9994
_ANCHOR360_STORED = [0.0, 8.0001, 15.9998, 24.0003, 31.9997]
_ANCHOR360_TRUE = list(_ANCHOR360_STORED)


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    entry = _new_species_entry(conn, "CCCC", "AAAAAAAAAAAAAA-UHFFFAOYSA-N")

    # --- The population this revision repairs ---------------------------
    ids["legacy"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=_LEGACY_TRUE,
    )
    ids["legacy_decimal"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_DECIMAL_START,
        stored_values=_LEGACY_DECIMAL_OFFSETS,
        true_dihedrals=_LEGACY_DECIMAL_TRUE,
    )

    # --- ADR 0020's "conforms" bucket: anchor within a turn of 0 --------
    # start_value = 0.0 means relative and absolute coincide exactly; the
    # stored value already IS the absolute dihedral.
    absolute_values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    ids["already_conforms_anchor0"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=0.0,
        stored_values=absolute_values,
        true_dihedrals=absolute_values,
    )

    # --- ADR 0020's "conforms" bucket: anchor within a turn of 360 ------
    # Real shape (relative sweep), not a constant series -- see
    # _ANCHOR360_* above.
    ids["already_conforms_anchor360"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_ANCHOR360_START,
        stored_values=_ANCHOR360_STORED,
        true_dihedrals=_ANCHOR360_TRUE,
    )

    # --- A CORRECT post-ADR-0020 deposit at an ordinary anchor -----------
    # What the since-removed margin-based downgrade guard would have
    # corrupted: stored values already ARE the absolute dihedral, at a
    # start_value nowhere near a multiple of 360.
    ids["correct_ordinary_anchor"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_TRUE,
        true_dihedrals=_LEGACY_TRUE,
    )

    # --- Geometry disagrees throughout: proof never holds ----------------
    ids["geometry_disagrees"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
    )

    # --- One bad point (bad distance) in an otherwise-good series --------
    ids["one_bad_point"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=list(_LEGACY_TRUE),
        bad_point_index=3,
    )

    # --- One point with a near-collinear quartet --------------------------
    # angle_deg=1.0 makes sin(theta_234) = sin(1 degree) ~= 0.0175, below
    # the revision's 0.05 not-checkable threshold, so this point's proof is
    # provable=False -- exercising a genuinely different guard than
    # one_bad_point's TOLERANCE-branch failure.
    ids["near_collinear_point"] = _new_scan_calculation(conn, entry)
    _new_quartet_coordinate(
        conn, ids["near_collinear_point"], 1, start_value=_LEGACY_START
    )
    for i, (stored, true_dihedral) in enumerate(zip(_LEGACY_OFFSETS, _LEGACY_TRUE), start=1):
        if i == 3:
            d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, 1.0, true_dihedral)
        else:
            d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
        gid = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B), ("C", *_ATOM_C), ("H", *d)])
        _new_scan_point(conn, ids["near_collinear_point"], i, gid)
        _new_coordinate_value(conn, ids["near_collinear_point"], i, 1, stored)

    # --- One point with a degenerate (near-zero-length) wing bond ---------
    # Atom D placed on top of atom C for point 3: r_cd ~ 0, provable=False.
    ids["degenerate_bond_point"] = _new_scan_calculation(conn, entry)
    _new_quartet_coordinate(
        conn, ids["degenerate_bond_point"], 1, start_value=_LEGACY_START
    )
    for i, (stored, true_dihedral) in enumerate(zip(_LEGACY_OFFSETS, _LEGACY_TRUE), start=1):
        if i == 3:
            d = (_ATOM_C[0] + 1e-9, _ATOM_C[1], _ATOM_C[2])
        else:
            d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
        gid = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B), ("C", *_ATOM_C), ("H", *d)])
        _new_scan_point(conn, ids["degenerate_bond_point"], i, gid)
        _new_coordinate_value(conn, ids["degenerate_bond_point"], i, 1, stored)

    # --- One point whose geometry is missing atom_index=4 -----------------
    ids["missing_atom_point"] = _new_scan_calculation(conn, entry)
    _new_quartet_coordinate(
        conn, ids["missing_atom_point"], 1, start_value=_LEGACY_START
    )
    for i, (stored, true_dihedral) in enumerate(zip(_LEGACY_OFFSETS, _LEGACY_TRUE), start=1):
        d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
        atoms = [("C", *_ATOM_A), ("C", *_ATOM_B), ("C", *_ATOM_C)]
        if i != 3:
            atoms.append(("H", *d))
        gid = _new_geometry(conn, atoms)
        _new_scan_point(conn, ids["missing_atom_point"], i, gid)
        _new_coordinate_value(conn, ids["missing_atom_point"], i, 1, stored)

    # --- One point with geometry_id IS NULL --------------------------------
    ids["missing_geometry_point"] = _new_scan_calculation(conn, entry)
    _new_quartet_coordinate(
        conn, ids["missing_geometry_point"], 1, start_value=_LEGACY_START
    )
    for i, (stored, true_dihedral) in enumerate(zip(_LEGACY_OFFSETS, _LEGACY_TRUE), start=1):
        if i == 3:
            _new_scan_point(conn, ids["missing_geometry_point"], i, None)
        else:
            d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
            gid = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B), ("C", *_ATOM_C), ("H", *d)])
            _new_scan_point(conn, ids["missing_geometry_point"], i, gid)
        _new_coordinate_value(conn, ids["missing_geometry_point"], i, 1, stored)

    # --- One point declaring a non-degree value_unit on itself -------------
    # Distinct from coordinate_unit_mismatch below: the coordinate row here
    # correctly declares 'degree', and only one POINT disagrees -- exercising
    # _series_points's per-point unit check rather than the eligibility SQL.
    ids["point_unit_mismatch"] = _new_scan_calculation(conn, entry)
    _new_quartet_coordinate(
        conn, ids["point_unit_mismatch"], 1, start_value=_LEGACY_START
    )
    for i, (stored, true_dihedral) in enumerate(zip(_LEGACY_OFFSETS, _LEGACY_TRUE), start=1):
        d = _place_fourth_atom(_ATOM_A, _ATOM_B, _ATOM_C, _BOND_LENGTH, _BOND_ANGLE_DEG, true_dihedral)
        gid = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B), ("C", *_ATOM_C), ("H", *d)])
        _new_scan_point(conn, ids["point_unit_mismatch"], i, gid)
        unit = "angstrom" if i == 3 else None
        _new_coordinate_value(conn, ids["point_unit_mismatch"], i, 1, stored, value_unit=unit)

    # --- coordinate_kind = 'improper', otherwise shaped like 'legacy' -----
    # If the eligibility SQL's coordinate_kind = 'dihedral' clause were
    # dropped, this series' geometry (built for the dihedral formula) would
    # pass the shifted-conforms proof and convert -- so this is a live
    # positive control for that clause, not just a shape check.
    ids["improper_kind"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=_LEGACY_TRUE,
        kind="improper",
    )

    # --- Near-miss: not a dihedral ---------------------------------------
    ids["non_dihedral"] = _new_scan_calculation(conn, entry)
    _new_bond_coordinate(conn, ids["non_dihedral"], 1)
    g = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B)])
    _new_scan_point(conn, ids["non_dihedral"], 1, g)
    _new_coordinate_value(conn, ids["non_dihedral"], 1, 1, 1.5)

    # --- Near-miss: coordinate-level declared unit is not degree ----------
    ids["coordinate_unit_mismatch"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=_LEGACY_TRUE,
        unit="angstrom",
    )

    # --- Near-miss: start_value IS NULL -----------------------------------
    ids["no_start_value"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=None,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=_LEGACY_TRUE,
    )

    return ids


#: What must convert.
_CONVERTED = ("legacy", "legacy_decimal")

#: What must not move under upgrade(), and the clause/shape each one pins.
_UNTOUCHED = (
    "already_conforms_anchor0",
    "already_conforms_anchor360",
    "correct_ordinary_anchor",
    "geometry_disagrees",
    "one_bad_point",
    "near_collinear_point",
    "degenerate_bond_point",
    "missing_atom_point",
    "missing_geometry_point",
    "point_unit_mismatch",
    "improper_kind",
    "non_dihedral",
    "coordinate_unit_mismatch",
    "no_start_value",
)


def _all_values(conn) -> dict[int, dict[int, float]]:
    """Every ``(calculation_id, point_index) -> coordinate_value`` in the DB,
    grouped by calculation, for coordinate_index=1 (the only coordinate every
    seeded series has)."""
    rows = conn.execute(
        text(
            "SELECT calculation_id, point_index, coordinate_value "
            "FROM calc_scan_point_coordinate_value WHERE coordinate_index = 1"
        )
    ).all()
    result: dict[int, dict[int, float]] = {}
    for row in rows:
        result.setdefault(row.calculation_id, {})[row.point_index] = row.coordinate_value
    return result


def _full_row_set(conn) -> set[tuple]:
    """Every row of the guarded table, as an exact, orderless snapshot."""
    return {
        (row.calculation_id, row.point_index, row.coordinate_index, row.coordinate_value)
        for row in conn.execute(
            text(
                "SELECT calculation_id, point_index, coordinate_index, coordinate_value "
                "FROM calc_scan_point_coordinate_value"
            )
        ).all()
    }


def _calculation_ref(conn, calculation_id: int) -> str:
    return conn.scalar(
        text("SELECT public_ref FROM calculation WHERE id = :id"), {"id": calculation_id}
    )


def _upgraded(harness) -> dict[str, int]:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    return ids


# ---------------------------------------------------------------------------
# upgrade(): conversion correctness
# ---------------------------------------------------------------------------


def test_legacy_series_converts_bit_exactly_for_integer_valued_fixture(harness) -> None:
    """The one case where 'exact' is a literal claim: every stored/start
    value here is an integer degree, exactly representable in IEEE754
    double precision, so ``start + stored`` round-trips bit-for-bit. Real
    6-decimal deposits do not, in general -- see
    test_upgrade_conversion_is_correct_within_a_stated_tolerance_not_claimed_bit_exact.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        values = _all_values(conn)

    converted = values[ids["legacy"]]
    for i, expected in enumerate(_LEGACY_TRUE, start=1):
        assert converted[i] == expected, (
            f"point_index={i} did not land bit-exactly on start_value + coordinate_value"
        )


def test_upgrade_conversion_is_correct_within_a_stated_tolerance_not_claimed_bit_exact(
    harness,
) -> None:
    """At realistic (6-decimal-place) deposit precision, ``start_value +
    coordinate_value`` is not guaranteed to be bit-exact in IEEE754 -- ADR
    0020's 2026-08-31 amendment measures 29% of real (start_value,
    coordinate_value) pairs failing to round-trip bit-exactly, with a
    maximum deviation of 5.684e-14 degrees. This test asserts closeness
    against that same order of magnitude (comfortably looser, so it is not
    fragile to which specific pairs round awkwardly) rather than equality.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        values = _all_values(conn)

    converted = values[ids["legacy_decimal"]]
    for i, expected in enumerate(_LEGACY_DECIMAL_TRUE, start=1):
        assert converted[i] == pytest.approx(expected, abs=1e-9), (
            f"point_index={i} drifted further from start_value + coordinate_value "
            "than IEEE754 rounding alone accounts for"
        )


def test_already_conforming_series_are_skipped_not_converted(harness) -> None:
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        values = _all_values(conn)

    assert values[ids["already_conforms_anchor0"]] == {
        1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0, 6: 60.0,
    }
    for i, expected in enumerate(_ANCHOR360_STORED, start=1):
        assert values[ids["already_conforms_anchor360"]][i] == expected
    for i, expected in enumerate(_LEGACY_TRUE, start=1):
        assert values[ids["correct_ordinary_anchor"]][i] == expected


def test_near_misses_are_left_completely_untouched(harness) -> None:
    """One assertion per near-miss, so a widened gate fails exactly here."""
    with_original: dict[str, dict[int, float]] = {}

    # Seed once on the parent revision, snapshot before converting, then
    # upgrade -- rather than re-deriving "what should be unchanged" from the
    # seed function, which could silently drift from what was actually
    # written.
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = _all_values(conn)
    for label in _UNTOUCHED:
        with_original[label] = dict(before[ids[label]])

    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        after = _all_values(conn)

    for label in _UNTOUCHED:
        assert after[ids[label]] == with_original[label], (
            f"{label} changed under upgrade(); it is one clause (or one "
            "not-provable point) away from the legacy series this revision "
            "converts, so the gate that excludes it has been widened"
        )
    # And the positive controls actually did move, so this test cannot pass
    # by the gate refusing everything.
    for label in _CONVERTED:
        assert after[ids[label]] != before[ids[label]]


def test_bad_geometry_series_is_left_untouched_and_logged(harness) -> None:
    """``caplog`` cannot see this: ``harness.run`` drives ``alembic`` as a
    real subprocess (so a genuinely fresh interpreter runs ``upgrade()``,
    not a monkeypatched in-process one), and pytest's log-capture fixture
    only ever sees logging inside its own process. The migration's own
    stdout/stderr -- what an operator actually reads -- is what
    ``subprocess.run(capture_output=True)`` hands back on ``completed``,
    and that is what this test checks instead.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["geometry_disagrees"]])

    completed = harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["geometry_disagrees"]])
    assert after == before, "a series whose geometry disagrees throughout was modified"

    combined = completed.stdout + completed.stderr
    assert f"calculation_id={ids['geometry_disagrees']}" in combined
    assert "left untouched" in combined


def test_partial_failure_converts_nothing_in_the_series(harness) -> None:
    """Five of six points would individually confirm the conversion; the
    revision must still convert zero of them."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["one_bad_point"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["one_bad_point"]])
    assert after == before, (
        "one_bad_point converted some or all of its points; a single "
        "unprovable point must refuse the whole series, not just itself"
    )


# ---------------------------------------------------------------------------
# upgrade(): the "not provable" refusal family
# ---------------------------------------------------------------------------
#
# Each test below reaches provable=False for a genuinely different reason,
# and each is a live positive control: the fixture's geometry is built so
# that IF the specific guard it targets were disabled, the series would
# convert (or the point would be silently treated as provable), not merely
# "look different". A prior version of this file never drove any fixture to
# provable=False at all -- one_bad_point fails on the TOLERANCE branch, and
# the old unit-mismatch fixture was caught by the eligibility SQL before
# reaching a single point -- so five mutants covering this family survived
# undetected. See the migration report for the mutation kill for each.


def test_near_collinear_point_refuses_the_whole_series(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["near_collinear_point"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["near_collinear_point"]])
    assert after == before, "a series with a near-collinear point was converted"


def test_degenerate_bond_point_refuses_the_whole_series(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["degenerate_bond_point"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["degenerate_bond_point"]])
    assert after == before, "a series with a degenerate wing bond was converted"


def test_missing_atom_point_refuses_the_whole_series(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["missing_atom_point"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["missing_atom_point"]])
    assert after == before, "a series with a point missing one atom was converted"


def test_missing_geometry_point_refuses_the_whole_series(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["missing_geometry_point"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["missing_geometry_point"]])
    assert after == before, "a series with a point missing its geometry_id was converted"


def test_point_level_unit_mismatch_refuses_the_whole_series(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["point_unit_mismatch"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["point_unit_mismatch"]])
    assert after == before, (
        "a series with one point declaring a non-degree value_unit was converted"
    )


def test_improper_kind_is_never_converted(harness) -> None:
    """The positive control for the eligibility SQL's coordinate_kind =
    'dihedral' clause: this series' geometry is built exactly like
    'legacy', so if the clause were dropped it WOULD pass the shifted-
    conforms proof and convert."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["improper_kind"]])

    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["improper_kind"]])
    assert after == before, (
        "an 'improper' coordinate was converted -- the coordinate_kind = "
        "'dihedral' eligibility clause is not doing its job"
    )


def test_rerunning_upgrade_after_conversion_is_a_no_op(harness) -> None:
    """Calls the revision's own upgrade() a second time, directly -- not via
    ``alembic upgrade``, which would no-op on version tracking alone and
    prove nothing about the SQL itself."""
    import importlib.util

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        before = _full_row_set(conn)

    spec = importlib.util.spec_from_file_location("_scan_axis_rerun", _REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with harness.engine.connect() as connection:
        with connection.begin():
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.upgrade()
        after = _full_row_set(connection)

    assert after == before, "re-running upgrade() on already-converted data changed rows"
    # And the earlier conversion really did happen, so this is not a
    # vacuous pass from a series that never converted in the first place.
    converted_rows = {row for row in before if row[0] == ids["legacy"]}
    assert converted_rows == {
        (ids["legacy"], i, 1, expected) for i, expected in enumerate(_LEGACY_TRUE, start=1)
    }


# ---------------------------------------------------------------------------
# upgrade(): the reversal-record log
# ---------------------------------------------------------------------------


def test_upgrade_logs_a_reversal_record_for_every_converted_series(harness) -> None:
    """This log line is the only record of what upgrade() changed --
    downgrade() cannot reconstruct it (see below) -- so its content is load-
    bearing, not decoration: calculation_id, calculation_ref, coordinate_index,
    start_value, and a literal UPDATE naming exactly this series.

    Checked against the migration subprocess's actual stdout/stderr, not
    ``caplog`` -- see
    ``test_bad_geometry_series_is_left_untouched_and_logged`` for why
    ``caplog`` cannot see a subprocess's logging at all.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        legacy_ref = _calculation_ref(conn, ids["legacy"])

    completed = harness.run("upgrade", _MIGRATION.revision)
    combined = completed.stdout + completed.stderr

    lines = [
        line
        for line in combined.splitlines()
        if "SCAN_AXIS_REVERSAL_RECORD" in line
        and f"calculation_id={ids['legacy']} " in line
    ]
    assert lines, (
        "no SCAN_AXIS_REVERSAL_RECORD line named the converted 'legacy' series "
        f"in:\n{combined}"
    )
    message = lines[0]
    assert f"calculation_ref={legacy_ref}" in message
    assert "coordinate_index=1" in message
    assert f"start_value={_LEGACY_START!r}" in message
    assert "UPDATE calc_scan_point_coordinate_value" in message
    assert f"WHERE calculation_id = {ids['legacy']} AND coordinate_index = 1" in message

    # A series that was skipped, not converted, gets no reversal record --
    # there is nothing to reverse.
    assert not any(
        "SCAN_AXIS_REVERSAL_RECORD" in line
        and f"calculation_id={ids['already_conforms_anchor0']} " in line
        for line in combined.splitlines()
    )


# ---------------------------------------------------------------------------
# downgrade(): refuses, always
# ---------------------------------------------------------------------------


def test_downgrade_performs_no_writes(harness) -> None:
    """The blocking requirement, checked directly: after upgrade(), calling
    downgrade() must not change a single row -- not the converted series,
    not the untouched ones, not the already-conforming ones."""
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        before = _full_row_set(conn)

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        after = _full_row_set(conn)
    assert after == before, "downgrade() wrote to calc_scan_point_coordinate_value"

    # In particular, the specific population the old margin-based guard
    # would have destroyed: a correct deposit at an ordinary anchor.
    with harness.engine.connect() as conn:
        values = _all_values(conn)
    for i, expected in enumerate(_LEGACY_TRUE, start=1):
        assert values[ids["correct_ordinary_anchor"]][i] == expected, (
            "downgrade() moved a correctly-deposited series at an ordinary "
            "anchor -- exactly the corruption the blocking review finding "
            "was about"
        )
    # And the series named in the review as one the old guard's docstring
    # claimed to protect but did not: unaffected, because nothing is written.
    for i, expected in enumerate(_ANCHOR360_STORED, start=1):
        assert values[ids["already_conforms_anchor360"]][i] == expected


def test_downgrade_logs_the_refusal_and_how_to_reverse_by_hand(harness) -> None:
    _upgraded(harness)
    completed = harness.run("downgrade", _MIGRATION.parent)
    assert completed.returncode == 0, "downgrade() must complete, not raise"

    combined = completed.stdout + completed.stderr
    assert "cannot be reversed automatically" in combined
    assert "SCAN_AXIS_REVERSAL_RECORD" in combined
    assert "makes no changes" in combined


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_the_revision_touches_no_schema() -> None:
    """A data migration stays a data migration. Scanned over the file with
    its module docstring removed, mirroring
    ``test_conformer_anchor_backfill_migration.py``'s equivalent check."""
    import ast

    source = _REVISION_FILE.read_text()
    module = ast.parse(source)
    body = module.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        lines = source.splitlines(keepends=True)
        executable = "".join(lines[body[0].end_lineno :])
    else:
        executable = source

    for forbidden in (
        "op.create_table",
        "op.drop_table",
        "op.add_column",
        "op.drop_column",
        "op.alter_column",
        "op.create_index",
        "op.drop_index",
        "op.create_check_constraint",
        "op.create_unique_constraint",
        "op.drop_constraint",
        "DISABLE TRIGGER",
        "ALTER TYPE",
    ):
        assert forbidden not in executable, (
            f"{forbidden} appeared in a revision documented as a pure data migration"
        )


def test_the_revision_does_not_import_the_conformance_check() -> None:
    """The self-containment invariant, enforced rather than promised: the
    revision must carry its own geometry math, not import
    ``app.services.scan_coordinate_conformance`` or any other ``app`` module.

    Checked against the parsed AST's actual ``import``/``from ... import``
    statements, not a text search -- the module docstring and a code comment
    both legitimately *name* the check module in prose, to explain why its
    arithmetic is duplicated rather than imported, so a text-level ban would
    refuse the very sentence that justifies the duplication.
    """
    import ast

    source = _REVISION_FILE.read_text()
    module = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "app" not in imported_roots, (
        f"the revision imports from 'app': {imported_roots!r} -- a migration "
        "must carry its own logic, not reach into application code that "
        "moves independently of it"
    )


def test_downgrade_does_no_reads_or_writes_at_the_sql_level() -> None:
    """Structural check on the source: ``downgrade()`` must not contain a
    ``bind.execute`` call at all -- not just "happens to write nothing" for
    today's fixtures, but incapable of it by construction."""
    source = _REVISION_FILE.read_text()
    match = re.search(r"\ndef downgrade\(\)[^\n]*:\n((?:[ \t].*\n|\n)*)", source)
    assert match, "could not locate downgrade() in the revision source"
    body = match.group(1)
    assert "bind.execute" not in body
    assert "op.get_bind" not in body
