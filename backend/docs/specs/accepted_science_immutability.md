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
The current Pi deployment runs as `tckdb`, which is both superuser and owner;
role separation is therefore required before claiming this guarantee in
production.

There is deliberately no bypass GUC or normal maintenance escape hatch. Data
repair after acceptance must use replacement records and supersession. Schema
migrations run as the separate owner role.

## v1 limitation

This is ownership-aware immutability, not a recursive freeze of every referenced
registry. Shared identity and provenance rows—such as species/reaction entries,
levels of theory, software releases, literature, users, and workflow-tool
releases—remain mutable where their own constraints allow. Publication text
must not claim universal transitive immutability until those registries have
their own versioning or freeze policy.
