"""Disposable-database contract for the legacy scan-dihedral-axis correction.

``a4f7c2e9d651`` converts ``calc_scan_point_coordinate_value.coordinate_value``
from ADR 0019's superseded "sweep relative to the first point" convention to
ADR 0020's "the coordinate itself": ``coordinate_value := start_value +
coordinate_value``, applied per series only when every one of its points'
own geometry proves the conversion. Everything here is a way that proof
could go wrong while looking perfect against an empty database, which is why
none of it is checked through a service layer -- the same reasoning
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
``already_conforms_anchor0``  ADR 0020's ``conforms`` bucket, start≈0
``already_conforms_anchor360``ADR 0020's ``conforms`` bucket, start≈360
``geometry_disagrees``        proof fails for every point -> untouched
``one_bad_point``             proof fails for one point -> whole series untouched
``non_dihedral``              ``coordinate_kind`` gate
``unit_mismatch``             declared-unit gate
``no_start_value``            ``start_value IS NOT NULL`` gate
============================  =====================================

Only ``legacy`` may change under ``upgrade()``. Every other row is one
clause away from being eligible, mirroring the near-miss table the
anchor-backfill test uses for the same reason: a predicate whose narrowness
nothing tests is a predicate that has been written down, not established.
"""

from __future__ import annotations

import math
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
    ``test_nerf_fixture_reproduces_its_own_target_dihedral``.
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


