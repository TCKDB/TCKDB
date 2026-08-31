"""Convert legacy relative-axis dihedral scan series to ADR 0020's contract.

ADR 0020 (``docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md``)
fixes what ``calc_scan_point_coordinate_value.coordinate_value`` means: the
internal coordinate itself, at that point's own sampled geometry, in that
coordinate's own unit -- never a displacement, never relative to
``calc_scan_coordinate.start_value``. The deposited corpus predates that
contract: every one of its 46 dihedral series holds ADR 0019's superseded
"sweep relative to the first point" convention, with the absolute anchor held
separately in ``start_value``. This revision is the one-time, in-place
correction ADR 0020 authorises and describes exactly:

    ``coordinate_value := start_value + coordinate_value``

-- an operation that keeps ``start_value`` rather than consuming it, so a
converted row *can* be reversed once an operator knows which rows changed.
That is weaker than "exact and invertible", the phrase ADR 0020 used before
its 2026-08-31 amendment: IEEE754 double-precision arithmetic does not
guarantee the naive round trip is bit-exact (see the amendment for the
measurement), and retaining ``start_value`` does not let the *database* work
out on its own which rows to reverse -- only a human who already knows can do
that. See "downgrade() is one-way, by design" below for why this migration's
``downgrade()`` performs no writes. ADR 0020's own three-part argument for why
the correction is permissible at all is measured, not assumed here: every
affected row is ``record_review.status = 'not_reviewed'`` at ``calc_quality.raw``
(see "Why this does not use the accepted-science repair ledger" below), and it
"refuses to guess" -- the conversion is applied to a series only when its
geometry *proves* the conversion is right, point by point.

What "proof" means, and why it is duplicated here rather than imported
------------------------------------------------------------------------
``app.services.scan_coordinate_conformance`` (merged the day before this
revision) is the read-only conformance check ADR 0020 asks for: it recomputes
a dihedral from a scan point's own stored geometry and compares it against
what is stored, with a tolerance derived from deposit precision and the
``1 / (r * sin(theta))`` conditioning of a four-atom dihedral, floored at
``1e-3`` degrees and capped at ``1.0`` degree (the check's module docstring
gives the measured reasoning for both numbers; the 0.5/1.0-degree floor that
was considered and rejected is the same rejection this revision inherits).
This migration needs exactly that arithmetic, and deliberately does **not**
import it: a migration has to keep meaning the same thing when it is replayed
against a years-old backup, on whatever ``app`` looks like by then, and the
only way to guarantee that is for the file to carry its own math. The
functions below are the check's ``dihedral_deg``, ``bond_angle_deg``,
``_wrap_deg``, ``_sigma_pred_dihedral_deg``, ``_tolerance_degrees`` and
``infer_precision_decimals``, copied rather than generalised into a shared
module -- duplicating roughly fifty lines here is the correct trade, not an
oversight, for the same reason ADR 0020 gives for not centralising the
conversion arithmetic itself. Unlike the check, this file uses plain
``tuple``-based vector arithmetic instead of numpy: one fewer runtime
dependency for code that must still run correctly when read years from now.

The rule, per series
---------------------
A coordinate series (``calculation_id``, ``coordinate_index``) is a
**candidate** only when:

* ``coordinate_kind = 'dihedral'`` and the coordinate's own ``value_unit`` is
  ``'degree'`` -- this is what makes the series eligible at all; a ``NULL``
  or non-degree coordinate-level unit excludes it, even if every point
  happens to declare ``'degree'`` itself, and no point's own ``value_unit``
  can rescue it. A point's ``value_unit``, when set, is checked separately
  and can only *disqualify* a series (declaring something other than
  ``'degree'`` on any single point), never rescue one whose coordinate-level
  unit is not ``'degree'``;
* ``start_value IS NOT NULL``;
* every one of its scan points has a coordinate value for this coordinate
  index, a non-null ``geometry_id``, and that geometry has all four atoms of
  the dihedral's quartet.

For a candidate series, every point must recompute successfully (no
degenerate bond, no near-collinear quartet, derived tolerance under the
1-degree ceiling) -- one unprovable point refuses the **entire series**,
never a partial conversion. Then:

* if every point's *stored* value already agrees with its own geometry
  (within tolerance), the series **already conforms** and is left alone --
  this is the ``conforms`` bucket ADR 0020 names for three of the 46 series
  (``start_value`` within a few ten-thousandths of a degree of a multiple of
  360, so the relative and absolute readings coincide);
* else if every point's ``start_value + stored`` agrees with its own
  geometry, the series is **converted**;
* else the series is left completely untouched and logged -- geometry does
  not confirm the conversion.

Measured against the deployed database on 2026-08-31 (see the conformance
report this revision follows): 46 candidate series, 2116 rows in scope. 43
classify ``consistent_with_legacy_relative_axis`` and are expected to
convert; 3 (``calc_vsaz3uwpxdbbkrmgmaj66z7klm`` at ``start_value=359.9999``,
``calc_z63ecgljjdt2dvkqkjadmxkxou`` at ``359.9994``, and
``calc_b7gspa3garcqoth42bfyqo2iwe`` at ``0.0``) already conform and are
expected to be skipped. Those three remain roughly ``1e-4`` degrees from
exact after this revision runs -- below the deposit noise floor the
conformance check itself uses (six decimal places, ``1e-3``-degree floor) --
and that is accepted rather than chased: their anchor is within a few
ten-thousandths of a degree of a full turn, so "convert" and "leave alone"
differ by an amount smaller than the check can distinguish from noise, and
guessing between them is exactly what ADR 0020 refuses to do.

Why this does not use the accepted-science repair ledger
----------------------------------------------------------
``calc_scan_point_coordinate_value`` is guarded by ``trg_as_child_...``
(``c6f2a9d4e7b1``, root type ``calculation`` via ``calculation_id``), the
same regime ``b8e3f1a7c250`` used ``accepted_science_repair`` /
``accepted_science_repair_change`` to write through. That precedent does not
apply here, on purpose. ``b8e3f1a7c250`` repaired a link that changed no
scientific claim -- its own argument was that ``evidence_coverage`` and
``optimization_chain_count`` were measured unchanged -- so declaring a repair
against an accepted root was defensible even in principle. This revision
changes a stored *number*. ADR 0020 authorises that rewrite on exactly one
ground: every affected row is ``not_reviewed``/``raw``, so nothing ADR 0003
freezes is in scope. It does not authorise it for a row that has been
accepted, and does not ask for that case to be handled gracefully. So this
revision declares nothing, and if a future or self-hosted deployment has
approved one of these calculations, ``tckdb_raise_if_accepted`` refuses the
``UPDATE`` and the whole transaction rolls back -- correctly: the premise
this revision runs under does not hold there, and failing loudly is the
right outcome, not a gap to route around.

downgrade() is one-way, by design
------------------------------------
An earlier draft of this revision made ``downgrade()`` reverse the same way
``upgrade()`` converts: re-derive the answer from geometry, on the theory
that a series whose *current* value agrees with geometry but whose value
*minus* ``start_value`` would not is a converted series, and a series where
both agree is one that was always fine and must be left alone. That theory
has a real margin, and adversarial review measured it directly: the
distinguishing test's residual is ``2 * (360 - start_value)`` for a series
anchored near a full turn, so the guard's true tolerance is half the
stated one. On the real corpus, ``calc_z63ecgljjdt2dvkqkjadmxkxou`` at
``start_value = 359.9994`` sits 6e-4 degrees outside that halved margin --
the guard misses it, and a downgrade seeded with that series' real
relative-sweep values moves all of its points by ``-359.9994``, corrupting
exactly the series the module docstring claimed it protected.

That was the narrow failure. The wide one is structural and has no
tolerance that fixes it: after a successful conversion, a row this
migration changed and a row deposited **correctly** under ADR 0020 at an
ordinary, non-trivial ``start_value`` are identical in every stored column.
Both have their first point equal to ``start_value``. Both agree with their
own geometry. There is no query -- geometric, statistical, or otherwise --
that tells them apart, because the two states genuinely carry the same
information; nothing distinguishing was ever recorded, and no schema change
is available here to start recording it now. A margin-based guard cannot
fix an underdetermined problem; it can only move where it is wrong.

So ``downgrade()`` performs no writes at all. It logs, once, why the
correction cannot be reversed automatically -- the argument above -- and
how to reverse it by hand: using the reversal record ``upgrade()`` itself
wrote (see "What ``upgrade()`` logs" below), an operator who knows which
rows this migration actually touched can run the literal inverse ``UPDATE``
themselves, scoped to exactly those rows. Nothing here is a query the
database could run on its own without that external knowledge.

This diverges from the rest of this migration chain's usual both-directions
rule -- ``b8e3f1a7c250``, for instance, reverses by primary key from its own
repair ledger. It diverges deliberately: a downgrade that silently corrupts
correct data is worse than one that refuses, and this table has nothing to
catch a wrong automatic reversal against. ADR 0020 measures zero scan
calculation artifacts on the hosted instance, so there is no raw log to
re-derive a corrupted series from afterward -- a bad automatic reversal here
would look exactly like a correct one and be unrecoverable in exactly the
way the original problem was.

What ``upgrade()`` logs
------------------------
Every series ``upgrade()`` actually converts produces one log line naming,
in a form an operator can act on directly: ``calculation_id``, the
calculation's public ``calc_...`` ref, ``coordinate_index``, the
``start_value`` applied, and a literal, ready-to-run ``UPDATE`` statement
that undoes exactly that series and no other. That log line -- not a
database table, since no schema change is permitted in this revision -- is
the reversal record referenced above. It is written whether or not any
series is ever manually reversed; the record is the point, not the act.

Re-running ``upgrade()`` on already-converted data is a no-op for an
unrelated reason: a converted series' stored value now agrees with its own
geometry, so the "already conforms" branch fires on the second run and
nothing is written a second time. ``tests/db/test_scan_dihedral_axis_correction_migration.py``
asserts this directly rather than assuming it, per this repository's own
house rule against a check that has never observed a failure.

No schema changes. ``upgrade()`` touches only
``calc_scan_point_coordinate_value.coordinate_value``, selected by primary
key (``calculation_id``, ``point_index``, ``coordinate_index``).
``downgrade()`` touches nothing.

Revision ID: a4f7c2e9d651
Revises: b8e3f1a7c250
Create Date: 2026-08-31
"""

