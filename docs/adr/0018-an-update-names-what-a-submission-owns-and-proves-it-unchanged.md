# An update names what a submission owns, and proves it unchanged

**Status: proposed 2026-08-16.** No code change yet. Supersedes nothing; constrained by [0003](0003-freeze-ever-approved-science.md), [0007](0007-curated-selections-are-a-release-overlay-not-a-column.md), [0015](0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md) and [0016](0016-review-approves-a-claim-and-does-not-cascade.md).

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

## What this deliberately does not settle

**Whether a depositor may supersede their own record.** The path from "my number was wrong" to a corrected record plus an edge is the thing depositors will actually want, and it is a larger design: it touches review state, citation, and whether a supersession by a non-curator needs approval. This record covers editing, not correcting. Deciding it is the natural successor.

**Whether ownership should be transferable.** A depositor leaves a group; who owns their submissions? Today the answer is "nobody else", which is a real operational problem and not one an update endpoint should solve on the side.

**Whether a curator may update what a depositor deposited.** [0016](0016-review-approves-a-claim-and-does-not-cascade.md) established that a reviewer's act must be represented as the act they performed. A curator editing a depositor's record is a different act from a depositor editing it, and if it is allowed at all it needs its own audit shape rather than reusing this one.

**Whether refs of records inside a bundle are individually updatable**, or only through the root that deposited them. The bundle roots create several records in one request; whether each is independently addressable afterwards is a surface question this record does not answer.
