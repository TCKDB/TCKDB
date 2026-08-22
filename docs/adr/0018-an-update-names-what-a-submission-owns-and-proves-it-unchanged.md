# An update names what a submission owns, and proves it unchanged

**Status: proposed 2026-08-16, amended 2026-08-22.** No code change yet. The amendment settles two of the questions this record first left open — whether a depositor may supersede their own record, and what an update does to a record under review — and corrects a third that was posed wrongly: the level of theory, which turns out not to be a supersession question at all. Supersedes nothing; constrained by [0003](0003-freeze-ever-approved-science.md), [0007](0007-curated-selections-are-a-release-overlay-not-a-column.md), [0015](0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md) and [0016](0016-review-approves-a-claim-and-does-not-cascade.md).

A depositor who has uploaded a reaction wants to correct it. Today there is no path: the upload roots create, and nothing edits. The obvious design — hand the depositor a handle, let them PUT it back — collides with four things this store already decided, and the collisions are the interesting part.

The proposal that prompted this was: *query for one of my reactions, get a temporary unique id back, submit an update quoting it, and have the server check both that it is the same reaction and that my API key matches the uploader recorded against it.* The shape is right. Three of its four nouns turn out to be wrong, and each is wrong for a reason worth writing down.

## A reaction has no uploader

`chem_reaction` is an **identity** table under the four-role split, and its public ref is **content-derived**: `rxn_…` is generated from the canonical identity, so two instances holding the same reaction produce the same ref, and two groups depositing the same reaction attach to **one row**.

There is therefore no such thing as "the person who uploaded that reaction", any more than there is a person who uploaded ethanol. Identity is deduped precisely so that it is nobody's. Asking whether an API key matches the uploader of a `rxn_` is a category error, and answering it would require inventing a first-depositor claim over shared identity — which is the opposite of what dedup is for.

The same holds for every content-derived ref: `spc_`, `rxn_`, `geom_`, `lot_`, `soft_`, `srel_`, `wft_`, `wfr_`, `lit_`, `cas_`, `fsf_`, `ecs_`.

**What a depositor owns is not the reaction. It is the entry and the results they deposited for it** — `rxe_`, `calc_`, `thm_`, `kin_`, `sm_`, `trn_`, `co_`, `tse_` — every one of which carries an **opaque** ref, generated per instance, because an upload of the same calculation to two instances is two events.

> **Only opaque refs are updatable. A content-derived ref names a shared identity and is owned by no one.**

That line does more work than it looks: it means the update surface is exactly the provenance and result rows, which is also the set whose other constraints are already settled.

## Ownership lives on the submission

`submission.created_by` exists and is indexed. Every deposit is submission-scoped, so the chain from a record to a principal already runs record → submission → `created_by`.

So the ownership question is answerable today, and it is answerable at the only place it is meaningful:

> **A principal may update a record if and only if it is the `created_by` of the submission that deposited it.**

Not "the reaction is mine". "The deposit is mine."

## The token is a version check, not a credential

The proposal has the server issue a temporary id at read time and validate it at write time, with the ownership check at read time only.

That makes the token a **bearer credential**: whoever holds it can write. It then has to be treated as a secret — never logged, never cached, short-lived, rotated — and leaking one is a privilege escalation.

Check ownership at **both** read and write and the token stops being a secret. It no longer answers *may you write?*; it answers only *is this still the record you read?* A leaked token then buys an attacker nothing, because the write is refused on ownership regardless.

> **Authorise on every request. The token proves the record has not changed underneath the caller, and nothing else.**

This is the HTTP `ETag` / `If-Match` pattern, and it should be built as that rather than as a parallel mechanism, so ordinary clients get correct behaviour without learning a TCKDB-specific dance. `If-Match` failing yields **412 Precondition Failed**, which is a status clients already implement.

## Most of what a depositor wants to "correct" is not an update

Two prior decisions remove most of the surface before any endpoint is designed, and pretending otherwise would ship an edit button that silently does the wrong thing.

**Accepted science is frozen.** Under [0003](0003-freeze-ever-approved-science.md) a record becomes immutable when it is *first* approved and stays immutable even if its review status later changes — deliberately, so that un-approving cannot become an edit channel. The `trg_as_child_*` triggers enforce it below the application, so an update endpoint cannot route around it. For an ever-approved record the answer to "I want to change this" is not an update at all; it is [0015](0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md)'s declared repair, or a new record plus a supersession edge.

**Results are append-only.** A number that was computed and deposited is a fact about what happened. Correcting it means depositing the corrected record and linking the old one to the new, which is supersession — a different operation with different semantics, and the one that keeps a citation resolvable.

What remains genuinely updatable is narrow and unglamorous: on a **never-approved** record, the provenance and annotation around the science rather than the science itself. The companion spec enumerates it field by field, because a list is the only honest form for this and a principle would over-promise.

> **If a field changes what the record *claims*, it is not updatable — deposit a correction and supersede. If it changes how the claim is *described or attributed*, it may be.**

## The decision

