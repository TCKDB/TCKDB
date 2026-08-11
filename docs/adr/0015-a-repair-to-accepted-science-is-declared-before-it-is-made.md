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

The check is over columns rather than rows, deliberately. A repair of this kind
is corpus-wide by nature, and a list of row keys written in advance would be a
worse-founded claim than a list of columns. What makes that acceptable is that
every row it reaches is recorded individually — the root it sits under, the
row's primary key, the columns that changed, and the values on both sides. "The
record did not change scientifically" becomes a join.

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

## What this does not cover

`record_review` is outside the mechanism; approval history is not repairable.
`b4e7c1d20f83` is **not** retrofitted — it is deployed, and its decision to
leave the guard standing and fail loudly on a non-canonical accepted row remains
the correct outcome for a migration that cannot ask anybody. What changes is
that the operator who hits that failure now has somewhere to go that is not a
disabled trigger.

Nothing in the shipping revision repairs anything. It is the path the next
repair takes, and the first thing an auditor will ask of that repair is what it
declared.
