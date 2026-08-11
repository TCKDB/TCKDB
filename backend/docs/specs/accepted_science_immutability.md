# Accepted-science immutability (v1)

Once a supported record has ever been approved, its scientific row and owned
child rows are immutable. Corrections are new records connected by an
append-only `scientific_record_supersession` edge.

## Supported roots

`calculation`, `thermo`, `statmech`, `kinetics`, `transport`, `network`,
`network_solve`, `applied_energy_correction`, `transition_state_entry`, and
`conformer_observation` are protected. `record_review.first_approved_at` is the
permanent acceptance marker; reopening or deprecating a review does not remove
protection.

Database triggers reject UPDATE and DELETE on an ever-approved root and reject
INSERT, UPDATE, and DELETE on its owned children. They also reject TRUNCATE on
all protected roots/children, review history, supersession history, and
reproducibility assessments. Calculation input/output geometries and their atom
coordinates are protected when referenced by an ever-approved calculation.
Multi-root child changes lock affected roots in sorted order.

`reaction_atom_map` and `reaction_atom_map_pair` (ADR 0011) are owned children
of `transition_state_entry`. A declared atom map is the content of the claim
that a saddle point connects the reaction's declared reactants and products, so
approving the entry freezes the map; correcting one means depositing a new
transition-state entry that carries the corrected map, approving it, and
recording a `transition_state_entry` supersession. They joined the regime in
`b6c1f4a8e703`, after `d3a7f1c9b284` introduced them outside it.

The migration conservatively backfills `first_approved_at` from the earliest
event that touches `approved`. Current approved or deprecated rows without such
an event use `reviewed_at`, then `created_at`.

It cannot reconstruct an approval that predates `record_review_event` when the
current row was later reopened, rejected, or reset and no surviving event
mentions `approved`. Those rows remain unmarked and require a one-time curator
audit before deployment; the migration does not invent acceptance history.

## Replacing a record

Use `supersede_scientific_record(...)`. The service requires a curator/admin,
a nonblank reason, an accepted old record, a currently approved replacement,
and equal stable subject identity. It deprecates the old review and appends the
edge atomically without committing.

Edges form linear, acyclic chains: each record has at most one outgoing and one
incoming edge. `A -> B -> C` is valid. Exact retries are idempotent only when
the normalized reason also matches. Supersession subject identity is:

- species entry for thermo, statmech, and transport;
- reaction entry plus direction for kinetics;
- owner plus calculation type for calculations;
- parent network for network solves;
- correction target plus application role for applied corrections;
- parent concept/group for transition-state entries and conformer observations.

Networks have no stable parent concept in v1, so same-type curator replacement
with an explicit reason is the current boundary.

Curator/admin authorization and the self-approval rule are enforced by the
service layer. Database rows preserve the actor id, but not an immutable
role-at-action snapshot, and a later role change must not invalidate archive
restore. Consequently, actor authority is temporal application evidence—not a
cryptographically unforgeable database invariant.

## Deployment prerequisite

Triggers are an adversarial guarantee only when the application role is a
non-superuser, does not own the protected tables/functions, and lacks DDL and
TRUNCATE privileges. A separate migration-owner role must own schema objects.
The deployment procedure in `backend/docs/deployment/database_roles.md`
provisions a restricted runtime account and a separate migration owner. The
guarantee applies only after its read-only role-contract check passes on the
target deployment.

There is deliberately no bypass GUC and no maintenance escape hatch. A change
to what a record *says* — any corrected number — must use a replacement record
and a supersession edge. Schema migrations run as the separate owner role.

## Repairing what a record does not say (`e2c9a4f7b163`)

Supersession is the right answer when the science changes, because forking
identity is what keeps a citation resolving to what was cited. It is the wrong
answer for a change that alters no scientific content — the letter case of an
element symbol in the derived `geometry_atom.element` index column, with
`geom_hash`, `xyz_text`, coordinates, isotopes and atom ordering untouched
(`b4e7c1d20f83`). Forking identity there re-keys a citable public ref to fix a
shift key.

Before this, the only mechanical route was `ALTER TABLE … DISABLE TRIGGER`,
which leaves nothing behind: no record that the guard was stood down, over
which rows, or on what justification. A repair is now declared first, and the
database checks the declaration.

**Declare.** Insert one row into `accepted_science_repair` naming
`target_table`, the exact `declared_columns` the repair may change, the
`alembic_revision` doing it, and a nonblank `reason`:

```sql
INSERT INTO accepted_science_repair
    (target_table, declared_columns, alembic_revision, reason)
VALUES ('geometry_atom', ARRAY['element'], 'b4e7c1d20f83',
        'canonicalise element symbol case; geom_hash, xyz_text and '
        'coordinates are not touched');
```

**Then repair, in the same transaction.** While the declaration is in force,
`tckdb_raise_if_accepted` permits an UPDATE to that table only if the columns
that actually changed are a **subset** of the declared set. The comparison is
`OLD` against `NEW` inside the existing `BEFORE UPDATE` guard, so a statement
that also touches one undeclared column is refused by name and the whole
transaction with it.

**What is recorded.** Each changed row appends a row to
`accepted_science_repair_change` per accepted root it sits under: the root's
`record_type`/`record_id`, the changed row's primary key as `row_identity`,
`changed_columns`, and `before_json`/`after_json`. Under an accepted root an
UPDATE either raises or appends a change row — there is no third outcome — so
"the record did not change scientifically" is a query rather than a docstring.

**What it cannot do.**

- **INSERT and DELETE stay unconditionally refused**, declaration or not. The
  row count under an accepted root is invariant under every repair.
- **Primary-key columns cannot be declared.** The record names the row by its
  key, and a row under a new identity is a different row.
- **It cannot be left open.** The declaration is matched on
  `pg_current_xact_id()`, so it is inert the moment its transaction ends —
  there is no close step. A second bound, `expires_at` (default 1 hour, capped
  at 24), raises rather than silently permitting, so a wedged transaction
  cannot hold it open either. At most one declaration per table per
  transaction.
- **It confers no authority.** Only a role owning the target table may declare
  a repair or record a change to it — exactly the roles that could already have
  run `DISABLE TRIGGER`. The runtime role of
  [`../deployment/database_roles.md`](../deployment/database_roles.md) owns
  nothing and is refused, even holding the blanket `INSERT` its default
  privileges grant. The guarantee is carried by triggers and depends on no
  grant.
- **The record cannot be edited.** Both tables are append-only and
  un-truncatable, on the same terms as `record_review_event`.

`record_review` is outside this mechanism entirely; approval history is not
repairable.

## v1 limitation

This is ownership-aware immutability, not a recursive freeze of every referenced
registry. Shared identity and provenance rows—such as species/reaction entries,
levels of theory, software releases, literature, users, and workflow-tool
releases—remain mutable where their own constraints allow. Publication text
must not claim universal transitive immutability until those registries have
their own versioning or freeze policy.