import logging
import math
from dataclasses import dataclass
from typing import Sequence, Union

from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f7c2e9d651"
down_revision: Union[str, Sequence[str], None] = "b8e3f1a7c250"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration.a4f7c2e9d651")

Vector = tuple[float, float, float]

# ---------------------------------------------------------------------------
# Geometry math and tolerance rule, duplicated from
# app.services.scan_coordinate_conformance as of 2026-08-31. See the module
# docstring for why this is a deliberate copy rather than an import: a
# migration must keep meaning what it says when replayed years from now,
# independent of wherever application code has moved by then.
# ---------------------------------------------------------------------------

#: ADR 0020: "where a quartet is near-collinear the dihedral is not a usable
#: coordinate at all". Below this, on either flanking bond angle, a point's
#: dihedral cannot be trusted.
_DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA = 0.05

#: Never let the derived tolerance collapse below what a 6-decimal-place
#: deposit can distinguish (ADR 0020). Explicitly not the 0.5/1.0-degree
#: floor rejected during this design as roughly four orders of magnitude too
#: loose.
_TOLERANCE_FLOOR_DEGREES = 1e-3

#: Above this, a derived tolerance is not "generous" -- it is unjudgeable,
#: and would silently pass an error the check exists to catch. See
#: ``app.services.scan_coordinate_conformance.TOLERANCE_CEILING_DEGREES`` for
#: the measured argument (an unbounded tolerance was observed to reach 184.9
#: degrees at 0 decimal places of deposit precision).
_TOLERANCE_CEILING_DEGREES = 1.0

