"""level_of_theory: hash curated method aliases and component case (#618, #602)

``lot_hash`` is taken over the identity keys of a level of theory's names.
``c8424fe82997`` keyed the method by case. This revision moves two more
things into the key, and re-keys the rows that already exist so the next
upload of any of them still finds it. Verbatim names are not touched.

* **#618, curated method aliases.** ``method_identity_key`` now also applies a
  small table of spellings that name one functional in every program, each
  with its citations (``app/chemistry/method_names.py``): ``wb97x-d`` is keyed
  as ``wb97xd``, ``m06-2x`` as ``m062x``, and a trailing ``-d3(bj)`` or
  ``-gd3bj`` as ``-d3bj``. Nothing program-scoped is in the table, because the
  hash cannot see a program; Gaussian ``wB97XD`` and ORCA ``wB97X-D3`` stay two
  keys.
* **#602, component case.** ``dispersion``, ``solvent`` and ``solvent_model``
  are hashed through ``component_identity_key`` (stripped and lower-cased),
  so ``D3BJ`` and ``d3bj``, ``Water`` and ``water``, ``SMD`` and ``smd`` are
  one. ``keywords`` is free-form text and stays verbatim.

What this revision writes
-------------------------
* No DDL.
* Data: ``level_of_theory.lot_hash``, and nothing else on any table.

  - A row whose names are already in key form keeps its hash unchanged.
  - A row whose names change under the key gets the new hash, the same
    in-place re-hash ``38b06819f099`` and ``c8424fe82997`` did.
  - **Rows that were already duplicates** (``wb97x-d`` beside ``wb97xd``,
    ``D3BJ`` beside ``d3bj``) share a new hash, and ``lot_hash`` is unique, so
    only one of them can hold it. The holder is chosen exactly as in
    ``c8424fe82997`` (see "Who holds a key" below); the others keep their old
    hash and their calculations. New uploads resolve to the holder.
    This revision does not merge them: ``scripts/ops/merge_duplicate_levels_of_theory.py``
    does, as a dry-run-first operator step, through the same
    ``level_of_theory_merge`` alias table. The groups found are printed, by
    ``public_ref``, so the operator knows to run it.
  - **A row already merged** (it has a ``level_of_theory_merge`` row) is never
    chosen as a holder and never re-hashed: it is an alias, and the hash it
    kept resolves nothing.

Who holds a key
---------------
In each group of rows that now share one hash, exactly one row holds it. In
order:

1. the unmerged row that already holds the target hash;
2. the unmerged row whose current hash equals the hash its own *previous*
   formula gives (the row that held the key of the formula this revision
   replaces; on downgrade, the row that holds this revision's key). Choosing
   it makes upgrade-then-downgrade exact and keeps the row the merge script
   chose as holder as the holder here;
3. the unmerged row with the smallest id.

A hash **held by a merged row** is occupied: if a target is occupied that way,
that group's update is skipped and reported (both directions) rather than
violating ``uq_level_of_theory_lot_hash``.

Why the merge is not in this revision
-------------------------------------
Same reason as ``38b06819f099``: merging repoints ``calculation.lot_id``,
``calculation`` is an accepted-science root, and whether a duplicate carries
approved science is a fact about one deployment's data that a migration cannot
stop and ask about.

``public_ref`` is kept
----------------------
As in ``38b06819f099``: ``public_ref`` is minted from ``lot_hash`` once, at
insert, and stored. This revision updates ``lot_hash`` only.

Updates go through a unique placeholder
---------------------------------------
Both directions first move every row whose hash changes to a per-row
placeholder, then to its target, so no order can trip the unique constraint.

Rules frozen here
-----------------
``_HYPHEN_RULES``, ``_basis_identity_key``, ``_method_case_key``,
``_method_identity_key`` (with ``_NAME_ALIASES`` and ``_SUFFIX_RULES``) and
``_component_identity_key`` are a copy of the application rules as of this
revision. A migration must describe what it ran, so it does not import
application code that may change later; a test holds the two in agreement for
as long as the rules are the same. ``assign_hashes`` is a copy of the one in
``c8424fe82997``, and a test holds the two in agreement too.

Downgrade
---------
Re-hashes rows with the pre-revision formula (method by case only, dispersion,
solvent and solvent model verbatim). Every row whose stored hash came from the
post-revision formula gets its pre-revision hash back exactly. Rows whose
pre-revision hash is shared cannot all hold it: the holder is chosen by the
rules above and the others keep their current, unique hash. Upgrade then
downgrade restores every hash exactly, with or without merges in between
(``tests/services/test_level_of_theory_alias_rekey_plan.py`` checks this over
randomised lifecycles).

Revision ID: d0a7c3b91e4f
Revises: e88231299733
Create Date: 2026-09-30
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d0a7c3b91e4f"
down_revision: Union[str, Sequence[str], None] = "e88231299733"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HASH_FIELDS = (
    "method", "basis", "aux_basis", "cabs_basis",
    "dispersion", "solvent", "solvent_model", "keywords",
)
_BASIS_FIELDS = ("basis", "aux_basis", "cabs_basis")
_COMPONENT_FIELDS = ("dispersion", "solvent", "solvent_model")

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


def _method_case_key(name: str) -> str:
    """The method key as ``c8424fe82997`` left it: case only."""
    return name.strip().lower()


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


def _lot_hash(row, *, aliased: bool) -> str:
    """The application's hash formula.

    ``aliased=False`` is the formula as ``c8424fe82997`` left it (method by
    case, basis names keyed, dispersion/solvent/solvent model verbatim).
    """
    mapping = row._mapping
    payload = {field: mapping[field] for field in _HASH_FIELDS}
    for field in _BASIS_FIELDS:
        payload[field] = _basis_identity_key(payload[field])
    if aliased:
        payload["method"] = _method_identity_key(payload["method"])
        for field in _COMPONENT_FIELDS:
            payload[field] = _component_identity_key(payload[field])
    else:
        payload["method"] = _method_case_key(payload["method"])
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
        lambda row: _lot_hash(row, aliased=True),
        lambda row: _lot_hash(row, aliased=False),
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
        lambda row: _lot_hash(row, aliased=False),
        lambda row: _lot_hash(row, aliased=True),
        merged_ids,
    )
    return updates, groups, [h._mapping["public_ref"] for h in blocked]


def _apply(bind, updates: dict[int, str]) -> None:
    """Placeholder first, then target, so no order can trip the constraint."""
    for row_id in updates:
        placeholder = hashlib.sha256(f"d0a7c3b91e4f:placeholder:{row_id}".encode()).hexdigest()
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
        f"level_of_theory alias/component re-key: {len(updates)} row(s) re-hashed, "
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
            "pre-#618 hash (rows this revision moved onto a shared key, or "
            "spellings left over from c8424fe82997); one row per group took it and "
            "the others kept their current hash."
        )
    for ref in blocked:
        print(f"  {ref} NOT re-hashed: a merged row already holds its pre-#618 hash.")
