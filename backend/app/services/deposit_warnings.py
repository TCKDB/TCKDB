"""Persist a deposit's warnings beside the records they are about, and read them back.

``UploadWarning`` is what a depositor is told when a deposit is accepted. This
module stores each one (``deposit_warning``) against a record the deposit created,
so a later reader of that record sees it too.

How a warning finds its record
------------------------------
A warning is born from a payload position (``field``), before or while rows are
written, and carries no record id. Every upload workflow ends in
``apply_review_policy`` with the full set of records it created or reused and the
submission they belong to; that call notes the targets here (``note_deposit_targets``)
and the route or worker later calls :func:`persist_deposit_warnings` with the final
warning list. Workflows that hold a bundle-local calculation key map also note it
(``note_calculation_keys``), which is the payload-key to row map.

Where a warning lands, in order:

1. a calculation named by a bundle-local key in its ``field``;
2. for a calculation-subject code: the deposit's only calculation, if there is
   exactly one, otherwise the deposit's primary result record (never a guess among
   several calculations);
3. for a code with a natural subject (a transition-state entry, kinetics, statmech,
   network solve): the first such record of this deposit;
4. the deposit's primary result record;
5. the submission alone.

A warning about a shared identity row (species entry, reaction, literature, a ref
fragment) is never attached to that row: it takes 4 or 5.

Every checked record also gets one marker row (``code IS NULL``) so a read can tell
"checked, nothing fired" from "never checked". Warnings never change values, order
lists or gate selection; nothing here is read by a selector.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning

#: Bumped when the set of warnings a deposit is judged by changes.
CHECK_VERSION = 1

_TARGETS_KEY = "deposit_warning_targets"
_CALC_KEYS_KEY = "deposit_warning_calculation_keys"

#: Records a warning may be anchored to. Identity rows (species entry, reaction entry, ...) are not.
ANCHOR_TYPES = frozenset(
    {
        RT.calculation,
        RT.transition_state_entry,
        RT.kinetics,
        RT.thermo,
        RT.statmech,
        RT.transport,
        RT.network,
        RT.network_solve,
        RT.conformer_observation,
    }
)

#: ``scientific_check_register`` entry per code. ``tests/services/test_deposit_warnings.py`` pins this against
#: the generated register, so it cannot drift. Codes without an entry are stored with NULL.
REGISTER_ENTRY: dict[str, int] = {
    "charge_mismatch": 12,
    "multiplicity_mismatch": 12,
    "n_imag_contradicts_minimum": 16,
    "n_imag_higher_order_saddle": 16,
    "n_imag_suggests_transition_state": 16,
    "transition_state_extra_imaginary_modes_below_tau": 17,
    "transition_state_extra_imaginary_mode_above_tau": 17,
    "transition_state_extra_imaginary_modes_not_assessable": 17,
    "transition_state_imaginary_frequency_too_small": 18,
    "transition_state_missing_irc_evidence": 19,
    "freq_list_linear_mode_count_for_bent_geometry": 22,
    "freq_list_bent_mode_count_for_linear_geometry": 22,
    "transition_state_energy_ordering_mixed_levels": 23,
    "transition_state_energy_ordering_not_compared": 25,
    "network_state_energy_sum_not_compared": 25,
    "network_energy_zpe_source_absent": 25,
    "reaction_atom_map_absent": 32,
    "reaction_atom_map_participants_incomplete": 33,
    "reaction_atom_map_atoms_incomplete": 33,
    "reported_network_solve": 37,
    "network_wide_energy_transfer_scope": 38,
}

_CALCULATION_SUBJECT_PREFIXES = (
    "n_imag_",
    "transition_state_extra_imaginary",
    "transition_state_imaginary_frequency",
    "freq_list_",
    "freq_parser_",
    "hessian_not_stored",
    "charge_mismatch",
    "multiplicity_mismatch",
    "sp_energy_",
    "composite_",
    "named_composite_",
    "software_release_identity",
    "software_release_version_filled",
    "converged_opt_",
    "dependency_edge_",
    "calculation_conformer_anchor",
    "scf_stability_source_",
)

_SUBJECT_BY_CODE_PREFIX: tuple[tuple[tuple[str, ...], RT], ...] = (
    (("transition_state_missing_irc", "transition_state_energy_ordering", "reaction_atom_map_"), RT.transition_state_entry),
    (("missing_kinetics_", "missing_tunneling_", "energy_level_stored_without", "reaction_family_not_applied"), RT.kinetics),
    (("missing_statmech_", "statmech_frequency_scale_factor"), RT.statmech),
    (("network_state_", "network_energy_", "reported_network_solve", "network_wide_energy_transfer"), RT.network_solve),
)

_KEY_IN_FIELD = re.compile(r"\['([^']+)'\]")


@dataclass(frozen=True)
class _Target:
    record_type: RT
    record_id: int


def note_deposit_targets(session: Session, submission_id: int, targets: Iterable[Any]) -> None:
    """Remember the records a workflow created or reused for ``submission_id`` (called by ``apply_review_policy``)."""
    store: dict[int, list[_Target]] = session.info.setdefault(_TARGETS_KEY, {})
    bucket = store.setdefault(submission_id, [])
    seen = {(t.record_type, t.record_id) for t in bucket}
    for target in targets:
        key = (target.record_type, target.record_id)
        if key not in seen:
            seen.add(key)
            bucket.append(_Target(*key))


def note_calculation_keys(session: Session, key_to_id: dict[str, int]) -> None:
    """Remember a bundle-local calculation key to calculation id map (the payload-key to row map)."""
    session.info.setdefault(_CALC_KEYS_KEY, {}).update(key_to_id)


def _is_calculation_subject(code: str) -> bool:
    return code.startswith(_CALCULATION_SUBJECT_PREFIXES)


def _natural_subject(code: str) -> RT | None:
    for prefixes, record_type in _SUBJECT_BY_CODE_PREFIX:
        if code.startswith(prefixes):
            return record_type
    return None


def _resolve(warning: UploadWarning, targets: list[_Target], calc_keys: dict[str, int]) -> _Target | None:
    anchorable = [t for t in targets if t.record_type in ANCHOR_TYPES]
    calcs = [t for t in anchorable if t.record_type is RT.calculation]
    for key in _KEY_IN_FIELD.findall(warning.field):
        calc_id = calc_keys.get(key)
        if calc_id is not None:
            return _Target(RT.calculation, calc_id)
    non_calcs = [t for t in anchorable if t.record_type is not RT.calculation]
    primary = non_calcs[0] if non_calcs else (calcs[0] if calcs else None)
    if _is_calculation_subject(warning.code):
        return calcs[0] if len(calcs) == 1 else primary
    wanted = _natural_subject(warning.code)
    if wanted is not None:
        for target in anchorable:
            if target.record_type is wanted:
                return target
    return primary


def _insert(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    session.execute(
        pg_insert(DepositWarning).values(rows).on_conflict_do_nothing(constraint="uq_deposit_warning_identity")
    )


def persist_deposit_warnings(
    session: Session,
    submission_id: int | None,
    warnings: Iterable[UploadWarning],
    *,
    targets: Iterable[Any] | None = None,
    mark_checked: bool = True,
) -> None:
    """Write ``warnings`` (and one marker per checked record) for ``submission_id``.

    Idempotent per submission: the unique key drops a repeat. ``targets`` defaults to what the workflow noted via
    :func:`note_deposit_targets`; the artifact route passes its calculation explicitly and ``mark_checked=False``
    (an artifact check is not a re-check of the deposit). Notes are consumed, so a session reused by a worker
    cannot leak one job's targets into the next.
    """
    noted: dict[int, list[_Target]] = session.info.get(_TARGETS_KEY, {})
    calc_keys: dict[str, int] = session.info.get(_CALC_KEYS_KEY, {})
    if targets is None:
        target_list = list(noted.get(submission_id, ())) if submission_id is not None else []
    else:
        target_list = [_Target(t.record_type, t.record_id) for t in targets]
    # Consume the notes first: whatever happens below, the next job on this session starts clean.
    if submission_id is not None:
        noted.pop(submission_id, None)
    if not noted:
        session.info.pop(_TARGETS_KEY, None)
        session.info.pop(_CALC_KEYS_KEY, None)
    if submission_id is None:
        return
    rows: list[dict[str, Any]] = []
    if mark_checked:
        rows.append(_row(submission_id, None, None))
        for t in target_list:
            if t.record_type in ANCHOR_TYPES:
                rows.append(_row(submission_id, t, None))
    for warning in warnings:
        rows.append(_row(submission_id, _resolve(warning, target_list, calc_keys), warning))
    _insert(session, rows)


def _row(submission_id: int, target: _Target | None, warning: UploadWarning | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "submission_id": submission_id,
        "record_type": target.record_type if target else None,
        "record_id": target.record_id if target else None,
        "check_version": CHECK_VERSION,
        "code": None,
        "register_entry": None,
        "field": None,
        "message": None,
        "context": None,
    }
    if warning is not None:
        base.update(
            code=warning.code,
            register_entry=REGISTER_ENTRY.get(warning.code),
            field=warning.field,
            message=warning.message,
            context=getattr(warning, "context", None),
        )
    return base


def deposit_warnings_for(
    session: Session, record_type: RT, record_ids: Iterable[int]
) -> dict[int, dict[str, Any]]:
    """``{record_id: {"status", "items"}}`` for ``record_ids`` (every id is present in the result).

    ``recorded`` when a submission left a marker for the record; otherwise ``not_recorded`` (a record deposited
    before warnings were stored, or one whose only rows are later additions such as an artifact warning). Items are
    every stored warning, oldest first within a submission. No row ids are returned.
    """
    ids = sorted(set(record_ids))
    out: dict[int, dict[str, Any]] = {i: {"status": "not_recorded", "items": []} for i in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(DepositWarning)
        .where(DepositWarning.record_type == record_type, DepositWarning.record_id.in_(ids))
        .order_by(DepositWarning.id)
    ).all()
    for row in rows:
        entry = out[row.record_id]
        if row.code is None:
            entry["status"] = "recorded"
            continue
        entry["items"].append(
            {
                "code": row.code,
                "message": row.message,
                "field": row.field,
                "register_entry": row.register_entry,
                "context": row.context,
                "check_version": row.check_version,
            }
        )
    return out


def deposit_warnings_by_ref(session: Session, record_type: RT, id_to_ref: dict[int, str]) -> dict[str, Any]:
    """The read envelope's ``warnings_by_ref``: ``{public_ref: DepositWarningsFragment}`` for the records named."""
    from app.schemas.reads.scientific_deposit_warnings import DepositWarningsFragment

    stored = deposit_warnings_for(session, record_type, id_to_ref)
    return {ref: DepositWarningsFragment.model_validate(stored[rid]) for rid, ref in id_to_ref.items()}
