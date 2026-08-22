# Updating a deposited record

**Status:** design, pending implementation. No code change yet.
**Decision record:** [ADR 0018](../../../docs/adr/0018-an-update-names-what-a-submission-owns-and-proves-it-unchanged.md).
**Constrained by:** ADR 0003 (accepted science frozen), ADR 0015 (declared repair), ADR 0016 (review does not cascade), `docs/specs/public_identifier_policy.md`, `docs/specs/internal_ids_visibility_policy.md`, DR-0028 Req 2 (no row ids in user-facing bodies).

---

## In plain terms

A depositor uploads a reaction, then notices they got something wrong. Today there is no way to fix it through the API.

This spec adds one — but a narrow one, because most of what people mean by "fix it" is not editing:

- If the record has ever been **approved**, it is frozen and cannot be edited at all. That is deliberate: someone may have cited it.
- If the thing that is wrong is a **number**, editing it would silently change what a citation resolves to. The right move is to deposit a corrected record and mark the old one superseded.
- What genuinely can be edited is the **description around the science** — a note, a missing literature link, an attribution — on a record nobody has approved yet.

So the endpoint exists, and it is small on purpose.

---

## 1. What can be addressed

**By public ref only.** Never by primary key. `docs/specs/internal_ids_visibility_policy.md` is already moving integer PKs *out* of public responses; an update surface must not add a reason to keep them.

**Only opaque refs.** The registry in `backend/app/services/public_refs.py` splits every ref-bearing class two ways:

| category | examples | updatable? |
|---|---|---|
| **content-derived** — same entity, same ref, on every instance | `spc_` `rxn_` `geom_` `lot_` `soft_` `srel_` `wft_` `wfr_` `lit_` `cas_` `fsf_` `ecs_` | **No** |
| **opaque** — one deposit, one event, per instance | `rxe_` `spe_` `calc_` `thm_` `kin_` `sm_` `trn_` `cg_` `co_` `ts_` `tse_` `net_` `nsolve_` `nkin_` | Candidate |

A content-derived ref names an identity that **deduplicates**, so two depositors of the same reaction hold the same `rxn_`. Nobody owns it and nobody may edit it. Refusing these needs its own code — *"identity is shared, it is not yours to edit"* is a different repair from *"that record belongs to someone else"*, and collapsing them would repeat the defect #195 removed.

> **Implementation note.** Derive the updatable set from `PREFIXES` and `_CONTENT_DERIVED` at import time rather than restating it here. A second hand-maintained list is a second thing to drift. A test should assert that every name in `PREFIXES` is classified exactly once.

### The pairing, and what "my reactions" returns

The two categories are not alternatives — every deposit produces one of each, and a depositor interacts with both:

| ref | what it names | owned |
|---|---|---|
| `rxn_…` | `ChemReaction` — *"H + CH₄ → H₂ + CH₃"*. One row however many people deposit it | **No** |
| `rxe_…` | `ReactionEntry` — *this* deposit of it: participants as specific entries, geometries, level of theory, attached kinetics | **Yes** |

The same pairing holds for `spc_`/`spe_` and `ts_`/`tse_`.

So a "list my reactions" surface returns **entries**, and each row must carry **both refs**: the `rxn_` says which reaction chemically, the `rxe_` is the handle the depositor owns and quotes in an update.

**Both are required, because one depositor may hold several entries for one reaction** — the same reaction computed at two levels of theory is two `rxe_` sharing one `rxn_`. A listing keyed on the reaction alone would collapse them and invite an update against the wrong entry, silently. Show the discriminating provenance (level of theory, submission, deposit time) alongside, or the depositor cannot tell their own entries apart.

Two depositors holding entries for the same reaction is likewise normal: two `rxe_`, one `rxn_`, each owned by its own submission. That is dedup working, not a conflict to resolve.

---

## 1a. Listing what you own

A flat list of opaque refs plus a column dump is machine-readable and human-useless. Three things make it usable, none needing a schema change — so this can ship well before the update endpoint it exists to serve.

### Use the name the depositor already gave

`submission.title` exists — `String(200)`, nullable, `backend/app/db/models/submission.py:100`. It is a depositor-supplied name for the whole deposit and is not currently surfaced on any read. Show it.

> **Do not persist the payload's local keys as a per-record label.** It is the obvious idea and it fails on measurement: ARC — the dominant producer — generates keys structurally, not meaningfully (`calc42-a3f9`, `prev1`, `alt0_opt`, `arc/tckdb/adapter.py:715,1124,2023`). Persisting them buys a column of noise for most real deposits, at the cost of a migration. Reconsider only if hand-authored bundles become a significant share.

