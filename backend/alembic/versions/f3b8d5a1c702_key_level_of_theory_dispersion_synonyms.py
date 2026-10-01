"""level_of_theory: key dispersion-column synonyms and folded dispersion (#630)

``lot_hash`` is taken over the identity keys of a level of theory's names.
``d0a7c3b91e4f`` (#627) joined D3(BJ) spellings inside the method and made the
dispersion column case-insensitive. Two splits were left, and this revision
closes them, re-keying the rows that already exist so the next upload of any
of them still finds it. Verbatim names are not touched.

* **Dispersion column synonyms.** ``gd3bj`` and ``d3(bj)`` key as ``d3bj``,
  ``gd3`` as ``d3zero`` and ``gd2`` as ``d2``, also when wrapped the way
  Gaussian's route writes them (``EmpiricalDispersion=GD3BJ``,
  ``EmpiricalDispersion=(GD3BJ)``, ``EmpiricalDispersion(GD3BJ)``). Bare
  ``d3`` stays its own key (ORCA ``D3`` is BJ-damped, Psi4 ``-d3`` is
  zero-damped). Citations: ``app/chemistry/dispersion_names.py``.
* **Folded dispersion.** A recognised trailing dispersion on a method
  (``b3lyp-d3bj``) moves into the dispersion key, so it keys as
  ``b3lyp`` + ``d3bj``, the same as a level stored that way. Only for the
  stems and suffixes in ``dispersion_names.py`` (``wb97x-d3bj`` and the like
  are separate functionals and are not split), and not when the column
  states a different dispersion.

Rows already in column form with a canonical dispersion (``b3lyp`` +
``d3bj``) keep their hash, so the holder of a group is usually the row that
was already stored that way.

What this revision writes
-------------------------
* No DDL.
* Data: ``level_of_theory.lot_hash``, and nothing else on any table.

  - A row whose key does not change keeps its hash.
  - A row whose key changes gets the new hash, the same in-place re-hash
    ``d0a7c3b91e4f`` did.
  - **Rows that were already duplicates** (``b3lyp`` + ``d3bj`` beside
    ``b3lyp`` + ``gd3bj`` and ``b3lyp-d3bj``) share a new hash, and
    ``lot_hash`` is unique, so only one of them can hold it. The holder is
    chosen as in ``d0a7c3b91e4f`` (see "Who holds a key"); the others keep
    their old hash and their calculations. This revision does not merge
    them: ``scripts/ops/merge_duplicate_levels_of_theory.py`` does, as a
    dry-run-first operator step, through the same ``level_of_theory_merge``
    alias table. The groups found are printed, by ``public_ref``.
  - **A row already merged** (it has a ``level_of_theory_merge`` row) is
    never chosen as a holder and never re-hashed.

Who holds a key
---------------
In each group of rows that now share one hash, exactly one row holds it. In
order:

1. the unmerged row that already holds the target hash;
2. the unmerged row whose current hash equals the hash its own *previous*
   formula (``d0a7c3b91e4f``'s) gives, so upgrade-then-downgrade is exact and
   the row the merge script chose as holder stays the holder;
3. the unmerged row with the smallest id.

A hash **held by a merged row** is occupied: if a target is occupied that
way, that group's update is skipped and reported (both directions) rather
than violating ``uq_level_of_theory_lot_hash``.

Why the merge is not in this revision
-------------------------------------
Same reason as ``38b06819f099`` and ``d0a7c3b91e4f``: merging repoints
``calculation.lot_id``, ``calculation`` is an accepted-science root, and
whether a duplicate carries approved science is a fact about one deployment's
data that a migration cannot stop and ask about.

``public_ref`` is kept
----------------------
``public_ref`` is minted from ``lot_hash`` once, at insert, and stored. This
revision updates ``lot_hash`` only.

Updates go through a unique placeholder
---------------------------------------
Both directions first move every row whose hash changes to a per-row
placeholder, then to its target, so no order can trip the unique constraint.

Rules frozen here
-----------------
``_method_identity_key`` (with ``_NAME_ALIASES`` and ``_SUFFIX_RULES``),
``_component_identity_key``, ``_dispersion_identity_key`` (with
``_DISPERSION_RULES``), ``_level_identity_keys`` (with ``_FOLDED_PATTERN``)
and ``_basis_identity_key`` are a copy of the application rules as of this
revision. A migration must describe what it ran, so it does not import
application code that may change later; tests hold the two in agreement for
as long as the rules are the same. ``assign_hashes`` is a copy of the one in
``d0a7c3b91e4f``, and a test holds the two in agreement too.

Downgrade
---------
Re-hashes rows with ``d0a7c3b91e4f``'s formula. Every row whose stored hash
came from this revision's formula gets its previous hash back exactly. Rows
whose previous hash is shared cannot all hold it: the holder is chosen by the
rules above and the others keep their current, unique hash. Upgrade then
downgrade restores every hash exactly, with or without merges in between
(``tests/services/test_level_of_theory_dispersion_rekey_plan.py`` checks this
over randomised lifecycles).

Revision ID: f3b8d5a1c702
Revises: a7d3f1c95e28
Create Date: 2026-10-01
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f3b8d5a1c702"
down_revision: Union[str, Sequence[str], None] = "a7d3f1c95e28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HASH_FIELDS = (
    "method", "basis", "aux_basis", "cabs_basis",
    "dispersion", "solvent", "solvent_model", "keywords",
)
_BASIS_FIELDS = ("basis", "aux_basis", "cabs_basis")

#: Frozen copy of ``app.chemistry.basis_set_names.HYPHEN_RULES``.
_HYPHEN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![^-])def2(?=[a-z])"), "def2-"),
    (re.compile(r"(?<![^-])ccp(?=(?:w?c)?v)"), "cc-p"),
)

#: Frozen copy of ``app.chemistry.method_names.NAME_ALIASES`` (alias -> key).
_NAME_ALIASES = {
    "wb97x-d": "wb97xd",
    "m06-2x": "m062x",
}

#: Frozen copy of ``app.chemistry.method_names.SUFFIX_RULES``.
_SUFFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(.+)-d3\(bj\)$", re.DOTALL), r"\1-d3bj"),
    (re.compile(r"^(.+)-gd3bj$", re.DOTALL), r"\1-d3bj"),
)

#: Frozen copy of ``app.chemistry.dispersion_names.DISPERSION_RULES``.
_DISPERSION_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), r)
    for p, r in (
        (
            r"^(?:gd3bj|empiricaldispersion\s*(?:=\s*gd3bj|=\s*\(\s*gd3bj\s*\)|\(\s*gd3bj\s*\)))$",
            "d3bj",
        ),
        (
            r"^(?:d3\(bj\)|empiricaldispersion\s*(?:=\s*d3\(bj\)|=\s*\(\s*d3\(bj\)\s*\)|\(\s*d3\(bj\)\s*\)))$",
            "d3bj",
        ),
        (
            r"^(?:gd3|empiricaldispersion\s*(?:=\s*gd3|=\s*\(\s*gd3\s*\)|\(\s*gd3\s*\)))$",
            "d3zero",
        ),
        (
            r"^(?:gd2|empiricaldispersion\s*(?:=\s*gd2|=\s*\(\s*gd2\s*\)|\(\s*gd2\s*\)))$",
            "d2",
        ),
    )
)

#: Frozen copy of ``app.chemistry.dispersion_names.FOLDED_PATTERN``.
_FOLDED_PATTERN = re.compile(
    r"^(cam\-b3lyp|b2plyp|revpbe|b3pw91|m06\-2x|b3lyp|tpss0|bhlyp|m062x|pbe0|tpss|bp86|blyp|pbe|hf)"
    r"-(d3bj|d3zero|d2)$"
)

_SELECT_ROWS = sa.text(
    "SELECT id, public_ref, lot_hash, method, basis, aux_basis, cabs_basis, "
    "dispersion, solvent, solvent_model, keywords, spin_treatment "
    "FROM level_of_theory ORDER BY id"
)
_SELECT_MERGED = sa.text("SELECT merged_lot_id FROM level_of_theory_merge")
_UPDATE_HASH = sa.text("UPDATE level_of_theory SET lot_hash = :h WHERE id = :id")


def _basis_identity_key(name: str | None) -> str | None:
    if name is None:
        return None
    key = name.strip().lower()
    if not key:
        return None
    for pattern, replacement in _HYPHEN_RULES:
        key = pattern.sub(replacement, key)
    return key


def _method_identity_key(name: str) -> str:
    key = name.strip().lower()
    if key in _NAME_ALIASES:
        return _NAME_ALIASES[key]
    for pattern, replacement in _SUFFIX_RULES:
        key = pattern.sub(replacement, key)
    return key


def _component_identity_key(name: str | None) -> str | None:
    if name is None:
        return None
    key = name.strip().lower()
    return key or None


def _dispersion_identity_key(name: str | None) -> str | None:
    key = _component_identity_key(name)
    if key is None:
        return None
    for pattern, replacement in _DISPERSION_RULES:
        key = pattern.sub(replacement, key)
    return key


def _level_identity_keys(method: str, dispersion: str | None) -> tuple[str, str | None]:
    method_key = _method_identity_key(method)
    dispersion_key = _dispersion_identity_key(dispersion)
    hit = _FOLDED_PATTERN.match(method_key)
    if hit is not None and dispersion_key in (None, hit.group(2)):
        return _method_identity_key(hit.group(1)), hit.group(2)
    return method_key, dispersion_key


def _lot_hash(row, *, split: bool) -> str:
    """The application's hash formula.

    ``split=False`` is the formula as ``d0a7c3b91e4f`` left it (method
    aliases, dispersion by case only, a folded dispersion kept in the method).
    ``split=True`` is this revision's.
    """
    mapping = row._mapping
    payload = {field: mapping[field] for field in _HASH_FIELDS}
    for field in _BASIS_FIELDS:
        payload[field] = _basis_identity_key(payload[field])
    for field in ("solvent", "solvent_model"):
        payload[field] = _component_identity_key(payload[field])
    if split:
        payload["method"], payload["dispersion"] = _level_identity_keys(
            payload["method"], payload["dispersion"]
        )
    else:
        payload["method"] = _method_identity_key(payload["method"])
        payload["dispersion"] = _component_identity_key(payload["dispersion"])
    payload["spin_treatment"] = mapping["spin_treatment"] or "unknown"
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def assign_hashes(
    rows, target_of, prior_target_of, merged_ids: set[int]
) -> tuple[dict[int, str], list[tuple[object, list]], list[object]]:
    """Give each distinct target hash to exactly one unmerged row.

    Rows are grouped by ``target_of(row)``. The holder is chosen as described
    in the module docstring: the row already holding the target, else the row
    whose hash is the one ``prior_target_of(row)`` gives, else the smallest
    id. A merged row is an alias: never the holder, never re-hashed, and its
    hash counts as occupied.

    :returns: ``(updates, groups, blocked)``: row id -> new hash for rows
        whose hash changes; ``(holder, others)`` for every group with two or
        more unmerged members; and the holders whose update was skipped
        because a merged row holds their target.
    """
    by_target: dict[str, list] = defaultdict(list)
    merged_hashes: set[str] = set()
    for row in rows:
        if row._mapping["id"] in merged_ids:
            merged_hashes.add(row._mapping["lot_hash"])
        else:
            by_target[target_of(row)].append(row)

    updates: dict[int, str] = {}
    groups: list[tuple[object, list]] = []
    blocked: list[object] = []
    for target, members in by_target.items():
        by_id = sorted(members, key=lambda m: m._mapping["id"])
        holder = next(
            (m for m in by_id if m._mapping["lot_hash"] == target),
            next(
                (m for m in by_id if m._mapping["lot_hash"] == prior_target_of(m)),
                by_id[0],
            ),
        )
        if holder._mapping["lot_hash"] != target:
            if target in merged_hashes:
                blocked.append(holder)
            else:
                updates[holder._mapping["id"]] = target
        others = [m for m in members if m is not holder]
        if others:
            groups.append((holder, others))
    return updates, groups, blocked


def plan_rekey(rows, merged_ids: set[int]):
    """Upgrade plan, with duplicate groups named by ``public_ref``."""
    updates, groups, blocked = assign_hashes(
        rows,
        lambda row: _lot_hash(row, split=True),
        lambda row: _lot_hash(row, split=False),
        merged_ids,
    )
    named = [
        (holder._mapping["public_ref"], [m._mapping["public_ref"] for m in others])
        for holder, others in groups
    ]
    return updates, named, [h._mapping["public_ref"] for h in blocked]


def plan_unkey(rows, merged_ids: set[int]):
    """Downgrade plan: the pre-revision formula, mirror of :func:`plan_rekey`."""
    updates, groups, blocked = assign_hashes(
        rows,
        lambda row: _lot_hash(row, split=False),
        lambda row: _lot_hash(row, split=True),
        merged_ids,
    )
    return updates, groups, [h._mapping["public_ref"] for h in blocked]


def _apply(bind, updates: dict[int, str]) -> None:
    """Placeholder first, then target, so no order can trip the constraint."""
    for row_id in updates:
        placeholder = hashlib.sha256(f"f3b8d5a1c702:placeholder:{row_id}".encode()).hexdigest()
        bind.execute(_UPDATE_HASH, {"h": placeholder, "id": row_id})
    for row_id, new_hash in updates.items():
        bind.execute(_UPDATE_HASH, {"h": new_hash, "id": row_id})


def _merged_ids(bind) -> set[int]:
    return {row[0] for row in bind.execute(_SELECT_MERGED)}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).all()
    updates, groups, blocked = plan_rekey(rows, _merged_ids(bind))
    _apply(bind, updates)
    print(
        f"level_of_theory dispersion re-key: {len(updates)} row(s) re-hashed, "
        f"{len(groups)} duplicate group(s) left for "
        "scripts/ops/merge_duplicate_levels_of_theory.py."
    )
    for holder, others in groups:
        print(f"  {holder} holds the key; also spelled as {', '.join(others)}")
    for ref in blocked:
        print(f"  {ref} NOT re-hashed: a merged row already holds its new hash.")


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).all()
    updates, groups, blocked = plan_unkey(rows, _merged_ids(bind))
    _apply(bind, updates)
    if groups:
        print(
            f"level_of_theory downgrade: {len(groups)} group(s) of rows share one "
            "pre-#630 hash (rows this revision moved onto a shared key, ); one row per group took it and "
            "the others kept their current hash."
        )
    for ref in blocked:
        print(f"  {ref} NOT re-hashed: a merged row already holds its pre-#630 hash.")
