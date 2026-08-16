# A refusal's code belongs to the check, not to the layer that runs it

A depositor who mis-types the name of one of their own calculations can be
refused in two different places, and until now the two answered differently.
Which one fired depended on where the check happened to live — so the boundary
between the request schema and the workflow was a published API contract that
nobody had declared, that no test named, and that any refactor could move
silently. It had moved twice in one month (#218, #198).

This decision says what the contract actually is, so that the next contributor
has something to argue against instead of a pattern to copy at random.

## The rule

> **The refusal's *code and context* belong to the check, not to the layer that
> happens to run it: wherever a request-schema validator and a workflow seam can
> refuse the same mistake, both raise the same `CodedValidationError` — same
> `code`, same `context` keys — so that which layer fired is invisible to a
> client. Neither layer may be deleted because the other exists; a validator
> refuses earlier and offline, a seam refuses last and cannot be bypassed.**

## What was wrong

An upload payload never carries a database id (DR-0029 Requirement 1). Every
cross-reference inside it is a *local key* — a string the same request declared
on something it also sent. Two layers can catch a key that names nothing:

- a **request-schema validator**, the moment the body is parsed;
- the **workflow seam** (`app.services.local_key_resolution`), when the upload
  is written and the namespace is finally complete.

The seam's refusal was good: a machine-readable `code`, plus the offending
field, the key as the depositor wrote it, and the list of names that *would*
have worked. The validator's was a bare `ValueError`, which the error envelope
reports as the generic `request_validation_error` with an empty `context`.

The validator runs **first**. So on almost every upload route the better refusal
was unreachable — it existed, it was tested, and no depositor ever saw it.

Measured on one route before the change (`/uploads/conformers`, where a statmech
source key *is* validated at the schema layer and an applied-correction source
key is *not*):

| Same route, same repair | `code` | `context` |
|---|---|---|
| `statmech.source_calculations[0].calculation_key` typo | `request_validation_error` | `{}` |
| `applied_energy_corrections[0].source_calculation_key` typo | `applied_energy_correction_source_key_undeclared` | `{field, key, declared_keys}` |

Two depositors making the same mistake on the same endpoint received two
different contracts, decided by an implementation detail.

## Why not one of the simpler answers

**"Delete the duplicate validators; let the seam own it."** No. Those models run
inside `tckdb-client` and in offline contribution-bundle tooling with no server
in reach, and they refuse before a transaction opens. They are not duplication,
they are the same check available earlier and cheaper.

**"Delete the seam; the validator is authoritative."** Emphatically no. #218 and
#198 are both cases where a validator's coverage was *narrower than the
workflow's reach* — one member of a union guarded and its sibling not — and the
penalty was an unhandled 500. Guard coverage is precisely the thing that drifts.
The seam is the backstop because of it, not in spite of it.

**"Leave it; the message is readable either way."** This is the status quo, and
it makes the layer boundary a contract by accident. A contract nobody declared
and everybody can change by refactoring is the worst kind to have.

The justification in one line: *the two layers do not answer different
questions. They answer the same question at different times, and a client is
entitled to the same answer either way.*

## Where the codes have to live

`schemas/python/tckdb-schemas/tests/test_import_boundaries.py` forbids
`tckdb_schemas` from importing anything under `app`, statically and at runtime.
A wire-schema validator therefore *could not* name the seam's code even if it
wanted to.

So the code constants moved into the wire package, at
`schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py`, beside
`coded_error.py`, which is already there for exactly this reason. The backend
re-exports them from their previous homes, so no backend import moved:

| Code | Was defined in | Now re-exported from |
|---|---|---|
| `calculation_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `species_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `network_state_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `network_channel_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `micro_reaction_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `transition_state_key_undeclared` | `app/services/local_key_resolution.py` | same |
| `geometry_key_unresolved` | `app/services/local_key_resolution.py` | same |
| `statmech_calculation_key_undeclared` | `app/services/statmech_resolution.py` | same |
| `applied_energy_correction_source_key_undeclared` | `app/services/energy_correction_resolution.py` | same |

The import boundary is **not** weakened to achieve this. Nothing in
`tckdb_schemas` reaches into `app`; the dependency runs the other way, as it
already did for `CodedValidationError`.

`app.api.code_catalogue` names the new module as the `origin` of all nine, which
`test_every_origin_still_defines_its_code` checks by looking for the literal in
the named file.

The two layers do not merely *agree* on the context; they build it with the same
function, `undeclared_key_context`. That is deliberate. A rule that both layers
must report the same three facts is a rule a reviewer cannot check by reading
either layer alone, so it is enforced by construction instead.

## The corollaries a contributor can apply

1. **Both layers can refuse it** → one code, defined in
   `tckdb_schemas.local_key_codes`, imported by both. The validator raises
   `undeclared_key_error(SAME_CODE, <its existing sentence>, …)`.
2. **Only the workflow can refuse it** — it needs the database, or resolution
   order: ownership checks, `existing_*_id` chaining, role/type compatibility →
   seam only. Do not invent a validator for it.
3. **Only the schema can refuse it** — pure wire shape: "must reference a
   *scan*-type calculation", "a dependency may not name itself", "these keys
   must be unique", "this transition state does not belong to that micro
   reaction" → validator only, and it is a **different refusal** from
   "undeclared", with a different repair. It must not be folded into this
   family. These keep the generic code, correctly, because no seam offers a
   better answer for the same mistake.
4. **Never delete a seam because a validator covers it.** Coverage drifts.
5. **When a workflow serves a union** — species | transition state, computed |
   reported — ask whether the guard covers every member. That question found
   both #218 and #198.

## What a client sees

`message_prefix=False` everywhere in this family, so **`detail` does not move a
byte**. HTTP status is unchanged at 422. `code` becomes specific where it was
generic, and `context` gains `{field, key, declared_keys}` where it was empty.
No new codes were minted and no client enum member was added — all nine were
already catalogued and already exported in
`clients/python/src/tckdb_client/rejection_codes.py`.

Row ids never appear. They are the *values* of these key maps and they stay
there (DR-0028 Requirement 2); the observer in `backend/tests/error_body_observer.py`
watches every error body the suite produces and would say so if they did not.

### When more than one thing is wrong

`validation_detail_code` declines to promote a code when the detail list holds
more than one failure — naming one would hide that the request failed in more
than one place — and since #236 `validation_detail_context` *calls* that
function rather than repeating its test, so the context empties with it. A
generic code beside one failure's facts was the lie that fix removed.

In practice a single model raises once and stops, so two typos in one payload
usually still produce one coded failure. The genuinely plural case is two
sibling models each failing — two species, each with an unresolvable
`geometry_key` — and there the envelope reports `request_validation_error` with
`context == {}`, while `detail` carries both failures in full. Both behaviours
are pinned in `backend/tests/api/test_api_upload_key_and_role_contracts.py`.

## Consequences

- The break, and there is exactly one: a consumer branching on
  `code == "request_validation_error"` to mean "bad local key" stops matching on
  these paths. That branch was never correct — the code was generic — but it
  worked. ARC, the only known consumer, was grepped and contains no reference to
  `request_validation_error`, `validation_error`, `RejectionCode` or
  `rejection_code` anywhere in its Python, tests included.
- `tckdb-client` is unaffected: it maps 422 to `TCKDBValidationError` by
  *status*, not by code.
- A consumer already branching on `calculation_key_undeclared` starts receiving
  it on many more paths than the two it fired on before. Strictly an
  improvement; the shape of `context` is unchanged.
- Anyone adding a new local-key namespace now has a decision to make explicitly
  rather than by accident: which of the three corollary buckets it falls into.