#: Fallback deposit precision (decimal places on a Cartesian coordinate) when
#: it cannot be inferred from the sample itself.
_FALLBACK_PRECISION_DECIMALS = 6

#: Below this sample size, a precision reading is not trusted and the
#: fallback is used instead.
_MIN_SAMPLE_SIZE_TO_TRUST = 3

#: A degenerate (zero-length) bond makes direction undefined.
_MIN_BOND_LENGTH_ANGSTROM = 1e-6


def _sub(a: Vector, b: Vector) -> Vector:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: Vector) -> float:
    return math.sqrt(_dot(a, a))


def _wrap_deg(value: float) -> float:
    """Wrap an angle (or angle difference) into ``(-180, 180]`` degrees."""
    wrapped = (value + 180.0) % 360.0 - 180.0
    if wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def _bond_length_angstrom(a: Vector, b: Vector) -> float:
    return _norm(_sub(b, a))


def _bond_angle_deg(a: Vector, b: Vector, c: Vector) -> float:
    """Angle at ``b`` formed by ``a-b-c``, in degrees, in ``[0, 180]``."""
    v1 = _sub(a, b)
    v2 = _sub(c, b)
    cos_theta = _dot(v1, v2) / (_norm(v1) * _norm(v2))
    cos_theta = min(1.0, max(-1.0, cos_theta))
    return math.degrees(math.acos(cos_theta))