### Group by reaction, do not list entries flat

The chemically meaningful thing goes at the top; the owned handles hang beneath it:

```
H + CH4 -> H2 + CH3        rxn_8k2p...
  |- rxe_7f3a...   wB97X-D/def2-TZVP    "ARC batch 12"   2026-07-02
  '- rxe_9c1b...   CCSD(T)/cc-pVTZ      "refit"          2026-08-14
```

### Compute what differs, rather than dumping every column

**This is the part worth building.** Given a depositor's sibling entries under one reaction, determine the *discriminating axis* across that set and present only it:

> *These 3 entries differ only in level of theory: wB97X-D, B3LYP, CCSD(T).*

A record has dozens of fields, nearly all identical across siblings; showing them buries the one that matters. The computation is small and runs over a set already fetched.

It degrades honestly: if entries differ on four axes, say so and show four columns. The failure mode is verbosity, not wrongness. If entries differ on *nothing* observable, say **that** — it means a genuine duplicate deposit, which the depositor wants to know about and which no column dump would have made obvious.

### The limit, stated so nobody designs against it

None of this makes an opaque ref memorable, and it must not try. `rxe_7f3a2b...` is a handle a **client** quotes, not a string a human retypes: the human picks a row, the client sends the ref. Optimising the ref itself for human recall would trade away the property that makes it safe to publish.

---

## 2. Who may update

```
record → submission → submission.created_by → principal
```

`submission.created_by` exists (`backend/app/db/models/submission.py:70`) and is indexed (`ix_submission_created_by`).

**The rule:** a principal may update a record if and only if it is the `created_by` of the submission that deposited it.

**Checked on every request** — the read that issues the token *and* the write that spends it. See §4 for why this matters more than it looks.

Open question, deliberately not answered here: an admin or curator acting on a depositor's record is a **different act** and needs its own audit shape (ADR 0016's principle — represent the act performed, not its consequence). Until that is designed, admin bypass is out of scope rather than implicitly allowed.

---

## 3. What may be changed

**The governing test:**

> If a field changes what the record **claims**, it is not updatable — deposit a correction and supersede.
> If it changes how the claim is **described or attributed**, it may be.

### Never updatable, on any record

- **Any result value.** `thermo`, `kinetics`, `statmech`, `transport` and the `calc_*_result` numbers are append-only. Changing one rewrites what a citation resolves to.
- **Anything on an ever-approved record.** ADR 0003. Enforced below the application by the `trg_as_child_*` triggers, so the endpoint cannot route around it even by accident — but it must refuse *before* the trigger fires, so the depositor gets a code naming supersession rather than a database error.
- **Identity-defining columns** — the fields a content-derived ref is computed from. Changing one does not edit the record; it makes it a different record.
- **Provenance that is itself a shared identity row** — a `level_of_theory`, a `software_release`. Re-pointing a record at a *different* LoT is allowed (§ below); editing the LoT is not.

### Candidate updatable set — **to be confirmed field by field before implementation**

This list is a starting point, not a settled contract. Each entry needs checking against the models, and the ones that survive get written into the spec explicitly.

| field | on | why it is describing, not claiming |
|---|---|---|
| free-text notes / comments | most deposit roots | annotation; asserts nothing about the world |
| literature link (`lit_`) | products, calculations | attribution of where a value was reported |
| `workflow_tool_release` link | calculations | which tool ran it — provenance, not result |
| missing `level_of_theory` link | calculations | pointing at the *correct existing* LoT, where one was omitted |
| conformer-group membership | `conformer_observation` | grouping is a judgement, not a measurement |

**Everything here is provenance completion** — a depositor filling in something they omitted. That is the honest scope, and it matches the ten `W_MISSING_*_PROVENANCE` upload warnings which already exist precisely because an incomplete record is still a true record (ADR 0008).

> **Do not generalise this into "anything not a number".** A `stationary_point` kind, a charge, a multiplicity are not numbers and are absolutely claims. The list is the contract.

---

## 4. The mechanism

### Read

```
GET /api/v1/records/{ref}
→ 200
   ETag: "<version token>"
   { …record…, "updatable_fields": [...] }
```

- Ownership checked. Not owned → the ownership refusal code, **not** 404. (A depositor who mistypes their own ref should be told it exists and is not theirs; whether that discloses too much is settled in §6.)
- `updatable_fields` is served rather than documented, so a client never has to hard-code the list and a future narrowing does not silently break one.

### Write

```
PATCH /api/v1/records/{ref}
If-Match: "<version token>"
{ …changed fields only… }
```

