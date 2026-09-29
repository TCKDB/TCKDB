"""level_of_theory: hash method names by their identity key (#585)

``lot_hash`` hashed ``method`` byte for byte, so ARC's ``ccsd(t)-f12`` (ARC
lower-cases every method, ``arc/level.py``) and the ``CCSD(T)-F12`` a
Gaussian, ORCA or Molpro record carries were two levels of theory. The
application now hashes the method through
``app.chemistry.method_names.method_identity_key`` (stripped and
lower-cased), as it has hashed basis names through their own key since
``38b06819f099``. This revision re-keys the rows that already exist, so that
the next upload of any of them still finds it. The verbatim names are not
touched.

What this revision writes
-------------------------
* No DDL.
* Data: ``level_of_theory.lot_hash``, and nothing else on any table.

  - A row whose method is already lower case keeps its hash unchanged (the
    key of a lower-case name is the name).
  - A row whose method has upper-case letters gets the new hash. This is
    the same in-place re-hash ``38b06819f099`` did for basis names.
  - **Rows that were already duplicates** (two cases of one method, alone or
    combined with two spellings of one basis) share a new hash, and
    ``lot_hash`` is unique, so only one of them can hold it. It goes to the
    row that already holds it, if any, and otherwise to the row with the
    smallest id (see "Who holds a key" below). The others keep their old hash.
    New uploads resolve to the holder, so no new calculation joins the others, but their existing
    calculations still point at them. This revision does not merge them:
    ``scripts/ops/merge_duplicate_levels_of_theory.py`` does, as a
    dry-run-first operator step, using the same ``level_of_theory_merge``
    alias table ``38b06819f099`` created (a merged row is kept, with its
    ``public_ref``, so a release that cited it still resolves). The groups
    found are printed, by ``public_ref``, so the operator knows to run it.
  - **A row already merged** (it has a ``level_of_theory_merge`` row) is
    never chosen as a holder and is never re-hashed: it is an alias, and the
    hash it kept resolves nothing. A group always has an unmerged member,
    because a merge only joins rows the key already equates and this key only
    equates more.

Who holds a key
---------------
In each group of rows that now share one hash, exactly one row can hold it.
The holder is, in order:

1. the unmerged row that already holds the target hash;
2. the unmerged row whose current hash equals the hash its own *previous*
   formula gives (the row that held the key of the formula this revision
   replaces; on downgrade, the row that holds this revision's key). Choosing
   it makes upgrade-then-downgrade exact, and keeps the row the #582 merge
   script chose as its holder as the holder here. Without this rule, an older
   row that only ever carried a stale hash could take the key, the script
   would then merge the true holder into it, and the downgrade would need the
   hash the merged row kept: a unique violation;
3. the unmerged row with the smallest id.

A hash **held by a merged row** is occupied: a merged row keeps its hash, and
it is not touched here. If a target is occupied that way, that group's update
is skipped and reported (both directions), rather than violating
``uq_level_of_theory_lot_hash``. Rule 2 makes this unreachable for data the
merge script produced; the check is the backstop.

Why the merge is not in this revision
-------------------------------------
Same reason as ``38b06819f099``: merging repoints ``calculation.lot_id``,
``calculation`` is an accepted-science root, and whether a duplicate carries
approved science is a fact about one deployment's data that a migration
cannot stop and ask about.

Out of scope: punctuation
-------------------------
``wb97xd`` and ``wB97X-D`` name one functional in most programs, but which
functional a spelling means depends on the program, so equating them takes an
alias table, not a spelling rule (see ``app/chemistry/method_names.py``).
Dispersion and solvent names are also hashed verbatim, and ARC lower-cases
those too; that is a separate change.

``public_ref`` is kept, and is no longer re-derivable
-----------------------------------------------------
As in ``38b06819f099``: a level of theory's ``public_ref`` is minted from
``lot_hash`` once, at insert, and stored. This revision updates ``lot_hash``
only, so every existing row keeps its ``public_ref``.

Updates go through a unique placeholder
---------------------------------------
Both directions first move every row whose hash changes to a per-row
placeholder, then to its target, so no intermediate state can trip the
unique constraint whatever order the rows come in.

Rules frozen here
-----------------
``_HYPHEN_RULES``, ``_basis_identity_key`` and ``_method_identity_key`` are a
copy of the application rules as of this revision. A migration must describe
what it ran, so it does not import application code that may change later. A
test holds the two in agreement for as long as the rules are the same.

Downgrade
---------
Re-hashes rows with the pre-revision formula (method verbatim, basis keyed).
Every row whose stored hash came from the post-revision formula gets the
pre-revision one back exactly: the formula reads only columns this revision
never writes. Rows whose pre-revision hash is shared (the two cases of one
method that were merged, or that this revision left as duplicates) cannot all
hold it: the holder is chosen by the rules above (so a row that upgrade
re-hashed gets its previous hash back), and the others keep their current,
unique hash. Upgrade then downgrade restores every hash exactly, with or
without merges in between (``tests/services/test_level_of_theory_rekey_plan.py``
checks this over randomised lifecycles).

Revision ID: c8424fe82997
Revises: 38b06819f099
Create Date: 2026-09-29
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c8424fe82997"
down_revision: Union[str, Sequence[str], None] = "38b06819f099"
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
    return name.strip().lower()


def _lot_hash(row, *, keyed_method: bool) -> str:
    """The application's hash formula.

    ``keyed_method=False`` is the formula as ``38b06819f099`` left it (basis
    names keyed, method verbatim).
    """
    mapping = row._mapping
    payload = {field: mapping[field] for field in _HASH_FIELDS}
    for field in _BASIS_FIELDS:
        payload[field] = _basis_identity_key(payload[field])
    if keyed_method:
        payload["method"] = _method_identity_key(payload["method"])
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
        lambda row: _lot_hash(row, keyed_method=True),
        lambda row: _lot_hash(row, keyed_method=False),
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
        lambda row: _lot_hash(row, keyed_method=False),
        lambda row: _lot_hash(row, keyed_method=True),
        merged_ids,
    )
    return updates, groups, [h._mapping["public_ref"] for h in blocked]


def _apply(bind, updates: dict[int, str]) -> None:
    """Placeholder first, then target, so no order can trip the constraint."""
    for row_id in updates:
        placeholder = hashlib.sha256(f"c8424fe82997:placeholder:{row_id}".encode()).hexdigest()
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
        f"level_of_theory method identity re-key: {len(updates)} row(s) re-hashed, "
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
            "pre-#585 hash (rows this revision moved onto a shared key, or "
            "spellings left over from #582); one row per group took it and the "
            "others kept their current hash."
        )
    for ref in blocked:
        print(f"  {ref} NOT re-hashed: a merged row already holds its pre-#585 hash.")