def _dihedral_deg(a: Vector, b: Vector, c: Vector, d: Vector) -> float:
    """Signed dihedral ``a-b-c-d``, in degrees, in ``(-180, 180]``.

    Standard "praxeolitic" formula, identical to
    ``app.services.scan_coordinate_conformance.dihedral_deg`` -- same sign
    convention, pinned there against RDKit.
    """
    b1 = _sub(b, a)
    b2 = _sub(c, b)
    b3 = _sub(d, c)
    b2_norm = _norm(b2)
    b2_unit = (b2[0] / b2_norm, b2[1] / b2_norm, b2[2] / b2_norm)
    n1 = _cross(b1, b2)
    n2 = _cross(b2, b3)
    x = _dot(n1, n2)
    y = _dot(_cross(n1, n2), b2_unit)
    return math.degrees(math.atan2(y, x))


def _quantization_sigma_angstrom(precision_decimals: int) -> float:
    lsb = 10.0**-precision_decimals
    return lsb / math.sqrt(12.0)


def _sigma_pred_dihedral_deg(
    precision_decimals: int,
    r_ab: float,
    r_cd: float,
    sin_theta_123: float,
    sin_theta_234: float,
) -> float:
    sigma_pos = _quantization_sigma_angstrom(precision_decimals)
    sensitivity = math.sqrt(
        (1.0 / (r_ab * sin_theta_123)) ** 2 + (1.0 / (r_cd * sin_theta_234)) ** 2
    )
    return math.degrees(sigma_pos * sensitivity)


def _tolerance_degrees(sigma_pred_deg: float) -> float | None:
    """``max(floor, 10 * sigma)``, or ``None`` when that exceeds the ceiling."""
    candidate = 10.0 * sigma_pred_deg
    if candidate > _TOLERANCE_CEILING_DEGREES:
        return None
    return max(_TOLERANCE_FLOOR_DEGREES, candidate)


def _infer_precision_decimals(
    values: list[float],
    *,
    fallback: int = _FALLBACK_PRECISION_DECIMALS,
    max_decimals: int = 12,
) -> int:
    """How many decimal places a sample of Cartesian coordinates was deposited at."""
    values = [v for v in values if v is not None]
    if len(values) < _MIN_SAMPLE_SIZE_TO_TRUST:
        return fallback
    needed = 0
    for value in values:
        for n in range(0, max_decimals + 1):
            if abs(round(value, n) - value) < 1e-9:
                needed = max(needed, n)
                break
        else:
            needed = max(needed, max_decimals)
    return needed


@dataclass(frozen=True)
class _PointProof:
    """One point's geometric evidence: what its own geometry says the
    dihedral is, and the tolerance to compare against. ``provable is False``
    means the quartet cannot be trusted at all (degenerate bond,
    near-collinear, or a tolerance too wide to mean anything) -- never a
    pass, never a failure, but enough to refuse the whole series."""

    point_index: int
    provable: bool
    expected_deg: float | None = None
    tolerance_deg: float | None = None
    reason: str | None = None


