# A repair to accepted science is declared before it is made

[0003](0003-freeze-ever-approved-science.md) froze every ever-approved record and
routed corrections through supersession. That is right, and this decision does
not weaken it: a corrected *number* forks identity, because a citation has to
keep resolving to what was cited. What 0003 did not have a position on is a
change that alters nothing a record says. The worked case is `b4e7c1d20f83` —
the letter case of an element symbol in `geometry_atom.element`, the derived
index column every element comparison joins against, with `geom_hash`,
`xyz_text`, coordinates, isotopes and atom ordering provably untouched.
Superseding there re-keys a citable public ref to fix a shift key, and the
replacement record would differ from the original in no scientific respect at
all — a fork that asserts a distinction that does not exist.

So the operator holding such a row had exactly one mechanical option:
`ALTER TABLE … DISABLE TRIGGER`, the work, `ENABLE`. The first draft of #118 did
that and review rejected it, correctly. The objection was not that the change
was wrong. It is that **after the fact the operation is invisible**. Nothing
records that a scientific-integrity control was stood down, over which rows, or
on what grounds; the claim "the record did not change scientifically" lived in a
migration docstring, and a docstring is prose, not an assertion anything checks.

## The alternative to an escape hatch is not a justification string

A repair table whose only content is free text would be the same bypass with
paperwork. It would record an intention, and the database would still have no
opinion about whether the repair matched it. The reason this is worth building
is that the claim is **checked**:

A repair declares, up front and in a row, the table it will change, the exact
columns it may change, the revision doing it, and why. While that declaration is
in force, the existing `BEFORE UPDATE` guard permits an UPDATE to that table
**only if the set of columns that actually changed is a subset of the declared
set** — computed by comparing `OLD` against `NEW`, which is why it is
enforceable rather than promised. A declaration for `geometry_atom(element)`
cannot move an atom, and an UPDATE that rewrites `element` *and* nudges `x` in
one statement is refused naming `x`. That case is the one that matters: the
declared change is the alibi for the undeclared one.

That check turns out to be enforced on two independent paths, which was not
designed and is worth knowing before anyone simplifies either one: the change
row carries its own `changed_columns`, and the trigger that writes it refuses a
set the declaration does not cover. Removing the check inside the permit
function alone still refuses the smuggling case, with a different message.

The check is over columns rather than rows, deliberately. A repair of this kind
is corpus-wide by nature, and a list of row keys written in advance would be a
worse-founded claim than a list of columns. What makes that acceptable is that
every row whose value it changes is recorded individually — the root it sits
under, the row's primary key, the columns that changed, and the values on both
sides. "The record did not change scientifically" becomes a join.

**The recording claim is narrower than "everything is recorded", and the
narrowness belongs here** rather than in a follow-up, because this is the
paragraph someone reads in two years before deciding whether to widen this
door. What is enforced is that a write which changes a *value* is either
refused or recorded. The comparison runs `to_jsonb(OLD)` against
`to_jsonb(NEW)`, and JSON numerics have no signed zero and ignore trailing
scale, so a write that changes only the representation of an equal value passes
with no change row — `SET x = -0.0` over a stored `0.0` succeeds silently under
an `element`-only declaration.

Scientifically that is nothing, and the reasoning is worth keeping rather than
just the conclusion. Only value-equal representations collapse; no genuinely
different number can hide, because `float8` renders round-trip; and text, enum,
timestamp, `mol` and `jsonb` all serialise exactly. Closing it would mean
normalising every value before comparing, on the path *every* UPDATE to a
guarded table takes, to catch a change that moves nothing a reader can observe.
The absolute phrasing was written first, and is recorded as wrong rather than
quietly corrected: an ADR that overstates a guarantee is exactly what lets the
next reader skip the check.

Only UPDATE. INSERT and DELETE under an accepted root stay unconditionally
refused, because adding or removing a row changes what reviewers accepted rather
than how it is spelled; the row count under an accepted root is invariant under
every repair this can express. Primary-key columns cannot be declared, for the
same reason the record is keyed on them: a row under a new identity is a
different row, which is what supersession is for.

## It cannot be left open, and it confers no authority

A permission that can outlive its use is a bypass on a delay. The scope here is
the transaction: the declaration carries `pg_current_xact_id()` and the guard
matches on it, so it is inert the instant its transaction ends. There is no
close step to forget, no flag to reset, and a committed row can never be
presented again. A second and independent bound is wall-clock — one hour by
default, capped at 24 — which raises rather than silently permitting, so a
wedged transaction cannot hold the door open either. This follows
[0014](0014-custody-of-stored-evidence-is-recorded-not-logged.md)'s age floors:
a guard any invocation may satisfy by doing nothing is not a guard.

The other half is that nobody gains anything. Only a role that owns the target
table may declare a repair to it — which is exactly the set of roles that could
already have run `DISABLE TRIGGER` and left no trace. This adds no power to
anyone; it converts an available and unrecorded capability into a recorded one,
which is the entire argument. In the deployment posture of
`backend/docs/deployment/database_roles.md` the runtime role owns nothing and is
refused, even holding the blanket `INSERT` that role's default privileges grant.
That grant is why the *record* is protected the same way rather than by a
revoke: default privileges cover tables created by later migrations, so a
grant-based defence would have to be re-applied after every one. A change row
must name a declaration made in the same transaction, for the same table, with
columns that declaration covers, and come from an owner of that table. All four
hold by construction when the guard writes it and none holds for a row written
by hand to make a repair look narrower than it was.

Both tables are append-only and un-truncatable, on 0003's own terms: a record of
a repair that the repairer can edit afterwards is not a record.

## The record does not travel, and that is a consequence rather than an omission

`tckdb.archive.v1` excludes both tables. Not because the account is
unimportant — [0014](0014-custody-of-stored-evidence-is-recorded-not-logged.md)
made the opposite call for `artifact_integrity_event` on the grounds that a
restore dropping it would resurrect condemned records as sound, and that
reasoning is right. It does not transfer here, and the difference is
structural. A custody observation is a fact about an *object*, and objects move
with the archive. A declaration's meaning is carried by `xact_id`, a
transaction id from the source cluster's counter, and every check deciding
whether it is live compares that against `pg_current_xact_id()`. Restored into
a fresh cluster whose counter starts near zero, an archived declaration names a
transaction larger than any the new database has issued: a row that is inert by
construction and misleading by shape. Relaxing the comparison to make it
transplantable would relax the one check that stops a declaration being minted
for a transaction that has not happened yet.

The repaired *value* travels with the science, which is what a restored
database has to get right. The account of how it got there stays with the
deployment that performed it and with that deployment's `pg_dump`, which
[0001](0001-separate-archive-projection-and-backup.md) already separates from
this projection.

## What this does not cover

`record_review` is outside the mechanism; approval history is not repairable.
Seven guarded tables are pure junction rows whose every column is part of their
own primary key — the `*_source_calculation` links, `network_reaction` and
`network_species` — so nothing about them can be respelled and no repair can
name them. That falls out of the key rule rather than being a separate one.
`b4e7c1d20f83` is **not** retrofitted — it is deployed, and its decision to
leave the guard standing and fail loudly on a non-canonical accepted row remains
the correct outcome for a migration that cannot ask anybody. What changes is
that the operator who hits that failure now has somewhere to go that is not a
disabled trigger.

Nothing in the shipping revision repairs anything. It is the path the next
repair takes, and the first thing an auditor will ask of that repair is what it
declared.
