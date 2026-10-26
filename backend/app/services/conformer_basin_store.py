"""Database reads and writes behind conformer basin identity: which observations define a basin, where their
structures are, and how a group row records its own shape.

Defining members
----------------
Only a *converged optimised structure* may define or link a basin. An observation is defining when its anchored
primary calculation is an ``opt`` or a ``composite``, has a ``final`` output geometry, and does not say it failed to
converge (``converged IS NOT FALSE``: a depositor who did not state convergence is not accused of failing it).
Everything else (a single point, a scan point, a partial optimisation, a coarse pre-optimisation that a later
optimisation of the same observation started from) is still filed into a basin, but it never acts as a comparison
target, so it can never join two basins. Otherwise one scan point lying between two minima would merge them.

"Primary" is the same calculation the upload fingerprinted: among the calculations anchored to the observation, the
lowest-id one that carries a geometry, with a coarse parent of another anchored optimisation never counting first.

Why this lives apart from the matcher
-------------------------------------
:mod:`app.chemistry.basin_matching` and :mod:`app.chemistry.basin_linkage` are pure: coordinates in, answers out.
Everything that touches a table is here, shared by the live upload path (:mod:`app.services.conformer_resolution`)
and the recompute (:mod:`app.services.conformer_reassignment`), so the two read the same facts.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chemistry.basin_linkage import BasinShape, PairTable, basin_shape
from app.chemistry.basin_matching import BasinMatchParams

logger = logging.getLogger(__name__)

#: Calculation types whose converged result is a minimum geometry.
DEFINING_CALCULATION_TYPES = ("opt", "composite")


def is_defining_calculation(calculation_type: str, *, has_final_geometry: bool, converged: bool | None) -> bool:
    """Whether an observation anchored on this primary calculation defines a basin.

    ``calculation_type`` is the calculation type's value (``"opt"``). ``converged`` is the depositor's statement
    (``None`` when not stated), and only a stated failure disqualifies.
    """
    return calculation_type in DEFINING_CALCULATION_TYPES and has_final_geometry and converged is not False


@dataclass
class StoredObservation:
    id: int
    group_id: int
    created_by: int | None
    scheme_id: int | None
    fingerprint_json: dict | None
    mapped_coords: list | None


def _primary_rows(session: Session, observation_ids: Sequence[int]):
    """``(obs_id, geometry_id, defining)`` of each observation's primary calculation, where it has one."""
    if not observation_ids:
        return []
    return session.execute(
        text(
            """
            WITH anchored AS (
                SELECT c.id AS calc_id, c.conformer_observation_id AS obs_id
                  FROM calculation c
                 WHERE c.conformer_observation_id = ANY(:obs_ids)
            ), coarse_parents AS (
                SELECT DISTINCT d.parent_calculation_id AS calc_id
                  FROM calculation_dependency d
                  JOIN anchored child ON child.calc_id = d.child_calculation_id
                  JOIN anchored parent ON parent.calc_id = d.parent_calculation_id AND parent.obs_id = child.obs_id
                 WHERE d.dependency_role = 'optimized_from'
            ), carried AS (
                SELECT a.obs_id, a.calc_id, 0 AS source, o.output_order AS position, o.geometry_id
                  FROM anchored a
                  JOIN calculation_output_geometry o ON o.calculation_id = a.calc_id AND o.role = 'final'
                UNION ALL
                SELECT a.obs_id, a.calc_id, 1 AS source, i.input_order AS position, i.geometry_id
                  FROM anchored a
                  JOIN calculation_input_geometry i ON i.calculation_id = a.calc_id
            ), chosen AS (
                SELECT DISTINCT ON (obs_id) obs_id, calc_id, source, geometry_id
                  FROM carried
                 ORDER BY obs_id,
                          (calc_id IN (SELECT calc_id FROM coarse_parents)),
                          calc_id,
                          source,
                          position
            )
            SELECT ch.obs_id, ch.geometry_id,
                   (ch.source = 0
                    AND c.type IN ('opt', 'composite')
                    AND r.converged IS NOT FALSE) AS defining
              FROM chosen ch
              JOIN calculation c ON c.id = ch.calc_id
              LEFT JOIN calc_opt_result r ON r.calculation_id = c.id
            """
        ),
        {"obs_ids": list(observation_ids)},
    ).all()


def defining_observation_ids(session: Session, observation_ids: Sequence[int]) -> set[int]:
    """The observations among ``observation_ids`` that define a basin (see the module docstring)."""
    return {row.obs_id for row in _primary_rows(session, observation_ids) if row.defining}