1. **Address by public ref, never by primary key.** Consistent with `docs/specs/public_identifier_policy.md` and the Phase D direction of `docs/specs/internal_ids_visibility_policy.md`, which is already hiding integer PKs rather than adding uses for them.
2. **Only opaque refs are updatable.** Content-derived identity refs are refused with a distinct code, because "you cannot edit shared identity" is a different repair from "that is not yours".
3. **Ownership is `submission.created_by`, checked on every request**, at read and at write.
4. **A version token accompanies every updatable read** and must be presented on write, as `ETag` / `If-Match`, answering only "unchanged since you read it".
5. **Ever-approved records are refused**, with a code that names supersession as the repair rather than a generic conflict.
6. **Result values are never updatable.** The updatable set is provenance and annotation on never-approved records, enumerated explicitly.
7. **A depositor may supersede their own record, and so may a curator — but they are not the same act.** The path from "my number was wrong" to a corrected record plus a supersession edge is what depositors actually want, and today only a curator can create that edge, so every correction waits on someone else's attention. The depositor's supersession therefore becomes the **ordinary** path. It is a *correction*, not an edit: the original row is never rewritten, the edge carries the meaning, and everything [0003](0003-freeze-ever-approved-science.md) freezes stays frozen.

   A curator superseding someone else's record stays available and stays **exceptional** — an intervention, taken when something is wrong and the depositor is not the one fixing it. [0016](0016-review-approves-a-claim-and-does-not-cascade.md) decides the shape that has to take: a reviewer's act must be represented as the act they performed. So these are not one edge with a different name on it. The record must show which happened and who did it, because "the depositor corrected their own number" and "a curator intervened in someone else's record" are different facts about a dataset — a store where curators routinely intervene has a quality problem, and one where depositors self-correct is working. A reader who cannot tell them apart has lost the more interesting one.
8. **An update to a record under review is refused while the review is open.** It does not queue, and it does not reset the review. Resetting is rejected on cost rather than principle: a depositor editing a record in the window where a reviewer is looking at it is vanishingly rare, and the machinery to make a mid-review edit coherent — invalidating a partial verdict, telling the reviewer their work was discarded, deciding whether the clock restarts — is a large amount of design for an event that does not happen. Refusing costs the depositor one message and a wait. That message must say the record is under review and that the edit can be re-sent afterwards, so the depositor is not left guessing which of several refusal reasons applies.

## The level of theory is not superseded, it is re-pointed

The rule above — a field that changes what the record *claims* is not
updatable — sorts almost every field cleanly. The level of theory looks like
the hard case, and an earlier reading of it asked whether changing one counted
as an update or a supersession.

That question is malformed, and this record's own first section says why.
**A level of theory is identity.** `LevelOfTheory` sits in `_CONTENT_DERIVED`;
its ref is `lot_`, generated from `lot_hash`, so every calculation run at
`B3LYP/6-31G(d)` anywhere in the store attaches to **one row**. Superseding it
is the same category error as superseding ethanol, and editing it is worse:
the change would silently rewrite what every other calculation pointing at
that row claims, including calculations deposited by people who have never met
each other. Levels of theory dedupe harder than almost anything else here,
because the community uses a small number of them.

> **A level of theory is never edited and never superseded. What a depositor
> owns is the *link* — and the repair is to re-point it.**

So the repair for a mis-transcribed level has two shapes, neither of which
touches shared identity:

* **Re-point at an existing `lot_`.** Almost always available, precisely
  because levels dedupe — the level they meant is very likely already a row
  that someone else's calculation put there.
* **Deposit the level, then point at it.** If it genuinely is not in the store
  yet, creating it is an ordinary write against an identity table, which
  anyone may do. Identity is nobody's, so there is no ownership question to
  answer.

This makes the question a better-posed one: not *"is a level-of-theory change
an update or a supersession"*, but *"may a depositor re-point a provenance
link?"* — and decision 6 nearly answers it already, since the updatable set is
provenance and annotation on never-approved records, and a level-of-theory
link is provenance.

Re-pointing is also **recoverable in a way a supersession is not**, which
lowers what is at stake: the old `lot_` row is shared and untouched, nothing
is destroyed, and the previous link is recordable. A wrong re-point is a wrong
attribution to be corrected, not a fork in the citation graph.

## What this deliberately does not settle

**Whether a depositor's supersession needs approval before a reader sees it.** Decision 7 settles that both a depositor and a curator may supersede, and that the two are distinct acts. It does not settle whether a depositor's correction is visible immediately or waits on review. Immediate is the honest default for a record nobody has approved — there is nothing yet to protect — but a record that has already been cited is a different case, and this record does not decide it.

**Whether ownership should be transferable.** A depositor leaves a group; who owns their submissions? Today the answer is "nobody else", which is a real operational problem and not one an update endpoint should solve on the side.

**Whether a curator may update what a depositor deposited.** [0016](0016-review-approves-a-claim-and-does-not-cascade.md) established that a reviewer's act must be represented as the act they performed. A curator editing a depositor's record is a different act from a depositor editing it, and if it is allowed at all it needs its own audit shape rather than reusing this one.

**Whether refs of records inside a bundle are individually updatable**, or only through the root that deposited them. The bundle roots create several records in one request; whether each is independently addressable afterwards is a surface question this record does not answer.
