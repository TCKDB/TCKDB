"""Persist a deposit's warnings beside the records they are about, and read them back.

``UploadWarning`` is what a depositor is told when a deposit is accepted. This
module stores each one (``deposit_warning``) against a record the deposit created,
so a later reader of that record sees it too.

How a warning finds its record
------------------------------
A warning is born from a payload position (``field``) and carries no record id.
The workflows that write the rows know which payload object became which row, and
say so while they write (all of it in-memory, on the ``Session``, consumed when the
deposit's warnings are stored):

* :func:`note_deposit_targets` -- every record the deposit created or reused
  (``apply_review_policy`` calls it);
* :func:`note_anchor` -- a payload path prefix to the record built from it
  (``kinetics[0]`` to that kinetics row, ``species['h'].thermo`` to that thermo
  row). Optionally also names a *scope* the record is a result of;
* :func:`note_calculation_keys` -- bundle-local calculation keys to calculations;
* :func:`note_warning_subject` -- a producer that iterates over rows and so knows
  exactly which row a warning is about says so (matched by the warning's content,
  consumed in emission order, so it survives the job result's JSON round trip).

Where a warning lands, in order, and **never** on a guess:

1. a producer-stated subject;
2. a calculation named by a bundle-local key in its ``field``;
3. the record whose noted path is the longest prefix of its ``field``;
4. an *identity-subject* warning (about a shared species, reaction, literature or
   ref row, which must never carry a deposit's warning): the deposit's result
   record for that scope when there is exactly one (the scope is the longest noted
   scope prefix of its ``field``; with none noted, the whole deposit);
5. the submission alone.

A warning that is not identity-subject and still reaches step 5 could not be
placed. It is stored on the submission, logged, and the deposit then writes **no
marker rows**, so none of its records reads a false "recorded, nothing fired":
they read ``not_recorded``. The same holds when an identity-subject warning has no
single result record.

Every checked record otherwise gets one marker row (``code IS NULL``) so a read can
tell "checked, nothing fired" from "never checked".

Writing is isolated. The rows describe the deposit; they get no vote on the
science. They are written inside a SAVEPOINT, and a failure rolls that back, is
logged, and leaves no rows and no markers at all.

Warnings never change values, order lists or gate selection; nothing here is read by
a selector.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning

logger = logging.getLogger(__name__)

#: Bumped when the set of warnings a deposit is judged by changes.
CHECK_VERSION = 1

_TARGETS_KEY = "deposit_warning_targets"
_CALC_KEYS_KEY = "deposit_warning_calculation_keys"
_ANCHORS_KEY = "deposit_warning_anchors"
_SUBJECTS_KEY = "deposit_warning_subjects"
_NOTED_IDS_KEY = "deposit_warning_noted_ids"
_NOTE_KEYS = (_TARGETS_KEY, _CALC_KEYS_KEY, _ANCHORS_KEY, _SUBJECTS_KEY, _NOTED_IDS_KEY)

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

#: ``scientific_check_register`` entry per code. ``tests/api/test_deposit_warnings_on_read.py`` pins this against
#: the generated register, so it cannot drift. Codes without an entry are stored with NULL.
REGISTER_ENTRY: dict[str, int] = {
    "charge_mismatch": 12,
    "multiplicity_mismatch": 12,
    "n_imag_contradicts_minimum": 16,
    "n_imag_higher_order_saddle": 16,
    "n_imag_suggests_transition_state": 16,
    "minimum_imaginary_modes_below_tau": 17,
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

#: Codes whose subject is a shared, deduplicated identity row (a species, a reaction, a literature entry, a ref
#: fragment, an energy-correction scheme). They are never attached to that row; they take the deposit's single result
#: record for their scope, or the submission.
_IDENTITY_SUBJECT_PREFIXES = (
    "species_entry_stereo_",
    "term_symbol_",
    "electronic_state_contradicts_method",
    "atomic_electronic_",
    "missing_atomic_",
    "reaction_reversible_twin",
    "reaction_family_not_applied",
    "literature_",
    "software_release_version_is_composite",
    "software_release_name_looks_wrong",
    "level_of_theory_method_names_correction_table",
    "missing_energy_correction_scheme_software",
    "ambiguous_energy_correction_scheme_without_literature",
    "composite_delta_prefer_scheme_terms",
)

_CALC_KEY_IN_FIELD = re.compile(r"calculations\['([^']+)'\]")


def is_identity_subject(warning: UploadWarning) -> bool:
    """Whether ``warning`` is about a shared identity row rather than a result of this deposit."""
    if warning.code.startswith(_IDENTITY_SUBJECT_PREFIXES):
        return True
    # A provenance gap on a correction scheme or a literature fragment names the fragment, not a result.
    return warning.code == "missing_literature_provenance" and warning.field.startswith("scheme.")


@dataclass(frozen=True)
class _Target:
    record_type: RT
    record_id: int


# ---------------------------------------------------------------------------
# Notes a workflow leaves on the session while it writes
# ---------------------------------------------------------------------------


def reset_deposit_notes(session: Session) -> None:
    """Forget every note (a worker calls this before each attempt, so a failed attempt's notes cannot leak)."""
    for key in _NOTE_KEYS:
        session.info.pop(key, None)


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


def note_anchor(
    session: Session,
    path: str,
    record_type: RT,
    record_id: int,
    *,
    scope: str | None = None,
    fan_out: bool = False,
) -> None:
    """A warning whose ``field`` starts with ``path`` is about this record.

    ``path`` matches a whole path segment (``kinetics[0]`` matches ``kinetics[0].reaction`` but not ``kinetics[01]``).
    ``scope``, when given, also makes the record a result of that payload scope (``species['h']``), which is where an
    identity-subject warning inside the scope lands when this is the scope's only result.

    Several records noted at one path are one declaration that the path is about all of them (``fan_out=True``: a
    bundle-root provenance field that the workflow wrote onto every kinetics row): the warning is stored on each. A
    fan-out path never takes an identity-subject warning.
    """
    anchors: list[tuple[str, RT, int, str | None, bool]] = session.info.setdefault(_ANCHORS_KEY, [])
    anchors.append((path, record_type, record_id, scope, fan_out))


def _first_note(session: Session, warning: UploadWarning) -> bool:
    """Whether ``warning`` (this very object) has not been given a subject yet; the first producer to say wins."""
    seen: set[int] = session.info.setdefault(_NOTED_IDS_KEY, set())
    if id(warning) in seen:
        return False
    seen.add(id(warning))
    return True


def note_warning_subject(session: Session, warning: UploadWarning, record_type: RT, record_id: int) -> None:
    """The producer of ``warning`` knows which row it is about (matched by content, consumed in emission order)."""
    if not _first_note(session, warning):
        return
    subjects: dict[tuple[str, str, str], list[_Target | str]] = session.info.setdefault(_SUBJECTS_KEY, {})
    subjects.setdefault((warning.code, warning.field, warning.message), []).append(_Target(record_type, record_id))


def note_warning_scope(session: Session, warning: UploadWarning, scope: str) -> None:
    """``warning`` is an identity-subject warning born inside payload scope ``scope`` (``species['h']``)."""
    if not _first_note(session, warning):
        return
    subjects: dict[tuple[str, str, str], list[_Target | str]] = session.info.setdefault(_SUBJECTS_KEY, {})
    subjects.setdefault((warning.code, warning.field, warning.message), []).append(scope)


@contextmanager
def attribute_warnings(
    session: Session,
    sink: list[UploadWarning] | None,
    *,
    record: tuple[RT, int] | None = None,
    scope: str | None = None,
) -> Iterator[None]:
    """Every warning appended to ``sink`` inside the block is about ``record`` (or, identity-subject, about ``scope``)."""
    before = len(sink) if sink is not None else 0
    try:
        yield
    finally:
        if sink is not None:
            for warning in sink[before:]:
                if record is not None:
                    note_warning_subject(session, warning, *record)
                elif scope is not None:
                    note_warning_scope(session, warning, scope)


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _under(field: str, path: str) -> bool:
    return path == "" or field == path or field.startswith((f"{path}.", f"{path}["))


def _scope_result(scope: str | None, *, anchors, targets) -> list[_Target]:
    """The deposit's single non-calculation result record for ``scope`` (or for the whole deposit), else nothing."""
    if scope is not None:
        records = {(a[1], a[2]) for a in anchors if a[3] == scope and a[1] is not RT.calculation}
    else:
        records = {
            (t.record_type, t.record_id)
            for t in targets
            if t.record_type in ANCHOR_TYPES and t.record_type is not RT.calculation
        }
    return [_Target(*next(iter(records)))] if len(records) == 1 else []


def _place(
    warning: UploadWarning,
    *,
    targets: list[_Target],
    calc_keys: dict[str, int],
    anchors: list[tuple[str, RT, int, str | None, bool]],
    subjects: dict[tuple[str, str, str], list[_Target | str]],
) -> list[_Target]:
    """The exact records ``warning`` is about; empty means the submission."""
    identity = is_identity_subject(warning)
    stated = subjects.get((warning.code, warning.field, warning.message))
    if stated:
        item = stated.pop(0)
        if isinstance(item, str):
            return _scope_result(item, anchors=anchors, targets=targets)
        return [item]
    for key in _CALC_KEY_IN_FIELD.findall(warning.field):
        calc_id = calc_keys.get(key)
        if calc_id is not None:
            return [_Target(RT.calculation, calc_id)]
    usable = [a for a in anchors if _under(warning.field, a[0]) and not (identity and a[4])]
    if usable:
        best = max(len(a[0]) for a in usable)
        records = {(a[1], a[2]) for a in usable if len(a[0]) == best}
        if len(records) == 1 or any(a[4] for a in usable if len(a[0]) == best):
            return [_Target(*r) for r in sorted(records, key=lambda r: (r[0].value, r[1]))]
    if identity:
        scoped = [a for a in anchors if a[3] is not None and _under(warning.field, a[3])]
        scope = max((a[3] for a in scoped), key=len) if scoped else None
        return _scope_result(scope, anchors=anchors, targets=targets)
    return []


def _place_all(warnings, *, targets, calc_keys, anchors, subjects) -> list[list[_Target]]:
    return [
        _place(w, targets=targets, calc_keys=calc_keys, anchors=anchors, subjects=subjects) for w in warnings
    ]


# ---------------------------------------------------------------------------
# Content hash and JSON-safe context
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """``value`` with no NaN, Infinity or non-JSON type left in it (those become strings)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return [_json_safe(v) for v in items]
    return str(value)


def _clean(text: str) -> str:
    """PostgreSQL text cannot hold a NUL character."""
    return text.replace("\x00", "")


def content_hash(code: str, field: str, message: str, context: dict[str, Any] | None) -> str:
    """SHA-256 over the warning's own content: only truly identical warnings share one."""
    payload = json.dumps([code, field, message, context], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row(submission_id: int | None, target: _Target | None, warning: UploadWarning | None) -> dict[str, Any]:
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
        "content_hash": None,
    }
    if warning is not None:
        context = _json_safe(getattr(warning, "context", None))
        code, field, message = _clean(warning.code), _clean(warning.field), _clean(warning.message)
        base.update(
            code=code,
            register_entry=REGISTER_ENTRY.get(code),
            field=field,
            message=message,
            context=context,
            content_hash=content_hash(code, field, message, context),
        )
    return base


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


def store_deposit_warnings(
    session: Session,
    submission_id: int | None,
    warnings: Iterable[UploadWarning],
    *,
    targets: Iterable[Any] | None = None,
    mark_checked: bool = True,
) -> None:
    """Write ``warnings`` (and one marker per checked record) for ``submission_id``; never raises.

    ``targets`` defaults to what the workflow noted via :func:`note_deposit_targets`; the artifact route passes its
    calculation explicitly, with ``mark_checked=False`` (an artifact check is not a re-check of the deposit) and, when
    the calculation has no live submission, ``submission_id=None`` (every row then names its record). With exactly one
    explicit target every warning is about it.

    A warning that cannot be placed lands on the submission and suppresses every marker of this deposit. The notes
    are consumed whatever happens, so the next deposit on this session starts clean.
    """
    warnings = list(warnings)
    noted: dict[int, list[_Target]] = session.info.get(_TARGETS_KEY, {})
    if targets is None:
        target_list = list(noted.get(submission_id, ())) if submission_id is not None else []
    else:
        target_list = [_Target(t.record_type, t.record_id) for t in targets]
    calc_keys = dict(session.info.get(_CALC_KEYS_KEY, {}))
    anchors = list(session.info.get(_ANCHORS_KEY, []))
    subjects = {k: list(v) for k, v in session.info.get(_SUBJECTS_KEY, {}).items()}
    if submission_id is not None:
        noted.pop(submission_id, None)
    if not noted:
        reset_deposit_notes(session)
    if submission_id is None and not target_list:
        return
    # Pending rows go out first, outside the savepoint: a failure of the deposit's own rows is the deposit's
    # failure to report, never ours to swallow.
    session.flush()
    try:
        if targets is not None and len(target_list) == 1:
            placements: list[list[_Target]] = [[target_list[0]] for _ in warnings]
        else:
            placements = _place_all(
                warnings, targets=target_list, calc_keys=calc_keys, anchors=anchors, subjects=subjects
            )
        on_submission = [w for w, p in zip(warnings, placements, strict=True) if not p]
        # An identity-subject warning with no single result record is on the submission by decision, and says nothing
        # about any one record. Any other warning that found no record could be about any of them: then no record of
        # this deposit may read "checked, nothing fired".
        unplaced = [w for w in on_submission if not is_identity_subject(w)]
        if submission_id is None and on_submission:
            return
        rows: list[dict[str, Any]] = []
        if mark_checked and not unplaced:
            rows.append(_row(submission_id, None, None))
            for t in target_list:
                if t.record_type in ANCHOR_TYPES:
                    rows.append(_row(submission_id, t, None))
        elif unplaced:
            logger.warning(
                "deposit warnings for submission %s could not be placed on a record (%s); they are stored on the "
                "submission and none of its records is marked checked",
                submission_id,
                ", ".join(sorted({f"{w.code}@{w.field}" for w in unplaced})),
            )
        for warning, placement in zip(warnings, placements, strict=True):
            for target in placement or [None]:
                rows.append(_row(submission_id, target, warning))
        if not rows:
            return
        with session.begin_nested():
            session.execute(
                pg_insert(DepositWarning).values(rows).on_conflict_do_nothing(constraint="uq_deposit_warning_identity")
            )
    except Exception:
        logger.exception(
            "deposit warnings for submission %s were not stored; the deposit stands and its records read not_recorded",
            submission_id,
        )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def deposit_warnings_for(
    session: Session, record_type: RT, record_ids: Iterable[int]
) -> dict[int, dict[str, Any]]:
    """``{record_id: {"status", "items"}}`` for ``record_ids`` (every id is present in the result).

    ``status`` describes the deposit-time check: ``recorded`` when a submission left a marker for the record,
    otherwise ``not_recorded`` (deposited before warnings were stored, or one whose deposit could not place every
    warning). ``items`` is every stored warning for the record, including any added later by an artifact upload, so a
    ``not_recorded`` record can still list items. No row ids are returned.
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


def deposit_record_warnings(session: Session, record_type: RT, id_to_ref: dict[int, str]) -> list[Any]:
    """The read envelope's ``record_warnings``: one ``RecordWarnings`` per record the response names, in the given order."""
    from app.schemas.reads.scientific_deposit_warnings import RecordWarnings

    stored = deposit_warnings_for(session, record_type, id_to_ref)
    return [RecordWarnings(record_ref=ref, **stored[rid]) for rid, ref in id_to_ref.items()]
