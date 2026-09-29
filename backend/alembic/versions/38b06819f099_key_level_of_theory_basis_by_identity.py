"""level_of_theory: hash basis names by their identity key (#574)

``lot_hash`` hashed ``basis``, ``aux_basis`` and ``cabs_basis`` byte for
byte, so Psi4's ``def2-tzvp`` and Gaussian/ARC's ``def2tzvp`` were two
levels of theory. The application now hashes each basis name through
``app.chemistry.basis_set_names.basis_identity_key`` (lower case, plus the
family hyphen in ``def2-`` and ``cc-p`` restored). This revision re-keys the
rows that already exist, so that the next upload of any of them still finds
it, and adds the column a later merge records itself in. The verbatim names
are not touched.

What this revision writes
-------------------------
* DDL: a new, empty table ``level_of_theory_merge`` (``merged_lot_id`` ->
  ``into_lot_id``). Only ``backend/scripts/ops/merge_duplicate_levels_of_theory.py``
  writes it. It is a table, not a column on ``level_of_theory``, because a
  new column would change the whole-row snapshots other features take of a
  level of theory (consistency-check inputs, reproducibility context
  hashes) for every row.
* Data: ``level_of_theory.lot_hash``, and nothing else on any table.

  - A row whose identity key is the same as before (a basis already written
    in lower case with its family hyphen, or no basis) keeps its hash
    unchanged.
  - A row whose spelling changes under the key gets the new hash. This is
    the same in-place re-hash ``e1a5c3f7b9d4`` did when spin treatment
    joined the identity.
  - **Rows that were already duplicates** (two spellings of one level) share
    a new hash, and ``lot_hash`` is unique, so only one of them can hold it.
    It goes to the row that already holds it, if any, and otherwise to the
    row with the smallest id. The others keep their old hash. New uploads
    resolve to the holder, so no new calculation joins the others, but
    their existing calculations still point at them. This revision does not
    merge them. The ops script does, as a separate dry-run-first operator
    step, and the groups found are printed so the operator knows to run it.

Why the merge is not in this revision
-------------------------------------
Merging repoints ``calculation.lot_id``. ``calculation`` is an
accepted-science root (``trg_as_root_calculation``), and an approved
thermo/statmech/kinetics record citing a calculation would see the level it
rests on change. Whether any duplicate carries such science is a fact about
one deployment's data. A migration runs unconditionally inside
``upgrade head``; it cannot stop and show an operator a plan.
``frequency_scale_factor`` and ``energy_correction_scheme`` also reference
``level_of_theory`` and have unique keys that include it, so a repoint there
can collide. The script refuses a group in any of those cases.

``public_ref`` is kept, and is no longer re-derivable
-----------------------------------------------------
A level of theory's ``public_ref`` is minted from ``lot_hash`` once, at
insert, and stored. This revision updates ``lot_hash`` only, so every
existing row keeps its ``public_ref``
(``tests/db/test_level_of_theory_basis_identity_migration.py`` checks it).
The cost: a re-keyed row's ``public_ref`` is no longer what its content
would mint on a fresh instance. LOT refs are minted once, not recomputed
(see ``docs/specs/public_identifier_policy.md``).

Updates go through a unique placeholder
---------------------------------------
Both directions first move every row whose hash changes to a per-row
placeholder, then to its target, so no intermediate state can trip the
unique constraint whatever order the rows come in.

Rules frozen here
-----------------
``_HYPHEN_RULES`` and ``_basis_identity_key`` are a copy of the application
rules as of this revision. A migration must describe what it ran, so it does
not import application code that may change later. A test holds the two in
agreement for as long as the rules are the same.

Downgrade
---------
Re-hashes rows with the pre-revision formula (verbatim basis names) and
drops ``level_of_theory_merge``.

* Every row whose stored hash came from that formula (every row the upload
  path wrote) gets it back exactly: the formula reads only columns this
  revision never writes.
* Rows whose stored hash did *not* come from the formula can share a target.
  ``scripts/seed_scientific_demo_data.py`` writes ``sha256("method|basis")``,
  so a demo-seeded row and an uploaded twin can coexist before this
  revision. Only one row per target can take it: the row already holding it,
  otherwise the smallest id. The others keep their current hash, which is
  unique. For a seeded twin that is exact when its key never moved, and a
  keyed hash otherwise; nothing recorded the seed value.
* Dropping ``level_of_theory_merge`` forgets merges. A merged row stays,
  with its ``public_ref`` and no calculations; after the downgrade its ref
  resolves to that row itself rather than to the row it was merged into.

Revision ID: 38b06819f099
Revises: 86ffcd9d3c65
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

revision: str = "38b06819f099"
down_revision: Union[str, Sequence[str], None] = "86ffcd9d3c65"
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


def _lot_hash(row, *, keyed: bool) -> str:
    """The application's hash formula; ``keyed=False`` is the pre-#574 one."""
    mapping = row._mapping
    payload = {field: mapping[field] for field in _HASH_FIELDS}
    if keyed:
        for field in _BASIS_FIELDS:
            payload[field] = _basis_identity_key(payload[field])
    payload["spin_treatment"] = mapping["spin_treatment"] or "unknown"
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def assign_hashes(rows, target_of) -> tuple[dict[int, str], list[tuple[object, list]]]:
    """Give each distinct target hash to exactly one row.

    Rows are grouped by ``target_of(row)``. In each group the target goes to
    the row that already holds it, otherwise to the smallest id; every other
    member keeps its current hash.

    :returns: ``(updates, groups)``: row id -> new hash for rows whose hash
        changes, and ``(holder, others)`` for every group of two or more.
    """
    by_target: dict[str, list] = defaultdict(list)
    for row in rows:
        by_target[target_of(row)].append(row)

    updates: dict[int, str] = {}
    groups: list[tuple[object, list]] = []
    for target, members in by_target.items():
        holder = next(
            (m for m in members if m._mapping["lot_hash"] == target),
            min(members, key=lambda m: m._mapping["id"]),
        )
        if holder._mapping["lot_hash"] != target:
            updates[holder._mapping["id"]] = target
        others = [m for m in members if m is not holder]
        if others:
            groups.append((holder, others))
    return updates, groups


