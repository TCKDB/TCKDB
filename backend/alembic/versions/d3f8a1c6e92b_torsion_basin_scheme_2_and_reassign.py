"""Add conformer scheme ``torsion_basin`` version 2 and reassign every observation under it.

No schema change. The new scheme is a row (parameters are data) and the rest is a data step.

What was wrong with version 1
-----------------------------
* The same planar aryl-nitro, aryl-aldehyde or aryl-ether conformer twisted +0.5 degrees and -0.5 degrees was
  assigned to different basins: symmetric atom mappings were canonicalised by the lexicographically smallest list of
  quantised torsion bins, which jumps at the 0/360 wrap.
* Rotors whose one side is a hydrogen-only top were dropped from basin identity, so ethanol trans and gauche, syn and
  anti acetic acid, and hydrogen-bonded versus free hydroxyl rotamers shared a basin.
* The 15 degree tolerance was a round number.

Version 2 (parameters as stored, below): two geometries share a basin iff some graph symmetry of the molecule puts every
rotor within its own tolerance (20 degrees for heavy-atom and methyl rotors, 30 degrees for one- and two-hydrogen
tops); one- and two-hydrogen tops (O-H, S-H, O-O-H, N-O-H, -NH2, radical -CH2) are rotors; the symmetry search is
capped. See ``app/chemistry/basin_matching.py`` and DR-0005.

The data step
-------------
``app.services.conformer_reassignment.reassign_conformer_groups`` recomputes every species entry's basins under the new
default scheme, under the same per-entry advisory lock live uploads take, then moves what pointed at a changed group
(thermo targets, statmech basin declarations and ensemble members, conformer selections) to the group holding the
observation its own calculations came from, or, where that cannot be determined, to the group holding the original
representative. Merged groups land on the older group. Every group whose membership changed has its review reset to
``not_reviewed`` with a ``record_review_event``.

Approved rows are frozen by trigger (``conformer_observation`` and ``thermo`` are accepted-science roots; the statmech
declaration and its members are frozen with their statmech record). They are changed only through the declared-repair
path of ``e2c9a4f7b163``: one ``accepted_science_repair`` declaration per table, naming the exact columns, in this
transaction, each changed accepted row recorded old -> new in ``accepted_science_repair_change``. The trigger is not
disabled or weakened. A repair is declared only for a table that actually needs a write, so a database with nothing to
move declares nothing.

Statmech basin declarations and ensemble members follow the same rule through the narrow exception added by
``a5d9c3e7b1f2`` (only ``conformer_group_id``, plus the declaration's ``content_hash`` recomputed in the same repair).

Emptied groups are never deleted: the row stays with its review reset to ``not_reviewed``, so no review history or public
ref is orphaned.

Every scheme version ships its own data step. This one is a no-op unless the default scheme is exactly
(``torsion_basin``, 2): the service declines (``skipped_reason``) when the database's default is any other scheme, so a
later version's code can never apply its rule under this revision's name.

Idempotent: the grouping is a deterministic function of the observations, so running the step again changes nothing.

Downgrade
---------
Makes version 1 the default again. It does **not** put observations back: the previous grouping cannot be rebuilt (a
merge destroys which observation was in which group, and version 1 cannot be run by the code any more), and version 1
is no longer implemented. The version 2 row is kept because observations and selections reference it. Upgrading again
re-asserts version 2 as the default and re-runs the (idempotent) step.

Superseded by ``b7e4c9a2d518`` (comment added with it; nothing executable here changed)
-------------------------------------------------------------------------------------
``app.services.conformer_reassignment`` now implements ``torsion_basin`` version 3 and declines (``skipped_reason``) on
a database whose default scheme is version 2. So on a database that has not yet run this revision, the data step above
is a no-op and ``b7e4c9a2d518`` does the grouping once, under version 3. A database that already ran this revision is
unaffected: its version 2 reassignment happened, and ``b7e4c9a2d518`` regroups it.

Revision ID: d3f8a1c6e92b
Revises: a5d9c3e7b1f2
Create Date: 2026-10-26
"""

import base64
import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.orm import Session

from alembic import op

revision: str = "d3f8a1c6e92b"
down_revision: Union[str, Sequence[str], None] = "a5d9c3e7b1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "torsion_basin"
_VERSION = 2

#: Frozen copy of the version 2 parameters (``TORSION_BASIN_PARAMS`` in ``app.services.conformer_resolution``): what
#: this revision stored is what this revision stored, whatever that constant later becomes.
_PARAMETERS = {
    "matching_rule": "symmetry_existence",
    "require_all_comparable_torsions_within_threshold": True,
    "use_circular_difference": True,
    "torsion_tolerance_heavy_degrees": 20,
    "torsion_tolerance_terminal_h_degrees": 30,
    "exclude_methyl_rotors": False,
    "exclude_terminal_noisy_rotors": False,
    "methyl_symmetry_fold": 3,
    "max_symmetry_mappings": 4096,
    "quantization_bin_degrees": 15,
    "rigid_fallback_use_rmsd": True,
    "rmsd_threshold_angstrom": 0.5,
    "tie_break": "closest_torsional_distance",
}

_DESCRIPTION = (
    "Symmetry-aware torsional basin matching: two geometries share a basin when some graph symmetry of the molecule "
    "puts every rotor within its own tolerance (20 degrees for heavy-atom and methyl rotors, 30 degrees for one- and "
    "two-hydrogen tops such as O-H and -NH2). One- and two-hydrogen tops are rotors. The symmetry search is capped; "
    "the stored fingerprint's bins are display only. Above the cap the answer can depend on the order of "
    "atoms in a deposited file (recorded as symmetry_cap_hit)."
)


def _content_ref(prefix: str, canonical_identity: str) -> str:
    """Frozen copy of ``app.services.public_refs.make_content_ref`` (sha256, first 16 bytes, base32, 26 chars)."""
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).digest()[:16]
    return f"{prefix}_{base64.b32encode(digest).decode('ascii').lower().rstrip('=')[:26]}"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE conformer_assignment_scheme SET is_default = false WHERE is_default"))
    bind.execute(
        sa.text(
            """
            INSERT INTO conformer_assignment_scheme
                (name, version, scope, description, parameters_json, is_default, public_ref)
            VALUES
                (:name, :version, CAST('canonical' AS conformer_assignment_scope_kind), :description,
                 CAST(:parameters AS jsonb), true, :ref)
            ON CONFLICT ON CONSTRAINT uq_conformer_assignment_scheme_name
            DO UPDATE SET is_default = true
            """
        ),
        {
            "name": _NAME,
            "version": _VERSION,
            "description": _DESCRIPTION,
            "parameters": json.dumps(_PARAMETERS),
            "ref": _content_ref("cas", f"cas:name={_NAME};version={_VERSION};scope=canonical"),
        },
    )

    # Imported here, not at module level: importing the app from a migration module at load time would run for
    # every alembic command, including ones that never reach this revision.
    from app.services.conformer_reassignment import reassign_conformer_groups

    session = Session(bind=bind)
    report = reassign_conformer_groups(session, repair_revision=revision)
    session.flush()
    print(f"[{revision}] conformer reassignment: {report.summary()}")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE conformer_assignment_scheme SET is_default = false WHERE is_default"))
    bind.execute(
        sa.text("UPDATE conformer_assignment_scheme SET is_default = true WHERE name = :name AND version = 1"),
        {"name": _NAME},
    )