def _evaluate_point_proof(
    coords: dict[int, Vector],
    atom_indices: tuple[int, int, int, int],
    precision_decimals: int,
    point_index: int,
) -> _PointProof:
    resolved: list[Vector] = []
    for atom_index in atom_indices:
        coord = coords.get(atom_index)
        if coord is None:
            return _PointProof(
                point_index, False, reason=f"geometry has no atom_index={atom_index}"
            )
        resolved.append(coord)
    a, b, c, d = resolved

    r_ab = _bond_length_angstrom(a, b)
    r_cd = _bond_length_angstrom(c, d)
    if r_ab < _MIN_BOND_LENGTH_ANGSTROM or r_cd < _MIN_BOND_LENGTH_ANGSTROM:
        return _PointProof(point_index, False, reason="degenerate bond length in wing atoms")

    theta_123 = _bond_angle_deg(a, b, c)
    theta_234 = _bond_angle_deg(b, c, d)
    sin_123 = math.sin(math.radians(theta_123))
    sin_234 = math.sin(math.radians(theta_234))
    if min(sin_123, sin_234) < _DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA:
        return _PointProof(point_index, False, reason="near-collinear quartet")

    expected = _dihedral_deg(a, b, c, d)
    sigma_pred = _sigma_pred_dihedral_deg(precision_decimals, r_ab, r_cd, sin_123, sin_234)
    tolerance = _tolerance_degrees(sigma_pred)
    if tolerance is None:
        return _PointProof(
            point_index,
            False,
            reason=(
                f"derived tolerance from {precision_decimals} decimal place(s) exceeds "
                f"the {_TOLERANCE_CEILING_DEGREES:g} degree ceiling"
            ),
        )
    return _PointProof(point_index, True, expected_deg=expected, tolerance_deg=tolerance)


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------

#: ``calculation_ref`` (the calculation's public ``calc_...`` ref) is pulled
#: in purely so a converted series' log line can name something an operator
#: can act on without a database session open in front of them -- see "What
#: upgrade() logs" in the module docstring.
_ELIGIBLE_SERIES_SQL = """
    SELECT csc.calculation_id, csc.coordinate_index,
           csc.atom1_index, csc.atom2_index, csc.atom3_index, csc.atom4_index,
           csc.start_value, calc.public_ref AS calculation_ref
      FROM calc_scan_coordinate AS csc
      JOIN calculation AS calc ON calc.id = csc.calculation_id
     WHERE csc.coordinate_kind = 'dihedral'
       AND csc.value_unit = 'degree'
       AND csc.start_value IS NOT NULL
     ORDER BY csc.calculation_id, csc.coordinate_index
"""

_SCAN_POINTS_SQL = """
    SELECT point_index, geometry_id
      FROM calc_scan_point
     WHERE calculation_id = :cid
     ORDER BY point_index
"""

_COORDINATE_VALUES_SQL = """
    SELECT point_index, coordinate_value, value_unit
      FROM calc_scan_point_coordinate_value
     WHERE calculation_id = :cid AND coordinate_index = :cidx
"""

_ATOM_COORDS_SQL = """
    SELECT geometry_id, atom_index, x, y, z
      FROM geometry_atom
     WHERE geometry_id = ANY(:gids) AND atom_index = ANY(:aidx)
"""

_PRECISION_SAMPLE_SQL = """
    SELECT ga.x, ga.y, ga.z
      FROM geometry_atom AS ga
      JOIN calc_scan_point AS sp ON sp.geometry_id = ga.geometry_id
     WHERE sp.calculation_id = :cid
"""

_UPDATE_VALUE_SQL = """
    UPDATE calc_scan_point_coordinate_value
       SET coordinate_value = :value
     WHERE calculation_id = :cid AND point_index = :pidx AND coordinate_index = :cidx
"""


def _series_points(
    bind: Connection, calculation_id: int, coordinate_index: int
) -> tuple[list[dict] | None, str | None]:
    """Every point of one series, or ``(None, reason)`` if coverage is incomplete.

    A missing coordinate value, a missing geometry, or a declared unit other
    than degree on any single point disqualifies the whole series -- the
    same "convert the series whole or not at all" rule the proof itself
    enforces.
    """
    points = bind.execute(text(_SCAN_POINTS_SQL), {"cid": calculation_id}).all()
    if not points:
        return None, "no scan points for this calculation"

    value_rows = {
        row.point_index: row
        for row in bind.execute(
            text(_COORDINATE_VALUES_SQL),
            {"cid": calculation_id, "cidx": coordinate_index},
        ).all()
    }

    result: list[dict] = []
    for point in points:
        value_row = value_rows.get(point.point_index)
        if value_row is None:
            return (
                None,
                f"point_index={point.point_index} has no coordinate_value for "
                f"coordinate_index={coordinate_index}",
            )
        if point.geometry_id is None:
            return None, f"point_index={point.point_index} has no geometry"
        if value_row.value_unit is not None and value_row.value_unit != "degree":
            return (
                None,
                f"point_index={point.point_index} declares value_unit="
                f"{value_row.value_unit!r}, not degree",
            )
        result.append(
            {
                "point_index": point.point_index,
                "geometry_id": point.geometry_id,
                "coordinate_value": value_row.coordinate_value,
            }
        )
    return result, None


