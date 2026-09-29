"""level_of_theory: hash basis names by their identity key (#574)

``lot_hash`` hashed ``basis``, ``aux_basis`` and ``cabs_basis`` byte for
byte, so Psi4's ``def2-tzvp`` and Gaussian/ARC's ``def2tzvp`` were two
levels of theory. The application now hashes each basis name through
``app.chemistry.basis_set_names.basis_identity_key`` (lower case, plus the
family hyphen in ``def2-`` and ``cc-p`` restored). This revision re-keys the
rows that already exist, so that the next upload of any of them still finds
it. The verbatim names are not touched.

What this revision writes
-------------------------
Only ``level_of_theory.lot_hash``. Nothing else, on any table.

* A row whose identity key is the same as before (a basis already written
  in lower case with its family hyphen, or no basis) keeps its hash
  unchanged.
* A row whose spelling changes under the key gets the new hash. This is
  the same in-place re-hash ``e1a5c3f7b9d4`` did when spin treatment joined
  the identity.
* **Rows that were already duplicates** (two spellings of one level) share
  a new hash, and ``lot_hash`` is unique, so only one of them can hold it.
  It goes to the row that already holds it, if any, and otherwise to the
  row with the smallest id. The others keep their old hash. New uploads
  resolve to the holder, so no new calculation joins the others, but their
  existing calculations still point at them. This revision does not merge
  them. ``backend/scripts/ops/merge_duplicate_levels_of_theory.py`` does,
  as a separate, dry-run-first operator step, and the groups found are
  printed below so the operator knows to run it.

Why the merge is not in this revision
-------------------------------------
Merging repoints ``calculation.lot_id``. ``calculation`` is an
accepted-science root (``trg_as_root_calculation``). Repointing an approved
calculation needs a declared repair (ADR 0015), and whether any loser row
carries an approved calculation is a fact about one deployment's data. A
migration runs unconditionally inside ``upgrade head``; it cannot stop and
show an operator a plan. ``frequency_scale_factor`` and
``energy_correction_scheme`` also reference ``level_of_theory`` and have
unique keys that include it, so a repoint there can collide. The script
refuses a group in any of those cases and merges only groups whose
calculations are all unapproved and that nothing else references.

``public_ref`` is not written
-----------------------------
A level of theory's ``public_ref`` is derived from ``lot_hash`` once, at
insert, and stored. This revision updates ``lot_hash`` only, so every
existing row keeps its ``public_ref``.
``tests/db/test_level_of_theory_basis_identity_migration.py`` checks this.

Rules frozen here
-----------------
``_HYPHEN_RULES`` and ``_basis_identity_key`` are a copy of the application
rules as of this revision. A migration must describe what it ran, so it does
not import application code that may change later. A test holds the two in
agreement for as long as the rules are the same.

Downgrade
---------
Re-hashes every row with the pre-revision formula (verbatim basis names).
That formula is a function of columns this revision never writes, so the
downgrade restores every pre-existing row's hash exactly. It stays
collision-free after the upgrade has been in use: two rows with the same
verbatim fields would have had the same identity key, and the upload path
would have resolved the second to the first. If
``merge_duplicate_levels_of_theory.py`` has deleted loser rows, the
downgrade does not bring them back; nothing here recorded them.

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


def plan_rekey(rows) -> tuple[dict[int, str], list[tuple[str, list[str]]]]:
    """Which rows get which new hash, and which duplicate groups remain.

    :returns: ``(updates, duplicate_groups)``. ``updates`` maps row id to its
        new hash, for rows whose hash changes. ``duplicate_groups`` lists
        ``(holder public_ref, [other public_refs])`` for every identity key
        that more than one row maps to.
    """
    by_hash: dict[str, list] = defaultdict(list)
    for row in rows:
        by_hash[_lot_hash(row, keyed=True)].append(row)

    updates: dict[int, str] = {}
    groups: list[tuple[str, list[str]]] = []
    for new_hash, members in by_hash.items():
        holder = next(
            (m for m in members if m._mapping["lot_hash"] == new_hash),
            min(members, key=lambda m: m._mapping["id"]),
        )
        if holder._mapping["lot_hash"] != new_hash:
            updates[holder._mapping["id"]] = new_hash
        others = [m for m in members if m is not holder]
        if others:
            groups.append(
                (
                    holder._mapping["public_ref"],
                    [m._mapping["public_ref"] for m in others],
                )
            )
    return updates, groups


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(_SELECT_ROWS).all()
    updates, groups = plan_rekey(rows)
    for row_id, new_hash in updates.items():
        bind.execute(_UPDATE_HASH, {"h": new_hash, "id": row_id})
    print(
        f"level_of_theory basis identity re-key: {len(updates)} row(s) re-hashed, "
        f"{len(groups)} duplicate group(s) left for "
        "scripts/ops/merge_duplicate_levels_of_theory.py."
    )
    for holder, others in groups:
        print(f"  {holder} holds the key; also spelled as {', '.join(others)}")


def downgrade() -> None:
    bind = op.get_bind()
    for row in bind.execute(_SELECT_ROWS).all():
        old_hash = _lot_hash(row, keyed=False)
        if row._mapping["lot_hash"] != old_hash:
            bind.execute(_UPDATE_HASH, {"h": old_hash, "id": row._mapping["id"]})