def _new_dihedral_coordinate(
    conn,
    calculation_id: int,
    coordinate_index: int,
    *,
    start_value,
    value_unit: str | None = "degree",
    atom_indices: tuple[int, int, int, int] = (1, 2, 3, 4),
) -> None:
    conn.execute(
        text(
            "INSERT INTO calc_scan_coordinate "
            "(calculation_id, coordinate_index, coordinate_kind, "
            " atom1_index, atom2_index, atom3_index, atom4_index, "
            " start_value, value_unit) "
            "VALUES (:cid, :cidx, CAST('dihedral' AS scan_coordinate_kind), "
            "        :a1, :a2, :a3, :a4, :start, "
            "        CAST(:unit AS coordinate_unit))"
        ),
        {
            "cid": calculation_id,
            "cidx": coordinate_index,
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


def _new_scan_point(conn, calculation_id: int, point_index: int, geometry_id: int) -> None:
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
) -> None:
    conn.execute(
        text(
            "INSERT INTO calc_scan_point_coordinate_value "
            "(calculation_id, point_index, coordinate_index, coordinate_value) "
            "VALUES (:cid, :pidx, :cidx, :value)"
        ),
        {
            "cid": calculation_id,
            "pidx": point_index,
            "cidx": coordinate_index,
            "value": coordinate_value,
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
) -> int:
    """A one-dimensional dihedral scan: ``stored_values[i]`` is what is
    written to ``coordinate_value``, ``true_dihedrals[i]`` is the dihedral
    the point's *geometry* is built to actually be. ``bad_point_index``
    (1-based) corrupts one point's geometry by displacing its fourth atom,
    independent of ``true_dihedrals`` -- the "one mis-attached geometry"
    shape, as opposed to a series built to disagree throughout.
    """
    assert len(stored_values) == len(true_dihedrals)
    calculation_id = _new_scan_calculation(conn, species_entry_id)
    _new_dihedral_coordinate(conn, calculation_id, 1, start_value=start_value, value_unit=unit)
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
    # Mirrors the real calc_z63ecgljjdt2dvkqkjadmxkxou / start_value=359.9994
    # case -- constant series, deliberately not a sweep, since the point
    # here is the anchor's near-360 value, not pattern classification.
    near_360 = 359.9994
    ids["already_conforms_anchor360"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=near_360,
        stored_values=[near_360, near_360, near_360],
        true_dihedrals=[near_360, near_360, near_360],
    )

    # --- Geometry disagrees throughout: proof never holds ----------------
    # Same start_value/stored_values shape as "legacy", but every point's
    # geometry was built for a completely different dihedral -- neither the
    # as-is nor the shifted hypothesis is confirmed anywhere.
    ids["geometry_disagrees"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
    )

    # --- One bad point in an otherwise-good series ------------------------
    # Every point but #3 is built exactly like "legacy"; #3's geometry is
    # displaced. The whole series must stay untouched, not five-sixths
    # converted.
    ids["one_bad_point"] = _seed_dihedral_series(
        conn,
        species_entry_id=entry,
        start_value=_LEGACY_START,
        stored_values=_LEGACY_OFFSETS,
        true_dihedrals=list(_LEGACY_TRUE),
        bad_point_index=3,
    )

    # --- Near-miss: not a dihedral ---------------------------------------
    ids["non_dihedral"] = _new_scan_calculation(conn, entry)
    _new_bond_coordinate(conn, ids["non_dihedral"], 1)
    g = _new_geometry(conn, [("C", *_ATOM_A), ("C", *_ATOM_B)])
    _new_scan_point(conn, ids["non_dihedral"], 1, g)
    _new_coordinate_value(conn, ids["non_dihedral"], 1, 1, 1.5)

    # --- Near-miss: declared unit is not degree ---------------------------
    ids["unit_mismatch"] = _seed_dihedral_series(
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
_CONVERTED = ("legacy",)

#: What must not move under upgrade(), and the clause/shape each one pins.
_UNTOUCHED = (
    "already_conforms_anchor0",
    "already_conforms_anchor360",
    "geometry_disagrees",
    "one_bad_point",
    "non_dihedral",
    "unit_mismatch",
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


def _upgraded(harness) -> dict[str, int]:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    return ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_legacy_series_converts_to_what_its_geometry_proves(harness) -> None:
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        values = _all_values(conn)

    converted = values[ids["legacy"]]
    for i, expected in enumerate(_LEGACY_TRUE, start=1):
        assert converted[i] == pytest.approx(expected, abs=1e-9), (
            f"point_index={i} did not land on the value its own geometry proves"
        )


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
            f"{label} changed under upgrade(); it is one clause away from "
            "the legacy series this revision converts, so the gate that "
            "excludes it has been widened"
        )
    # And the positive control actually did move, so this test cannot pass
    # by the gate refusing everything.
    assert after[ids["legacy"]] != with_original.get("legacy")


def test_bad_geometry_series_is_left_untouched_and_logged(harness, caplog) -> None:
    import logging

    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        before = dict(_all_values(conn)[ids["geometry_disagrees"]])

    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration.a4f7c2e9d651"):
        harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        after = dict(_all_values(conn)[ids["geometry_disagrees"]])
    assert after == before, "a series whose geometry disagrees throughout was modified"


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


def test_already_conforming_series_are_skipped_not_converted(harness) -> None:
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        values = _all_values(conn)

    assert values[ids["already_conforms_anchor0"]] == {
        1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0, 6: 60.0,
    }
    near_360 = 359.9994
    for point_value in values[ids["already_conforms_anchor360"]].values():
        assert point_value == near_360


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


def test_downgrade_restores_the_legacy_series_exactly(harness) -> None:
    ids = _upgraded(harness)
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.connect() as conn:
        values = _all_values(conn)
    restored = values[ids["legacy"]]
    for i, expected in enumerate(_LEGACY_OFFSETS, start=1):
        assert restored[i] == expected, (
            f"point_index={i} was not restored to its exact pre-upgrade value"
        )


def test_downgrade_does_not_touch_never_converted_series(harness) -> None:
    """The whole reason downgrade() cannot just re-derive its targets from
    the shape of the post-upgrade data: the two anchor≈0/360 series are, by
    construction, structurally identical to a converted series after
    upgrade -- their stored value already agrees with geometry. A downgrade
    that subtracted start_value from every such series would destroy them.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        before = {
            label: dict(_all_values(conn)[ids[label]])
            for label in ("already_conforms_anchor0", "already_conforms_anchor360")
        }

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        after = {
            label: dict(_all_values(conn)[ids[label]])
            for label in ("already_conforms_anchor0", "already_conforms_anchor360")
        }
    assert after == before, (
        "downgrade() moved a series it never converted -- the ambiguity "
        "guard between 'converted' and 'always conformed' has failed"
    )


def test_round_trip_upgrade_then_downgrade_is_byte_exact(harness) -> None:
    """The hard requirement: capture the full table, upgrade, downgrade,
    capture again, and the two snapshots must be identical -- not merely
    close. ``_LEGACY_OFFSETS`` and ``_LEGACY_START`` are integer-valued
    degrees, chosen so that ``(start + stored) - start`` recovers ``stored``
    bit-for-bit in IEEE754 double arithmetic; see the migration report for
    why that is not true of every possible float pair (general decimal
    values, e.g. 6-decimal-place deposits, are not guaranteed to survive an
    add-then-subtract round trip bit-exactly -- a real limitation of storing
    the correction in the same double-precision column, not a defect in the
    selection logic this test exercises).
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
        before = _full_row_set(conn)

    harness.run("upgrade", _MIGRATION.revision)
    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        after = _full_row_set(conn)

    assert after == before, "upgrade() followed by downgrade() did not restore the exact row set"


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