def _fetch_atom_coords(
    bind: Connection, geometry_ids: list[int], atom_indices: tuple[int, int, int, int]
) -> dict[int, dict[int, Vector]]:
    if not geometry_ids:
        return {}
    rows = bind.execute(
        text(_ATOM_COORDS_SQL),
        {"gids": geometry_ids, "aidx": list(atom_indices)},
    ).all()
    result: dict[int, dict[int, Vector]] = {}
    for row in rows:
        result.setdefault(row.geometry_id, {})[row.atom_index] = (row.x, row.y, row.z)
    return result


def _fetch_precision_decimals(bind: Connection, calculation_id: int) -> int:
    """Deposit precision for one calculation's geometry, over every scan point
    it has -- not just the one series being evaluated, mirroring
    ``build_scan_coordinate_conformance_report``'s per-calculation basis."""
    rows = bind.execute(text(_PRECISION_SAMPLE_SQL), {"cid": calculation_id}).all()
    values = [component for row in rows for component in (row.x, row.y, row.z)]
    return _infer_precision_decimals(values)


def _series_proofs(
    bind: Connection,
    *,
    calculation_id: int,
    coordinate_index: int,
    atom_indices: tuple[int, int, int, int],
    precision_decimals: int,
) -> tuple[list[tuple[dict, _PointProof]] | None, str | None]:
    """Every point's stored value alongside its geometric proof, or ``None``
    with a reason if coverage is incomplete or any single point is
    unprovable -- either refuses the whole series."""
    points, reason = _series_points(bind, calculation_id, coordinate_index)
    if points is None:
        return None, reason

    geometry_ids = sorted({p["geometry_id"] for p in points})
    coords_by_geometry = _fetch_atom_coords(bind, geometry_ids, atom_indices)

    proofs: list[tuple[dict, _PointProof]] = []
    for point in points:
        coords = coords_by_geometry.get(point["geometry_id"], {})
        proof = _evaluate_point_proof(coords, atom_indices, precision_decimals, point["point_index"])
        if not proof.provable:
            return None, f"point_index={point['point_index']} not provable: {proof.reason}"
        proofs.append((point, proof))
    return proofs, None


def _all_within_tolerance(proofs: list[tuple[dict, _PointProof]], value_of) -> bool:
    for point, proof in proofs:
        residual = _wrap_deg(value_of(point) - proof.expected_deg)
        if abs(residual) > proof.tolerance_deg:
            return False
    return True


def _convert_legacy_series(bind: Connection) -> None:
    """The whole of ``upgrade()``: find every candidate series, and convert,
    skip, or refuse each one on its own geometric merits. See the module
    docstring's "The rule, per series"."""
    series_rows = bind.execute(text(_ELIGIBLE_SERIES_SQL)).all()
    precision_cache: dict[int, int] = {}
    n_converted = n_skipped_conforms = n_skipped_failed = 0

    for row in series_rows:
        cid = row.calculation_id
        cref = row.calculation_ref
        cidx = row.coordinate_index
        atom_indices = (row.atom1_index, row.atom2_index, row.atom3_index, row.atom4_index)
        start_value = row.start_value

        if any(a is None for a in atom_indices):
            logger.warning(
                "scan series calculation_id=%s calculation_ref=%s coordinate_index=%s "
                "skipped: dihedral coordinate missing one or more atom indices",
                cid,
                cref,
                cidx,
            )
            continue

        if cid not in precision_cache:
            precision_cache[cid] = _fetch_precision_decimals(bind, cid)
        precision_decimals = precision_cache[cid]

        proofs, reason = _series_proofs(
            bind,
            calculation_id=cid,
            coordinate_index=cidx,
            atom_indices=atom_indices,
            precision_decimals=precision_decimals,
        )
        if proofs is None:
            logger.warning(
                "scan series calculation_id=%s calculation_ref=%s coordinate_index=%s "
                "left untouched: %s",
                cid,
                cref,
                cidx,
                reason,
            )
            n_skipped_failed += 1
            continue

        outcome = _apply_upgrade(bind, cid, cref, cidx, start_value, proofs)
        if outcome == "converted":
            n_converted += 1
        elif outcome == "already_conforms":
            n_skipped_conforms += 1
        else:
            n_skipped_failed += 1

    logger.info(
        "scan dihedral axis upgrade complete: %d series converted, %d already "
        "conforming (skipped), %d failed proof (skipped)",
        n_converted,
        n_skipped_conforms,
        n_skipped_failed,
    )