def observation_geometry_atoms(
    session: Session, observation_ids: Sequence[int]
) -> dict[int, tuple[tuple[str, float, float, float], ...]]:
    """The geometry each observation was deposited with, as atom tuples, where it can be determined.

    This is the geometry the upload path fingerprinted. An upload attaches the geometry it was given to the
    observation's *primary* calculation (the first one created, so the lowest id): as that calculation's final
    output when it is an optimisation, otherwise as its input (a frequency or single point has no output unless
    it declares one). So: among the calculations anchored to the observation, take the lowest-id one that carries
    a geometry (a coarse parent of another anchored optimisation never counts first), and use its final output
    geometry if it has one, else its first input geometry. A later optimisation of the same observation is
    deliberately not used: it may be a different structure. An observation with no such calculation is
    undeterminable and is left alone by the caller. Coordinates are read from ``geometry_atom``, as for every
    stored geometry; the upload parsed the same numbers from ``xyz_text``.
    """
    rows = _primary_rows(session, observation_ids)
    geometry_of: dict[int, int] = {row.obs_id: row.geometry_id for row in rows}
    if not geometry_of:
        return {}
    atoms_by_geometry: dict[int, list[tuple[str, float, float, float]]] = defaultdict(list)
    for row in session.execute(
        text(
            "SELECT geometry_id, element, x, y, z FROM geometry_atom "
            "WHERE geometry_id = ANY(:ids) ORDER BY geometry_id, atom_index"
        ),
        {"ids": sorted(set(geometry_of.values()))},
    ):
        atoms_by_geometry[row.geometry_id].append((row.element.strip(), row.x, row.y, row.z))
    return {oid: tuple(atoms_by_geometry[gid]) for oid, gid in geometry_of.items() if atoms_by_geometry.get(gid)}


def load_entry_observations(session: Session, entry_id: int) -> list[StoredObservation]:
    """Every observation filed under a group of this species entry, oldest first."""
    return [
        StoredObservation(
            r.id, r.conformer_group_id, r.created_by, r.assignment_scheme_id, r.torsion_fingerprint_json, r.mapped_coords_json
        )
        for r in session.execute(
            text(
                "SELECT o.id, o.conformer_group_id, o.created_by, o.assignment_scheme_id, o.torsion_fingerprint_json, "
                "o.mapped_coords_json FROM conformer_observation o "
                "JOIN conformer_group g ON g.id = o.conformer_group_id "
                "WHERE g.species_entry_id = :e ORDER BY o.id"
            ),
            {"e": entry_id},
        )
    ]


def write_group_shape(
    session: Session,
    group_id: int,
    *,
    representative_observation_id: int | None,
    fingerprint_json: dict | None,
    coords: Sequence | None,
    shape: BasinShape | None,
) -> None:
    """Record a group's derived representative and shape. A group that has members is, by definition, not merged away."""
    session.execute(
        text(
            "UPDATE conformer_group SET representative_observation_id = :rep, "
            "representative_fingerprint_json = CAST(:fp AS jsonb), representative_coords_json = CAST(:coords AS jsonb), "
            "basin_is_clique = :clique, basin_span_deg = :span, merged_into_conformer_group_id = NULL WHERE id = :id"
        ),
        {
            "id": group_id,
            "rep": representative_observation_id,
            "fp": json.dumps(fingerprint_json) if fingerprint_json is not None else None,
            "coords": json.dumps([list(c) for c in coords]) if coords is not None else None,
            "clique": shape.is_clique if shape is not None else None,
            "span": shape.span_deg if shape is not None else None,
        },
    )


def stored_group_shape(session: Session, group_id: int):
    return session.execute(
        text(
            "SELECT representative_observation_id AS rep, representative_fingerprint_json AS fp, "
            "representative_coords_json AS coords, basin_is_clique AS clique, basin_span_deg AS span "
            "FROM conformer_group WHERE id = :id"
        ),
        {"id": group_id},
    ).first()