| condition | status | code |
|---|---|---|
| success | 200 | — |
| `If-Match` absent | 428 | `precondition_required` |
| token stale — record changed since read | **412** | `record_changed_since_read` |
| not the depositor | 403 | ownership refusal |
| ever-approved | 409 | names supersession as the repair |
| content-derived ref | 422 | `identity_ref_not_updatable` |
| field not in the updatable set | 422 | names the field |

**`PATCH`, not `PUT`.** A `PUT` carries the whole record, which invites a client to send back result fields it did not change and forces the server to diff them. `PATCH` makes "changed fields only" the contract.

### Why the token is not a credential

The token answers exactly one question: *is this still the record you read?*

It does **not** answer *may you write?* — ownership does, and ownership is re-checked on the write. So the token needs no secrecy: it may be logged, cached, and put in a URL without creating a privilege-escalation path. A leaked token buys nothing.

Had ownership been checked only at read time, the token would be a bearer credential and every one of those properties would reverse.

### Nothing is minted, allocated, or stored

**This is load-bearing and easy to get wrong.** The token must be **derived**, not issued.

An issued token — a row in a `update_token` table, handed out per read — is a denial-of-service surface: an unauthenticated-ish read loop mints unbounded rows, and storage and index growth are attacker-controlled. It also creates a second source of truth about "has this changed", which is the shape ADR 0007 rejected for `is_current`.

**Derive the ETag from a hash of the record's updatable field values**, together with its ref. Then:

- Reading a million times produces the **same** token a million times. There is nothing to exhaust; a read loop costs exactly what the read costs and no more.
- No new column, **no migration**, no cleanup job, no expiry policy, no clock skew.
- It is precisely scoped: an unrelated column changing does not invalidate a caller's token, because the token covers only what they could have changed.

> **Do not derive it from `updated_at`.** Checked 2026-08-16: `TimestampMixin` in `backend/app/db/base.py` supplies **`created_at` only**. `updated_at` is added ad hoc on exactly one model (`machine_review_curator_task.py:151`), and `dataset_release.py:250` documents *deliberately* not having one. An `updated_at`-based design would require adding the column across the updatable set — a migration on deployed tables — to buy something a field hash gives for free.

---

## 5. What must be recorded

An update is a mutation of provenance, so it needs an audit trail of its own. **This is the part most likely to be under-built.**

At minimum: who, when, which record, which fields, and the previous values. Two candidate homes:

1. A dedicated `record_update_event` table — append-only, opaque ref, consistent with the curation-overlay shape used by `record_review` and `release_selection`.
2. The existing submission machinery, treating an update as an event on the owning submission.

(1) is likelier to be right: an update is an event about a record, and ADR 0007's reasoning — *a claim about a record is stored beside the record, never inside it* — applies unchanged.

**A record that has been updated must be able to say so on read.** Otherwise a consumer who fetched it yesterday has no way to learn it changed, which is the citation problem this whole store exists to avoid, reintroduced at a smaller scale.

---

## 6. Open questions, to settle before building

1. **Does a wrong-owner refusal disclose existence?** 403 says "this exists and is not yours"; 404 says nothing. For a public scientific database where refs are already published on read surfaces, 403 is probably right — but the download route deliberately answers a uniform 404 for unknown/under-review/rejected digests specifically to resist probing, and these two surfaces should not disagree by accident.
2. **Should an update reset review state?** If a record is under review and the depositor changes its literature link, does the reviewer's in-progress judgement still apply? Interacts directly with ADR 0016.
3. **Bundle-deposited records** — individually addressable, or only through their root? The bundle roots create several records per request.
4. **Rate limiting and abuse.** An update endpoint is a write surface authenticated by API key; the existing upload throttles may or may not cover it.
5. **Does the client need a new method?** `tckdb-client` currently only creates. A `patch_record` would be a wire-package change with a version bump and an ARC notification.

---

## 7. Verification requirements

Stated up front because this surface is unusually easy to test vacuously.

- **A test asserting only "a 4xx arrived" passes against an endpoint that refuses everything.** Assert the `(status, code)` pair, and in the same test assert that a *legitimate* update succeeds.
- **Prove the freeze.** Approve a record, attempt an update, assert the refusal — and assert the row is **byte-identical** afterwards, the way ADR 0007's selection test does. A refusal that still wrote something is the failure worth catching.
- **Prove the token bites.** Read, modify the record by another path, then attempt the write with the stale token and assert 412. A token check that never fails is the vacuous case.
- **Prove ownership is checked on write, not only on read.** Read as the owner, then attempt the write as a different principal with a *valid* token. This must fail. If it passes, the token has silently become a credential and the design has been lost in implementation — this is the single most important test in the set.
- **Mutate every guard**, confirm RED, restore, clear `__pycache__`.