def _apply_upgrade(
    bind: Connection,
    calculation_id: int,
    calculation_ref: str,
    coordinate_index: int,
    start_value: float,
    proofs: list[tuple[dict, _PointProof]],
) -> str:
    already_conforms = _all_within_tolerance(proofs, lambda p: p["coordinate_value"])
    if already_conforms:
        logger.info(
            "scan series calculation_id=%s calculation_ref=%s coordinate_index=%s "
            "already conforms to ADR 0020 as stored (start_value=%s is within a turn "
            "of a multiple of 360); skipped, not converted",
            calculation_id,
            calculation_ref,
            coordinate_index,
            start_value,
        )
        return "already_conforms"

    shifted_conforms = _all_within_tolerance(
        proofs, lambda p: start_value + p["coordinate_value"]
    )
    if not shifted_conforms:
        logger.warning(
            "scan series calculation_id=%s calculation_ref=%s coordinate_index=%s "
            "left untouched: geometry does not confirm start_value + coordinate_value "
            "for every point (%d points checked)",
            calculation_id,
            calculation_ref,
            coordinate_index,
            len(proofs),
        )
        return "failed_proof"

    updates = [
        {
            "cid": calculation_id,
            "pidx": point["point_index"],
            "cidx": coordinate_index,
            "value": start_value + point["coordinate_value"],
        }
        for point, _ in proofs
    ]
    bind.execute(text(_UPDATE_VALUE_SQL), updates)

    # The reversal record. downgrade() cannot reconstruct this on its own
    # (see "downgrade() is one-way, by design" in the module docstring) --
    # this line, plus the literal UPDATE it hands the operator, is the only
    # place this migration's change to these rows is recorded anywhere.
    # Named with a stable, grep-able tag rather than folded into the prose
    # line above, so an operator can pull every reversal out of a migration
    # log with one grep regardless of what else that run printed.
    logger.warning(
        "SCAN_AXIS_REVERSAL_RECORD calculation_id=%s calculation_ref=%s "
        "coordinate_index=%s start_value=%r points_converted=%d -- reverse by hand "
        "with: UPDATE calc_scan_point_coordinate_value SET coordinate_value = "
        "coordinate_value - (%r) WHERE calculation_id = %s AND coordinate_index = %s;",
        calculation_id,
        calculation_ref,
        coordinate_index,
        start_value,
        len(updates),
        start_value,
        calculation_id,
        coordinate_index,
    )
    logger.info(
        "scan series calculation_id=%s calculation_ref=%s coordinate_index=%s "
        "converted: coordinate_value := start_value(%s) + coordinate_value, for %d "
        "points",
        calculation_id,
        calculation_ref,
        coordinate_index,
        start_value,
        len(updates),
    )
    return "converted"


def upgrade() -> None:
    _convert_legacy_series(op.get_bind())


def downgrade() -> None:
    """Refuses. See "downgrade() is one-way, by design" in the module
    docstring for the full argument; this is the short version the log
    carries at the point someone actually runs ``alembic downgrade``.

    No writes, and no reads either -- there is nothing this function could
    look up that would change its answer. The decision not to act does not
    depend on the state of the database, only on the fact that a converted
    row and a correctly-deposited row are indistinguishable once converted,
    which is true regardless of which rows happen to be which today.
    """
    logger.error(
        "a4f7c2e9d651 cannot be reversed automatically and makes no changes. "
        "After a successful conversion, a row this migration changed and a row "
        "deposited correctly under ADR 0020 at an ordinary start_value are "
        "identical in every stored column -- both have their first point equal to "
        "start_value, and both agree with their own geometry -- so there is no "
        "query that tells them apart, and an automatic reversal would either miss "
        "real conversions or corrupt correct deposits. To reverse by hand: find "
        "this revision's 'SCAN_AXIS_REVERSAL_RECORD' lines in the upgrade log (one "
        "per converted series, naming calculation_id, calculation_ref, "
        "coordinate_index, start_value, and the literal UPDATE to undo it), and run "
        "only those UPDATEs, scoped to exactly the (calculation_id, "
        "coordinate_index) pairs they name."
    )