def refresh_group_shape(
    session: Session,
    group_id: int,
    *,
    smiles: str,
    params: BasinMatchParams,
    compute_missing: Callable[[list[int]], dict[int, tuple[dict | None, list | None]]],
    overrides: dict[int, tuple[bool, Sequence | None]] | None = None,
) -> BasinShape | None:
    """Re-derive a group's representative (medoid), clique flag and span from its members, and store them.

    ``compute_missing(observation_ids) -> {id: (fingerprint_json_or_None, coords)}`` supplies the structure of a
    member that has no stored ``mapped_coords_json`` (read from its geometry). ``overrides`` gives ``(defining,
    coords)`` for observations whose calculations are not anchored yet (the upload that has just been filed).

    A group with defining members is represented by their medoid. A group with none (a provisional basin of
    partly optimised structures) is represented by the first member filed, and carries no shape.
    """
    rows = session.execute(
        text(
            "SELECT id, torsion_fingerprint_json AS fp, mapped_coords_json AS coords "
            "FROM conformer_observation WHERE conformer_group_id = :g ORDER BY id"
        ),
        {"g": group_id},
    ).all()
    if not rows:
        return None
    overrides = overrides or {}
    ids = [r.id for r in rows]
    defining = defining_observation_ids(session, [i for i in ids if i not in overrides])
    for oid, (flag, _coords) in overrides.items():
        if oid in ids and flag:
            defining.add(oid)
    coords: dict[int, Sequence] = {}
    fingerprints: dict[int, dict | None] = {}
    missing: list[int] = []
    for r in rows:
        fingerprints[r.id] = r.fp
        if r.id in overrides and overrides[r.id][1] is not None:
            coords[r.id] = overrides[r.id][1]
        elif r.coords is not None:
            coords[r.id] = r.coords
        else:
            missing.append(r.id)
    if missing:
        for oid, (fp, structure) in compute_missing(missing).items():
            if structure is not None:
                coords[oid] = structure
                fingerprints.setdefault(oid, fp)
                if fingerprints[oid] is None:
                    fingerprints[oid] = fp
    defining_with_coords = sorted(i for i in defining if i in coords)
    if defining_with_coords:
        table = PairTable(smiles, params, {i: [tuple(c) for c in coords[i]] for i in defining_with_coords})
        shape = basin_shape(table, defining_with_coords)
        rep = shape.medoid_key
    else:
        shape = None
        rep = next((i for i in ids if i in coords), None)
        if rep is None:
            return None
    write_group_shape(
        session,
        group_id,
        representative_observation_id=rep,
        fingerprint_json=fingerprints.get(rep),
        coords=coords[rep],
        shape=shape,
    )
    if shape is not None and not shape.is_clique:
        logger.info("conformer group %s is a wide basin (span %.1f degrees)", group_id, shape.span_deg)
    return shape


# ---------------------------------------------------------------------------
# Merges that touch approved records
# ---------------------------------------------------------------------------


def merge_touches_approved(session: Session, group_ids: Sequence[int]) -> bool:
    """Whether moving the members and dependents of these groups would change an accepted (approved) record.

    Accepted records (those a reviewer approved at least once) change only through a declared repair, which only
    an owner-role job can make. They are: an observation filed under the group, a thermo whose target is the group,
    a statmech record carrying a basin declaration or ensemble member on it, and a conformer selection that an
    accepted kinetics record cites.
    """
    if not group_ids:
        return False
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM conformer_observation o
                      JOIN record_review r ON r.record_type = 'conformer_observation' AND r.record_id = o.id
                     WHERE o.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM thermo t
                      JOIN record_review r ON r.record_type = 'thermo' AND r.record_id = t.id
                     WHERE t.target_conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_declaration d
                      JOIN record_review r ON r.record_type = 'statmech' AND r.record_id = d.statmech_id
                     WHERE d.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_member m
                      JOIN statmech_model_declaration d ON d.id = m.declaration_id
                      JOIN record_review r ON r.record_type = 'statmech' AND r.record_id = d.statmech_id
                     WHERE m.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM conformer_selection s
                      JOIN kinetics_interpretation_assignment a ON a.conformer_selection_id = s.id
                      JOIN record_review r ON r.record_type = 'kinetics' AND r.record_id = a.kinetics_id
                     WHERE s.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                )
                """
            ),
            {"g": list(group_ids)},
        ).scalar()
    )


def record_pending_merge(
    session: Session, *, entry_id: int, kept: int, absorbed: int, observation_id: int, reason: str
) -> None:
    """Record a merge a live upload decided on but could not carry out (see ``ConformerGroupMergePending``)."""
    session.execute(
        text(
            "INSERT INTO conformer_group_merge_pending (species_entry_id, kept_conformer_group_id, "
            "absorbed_conformer_group_id, conformer_observation_id, reason) "
            "VALUES (:e, :k, :a, :o, :reason) ON CONFLICT ON CONSTRAINT uq_conformer_group_merge_pending_merge DO NOTHING"
        ),
        {"e": entry_id, "k": kept, "a": absorbed, "o": observation_id, "reason": reason},
    )


def entries_with_pending_merges(session: Session) -> list[int]:
    """Species entries that owe a merge: the input of the owner-role job that completes them."""
    return [
        r[0]
        for r in session.execute(
            text(
                "SELECT DISTINCT species_entry_id FROM conformer_group_merge_pending "
                "WHERE completed_at IS NULL ORDER BY species_entry_id"
            )
        )
    ]