def plan_rekey(rows) -> tuple[dict[int, str], list[tuple[str, list[str]]]]:
    """Upgrade plan, with duplicate groups named by ``public_ref``."""
    updates, groups = assign_hashes(rows, lambda row: _lot_hash(row, keyed=True))
    named = [
        (holder._mapping["public_ref"], [m._mapping["public_ref"] for m in others])
        for holder, others in groups
    ]
    return updates, named


def _apply(bind, updates: dict[int, str]) -> None:
    """Placeholder first, then target, so no order can trip the constraint."""
    for row_id in updates:
        placeholder = hashlib.sha256(f"38b06819f099:placeholder:{row_id}".encode()).hexdigest()
        bind.execute(_UPDATE_HASH, {"h": placeholder, "id": row_id})
    for row_id, new_hash in updates.items():
        bind.execute(_UPDATE_HASH, {"h": new_hash, "id": row_id})


def upgrade() -> None:
    op.create_table(
        "level_of_theory_merge",
        sa.Column("merged_lot_id", sa.BigInteger(), nullable=False),
        sa.Column("into_lot_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "merged_lot_id <> into_lot_id",
            name=op.f("ck_level_of_theory_merge_not_merged_into_itself"),
        ),
        sa.ForeignKeyConstraint(
            ["merged_lot_id"],
            ["level_of_theory.id"],
            name=op.f("fk_level_of_theory_merge_merged_lot_id_level_of_theory"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["into_lot_id"],
            ["level_of_theory.id"],
            name=op.f("fk_level_of_theory_merge_into_lot_id_level_of_theory"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("merged_lot_id", name=op.f("pk_level_of_theory_merge")),
    )
    op.create_index(
        op.f("ix_level_of_theory_merge_into_lot_id"),
        "level_of_theory_merge",
        ["into_lot_id"],
        unique=False,
    )

    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).all()
    updates, groups = plan_rekey(rows)
    _apply(bind, updates)
    print(
        f"level_of_theory basis identity re-key: {len(updates)} row(s) re-hashed, "
        f"{len(groups)} duplicate group(s) left for "
        "scripts/ops/merge_duplicate_levels_of_theory.py."
    )
    for holder, others in groups:
        print(f"  {holder} holds the key; also spelled as {', '.join(others)}")


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).all()
    updates, groups = assign_hashes(rows, lambda row: _lot_hash(row, keyed=False))
    _apply(bind, updates)
    if groups:
        print(
            f"level_of_theory downgrade: {len(groups)} group(s) of rows share one "
            "pre-#574 hash (a hash not written by the formula, such as the demo "
            "seed's); one row per group took it and the others kept their "
            "current hash."
        )

    op.drop_index(
        op.f("ix_level_of_theory_merge_into_lot_id"), table_name="level_of_theory_merge"
    )
    op.drop_table("level_of_theory_merge")
