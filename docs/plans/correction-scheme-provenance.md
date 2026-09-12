# Correction-scheme provenance — implementation plan

Status: **v2, reopened 2026-09-12; owner rulings applied 2026-09-13.**
Base: `main` at `c2633156` (main has since moved to `9ded15ec`; nothing
on this surface changed). Deployed Pi DB is at Alembic revision
`b6d80e36dcec` (measured: `SELECT * FROM alembic_version`).

v1 of this plan shipped in three merges — #436 (the plan), #439 (schema
`b6d80e36dcec`, resolver, warnings, admin attach-provenance route), #440
(frontend: software-titled expandable boxes). The owner reopened it
because the surface is still wrong, and the residual defect is in the
schema, not the page: the column v1 added keys on a **program**, and the
fact he names keys on a **program release**.

Governing records: `docs/decisions/0003-energy-correction-two-layer-architecture.md`
(reference layer vs. applied layer), `docs/literature_policy.md`, and the
house rule **`feedback_tckdb_is_sovereign`** — TCKDB decides its own
contracts; ARC and every other producer conform to TCKDB, never the
reverse. That rule shapes this document's structure: §2–§8 argue the
model from chemistry and from TCKDB's own code, and every fact about what
one particular toolchain happens to do, or about what this one deployment
happens to hold, is quarantined in §9 where it cannot be mistaken for a
design input.

Document shape follows `docs/plans/pressure-dependent-network-surface.md`:
measured facts with `file:line` anchors, a crux decision, PR slicing with
red-first criteria per PR, an explicit open-questions section.

## 0. The owner's rulings, quoted verbatim

### 0.1 The rulings that opened and reopened this plan

1. The question that opened it: what happens when two depositors provide
   different atom-energy or bond-additivity corrections for the same
   level of theory, software and version.
2. The literature discriminator: **use `source_literature_id`** — "a
   citation like Petersson 1998 is a real, citable identity rather than
   a depositor string."
3. Optionality: **"i think a lit source is not required but strongly
   advised."** Not `NOT NULL`. The plan must work when it is absent, make
   the absence visible, and make citing one the obvious default action.
4. The software gap: **"going back to AEC/BAC, they will differ per
   software combined with LoT so to have AEC and BAC on the LoT page but
   not indicating which software is problematic."** Framed as a
   scientific defect, not a display defect.
5. The display ruling that follows from (4): **"should be more like also
   expandable boxes for AEC and BAC but their names are the software or
   something."**
6. Reopening it, 2026-09-12: **"I still think the methods is a problem
   with how BAC and AEC are displayed since they are LoT and Software +
   Software Version defined."** The word v1 missed is **Version**.
7. And: **"and what's the correction scheme provenance?"** — asked of the
   two rows the archive actually holds.

### 0.2 The rulings that closed this plan's open questions, 2026-09-13

8. **Sovereignty, and it governs the rest**: *"those are those specific
   softwares, again TCKDB is not beholden to them if you get what I
   mean… Yes most likely they came from Gaussian in my Pi but it's not a
   rule TCKDB as a repo should follow. Only for the Pi DB atm."*
9. **The inferred program attribution is withheld**: *"I think NULL as we
   cannot assume Gaussian."*
10. **The tool releases behind the live rows are unknown, and that is
    final**: *"I dont know which ARC or ARKANE version was run to be
    honest."*
11. **Literature stays the discriminator**: *"I think literature should be
    discriminator… i cannot think why not right now."*
12. **`frequency_scale_factor` gets the same treatment** — a sibling
    revision of the same shape.
13. **`units` joins the identity index.**
14. **Provenance is corrected in place**, on the existing row.
15. **The trust rubric is extended**, with a reservation that becomes its
    own open question: *"I think yes for trust rubric but i dont like we
    do v1 when there is no released product version right."*

## 1. What v1 shipped, and what it left wrong

| v1 slice | Landed as | State today |
|---|---|---|
| A — schema + migration + resolver | #439, revision `b6d80e36dcec` | deployed; added `software_id`, `workflow_tool_release_id`, widened `uq_energy_correction_scheme_identity` |
| B — upload warnings | #439 (`provenance_warnings.py:656-736`) | deployed |
| C — admin attach-provenance route | #439 (`admin.py:790-864`) | deployed |
| D — frontend software-titled boxes | #440 (`LevelOfTheoryPage.tsx:257-323`, `CorrectionSchemePage.tsx`) | deployed |
| v1 §3 "no backfill, on either axis" | **reversed before merge** | `b6d80e36dcec:211-310` derives `software_id` from calculation data |

Three things to carry forward:

- **v1 chose `software_id` over `software_release_id` deliberately** (v1
  §3, "Deliberately not `software_release_id`"), on the ground that
  `FrequencyScaleFactor` uses the coarse grain and a new sibling should
  not introduce a third precision level. Ruling 6 answers that: version is
  part of the identity. Symmetry with a sibling was the wrong
  tie-breaker — it made the new column consistent rather than adequate,
  and ruling 12 now fixes the sibling instead (§6).
- **v1's "no backfill" decision was reversed at build time**, and the
  reversal is live data. Ruling 9 reverses it back. §4 removes the
  derived value rather than converting it.
- **v1's §4.3 admin route is the write path this plan needs** (ruling
  14). It exists; §5 changes its grain, not its existence.

## 2. Measured facts — TCKDB's own schema and code

These are facts about this repository and its contracts. They are design
inputs. Facts about particular producers and about this deployment's data
are in §9 and are not.

Code at `c2633156`; live schema read read-only from the Pi 2026-09-12/13.

### 2.1 The identity index has a program axis, no version axis, and no units axis

`backend/app/db/models/energy_correction.py:116-128`:

```python
Index(
    "uq_energy_correction_scheme_identity",
    "kind", "name", "level_of_theory_id", "version",
    "source_literature_id", "software_id", "workflow_tool_release_id",
    unique=True, postgresql_nulls_not_distinct=True,
)
```

`software_id` → `software.id` (`energy_correction.py:74-78`) — the
program row. There is no `software_release_id` column (confirmed against
the live table: 13 columns, no release). `units` is a column
(`energy_correction.py:93-96`, `EnergyUnit` = `hartree` | `kj_mol` |
`kcal_mol`, `common.py:1155-1158`) and is not in the index.

`FrequencyScaleFactor` has the identical program-grain limitation —
`software_id`, no release (`energy_correction.py:247-252`, index at
`:286-292`).

### 2.2 Every other provenance-bearing table in this schema keys on a release

`software_release` (`software_id`, `version`, `revision`, `build`,
`release_date`, `public_ref`) is referenced by `calculation`, `thermo`,
`statmech`, `kinetics`, `transport`, `network`, `network_solve`,
`molecular_property_observation` and `execution_environment_manifest`.
`energy_correction_scheme` and `frequency_scale_factor` are the **only**
two tables in the schema that reference `software` directly instead.
This is measured from the live `\d software_release` / `\d software`
referencing lists, not inferred.

### 2.3 The read layer fabricates a release because the tables cannot supply one

`energy_correction_schemes.py:429-450` returns a `SoftwareReleaseSummary`
with `software_release_id=0, software_release_ref=""`. Served live on a
real record:

```json
"software_release": { "software_release_ref": "", "software": "Gaussian", "version": null }
```

Two more sites do the same — `frequency_scale_factors.py:313-317` and
`statmech.py:720-721` (`# placeholder; FSF doesn't reference a release`).
`SoftwareReleaseSummary` declares both fields non-optional
(`schemas/reads/scientific_common.py:312-318`), so the read contract
wants a release and three call sites manufacture one. The frontend
consumes the object and deliberately refuses to link the ref
(`methodsApi.ts:112-122`, `LevelOfTheoryPage.tsx:222-225`) because it is
measured empty. This is the read layer reporting a missing column as an
empty value rather than as an absence.

### 2.4 The coarse grain already blocks a filter this API advertises

`GET /scientific/energy-correction-schemes/search?software_version=16`
returns `422 unsupported_filter`, live. The filter is declared on the
route (`routes/scientific/corrections.py:102`) and then explicitly
deferred (`energy_correction_schemes_search.py:67-75`: *"`software_version`
stays deferred because ECS only carries the bare software identity (no
release)"*). `software=Gaussian` works.

### 2.5 The resolver is value-comparing and unit-blind, and its remedy is unreachable

`resolve_or_create_scheme` (`energy_correction_resolution.py:128-228`)
looks a scheme up by the full index tuple, then calls
`_merge_scheme_params` (`:264`), which for every parameter key already
present calls `_assert_param_value_compatible`
(`energy_correction_resolution.py:239-262`):

```python
if abs(existing_value - supplied_value) <= _PARAM_VALUE_ABS_TOL:   # 1e-10
    return
raise ValueError(
    f"Conflicting {table_name} value for key='{key}': "
    f"existing={existing_value!r}, supplied={supplied_value!r}. "
    "Use a distinct energy_correction_scheme identity if these parameters "
    "represent a different correction library."
)
```

Two consequences, both load-bearing below:

- **It is unit-blind.** `units` is on the scheme row, never consulted
  here. A depositor who sends the same correction expressed in another
  energy unit resolves to the same row (units are not in the identity)
  and is then told their numbers conflict — e.g. a per-bond value of
  `-0.42 kcal_mol` against a stored `-0.00066932 hartree`. The message is
  wrong (it is not a different library) *and* its remedy is unreachable:
  the depositor cannot "use a distinct identity", because the thing that
  differs is not part of one.
- **It is far tighter than the physics.** `1e-10` absolute is below the
  level at which the same nominal method/basis differs between builds of
  one program, so any two genuinely different-build parameter sets that
  land on one identity are rejected rather than stored.

### 2.6 A write path for provenance already exists

`PATCH /admin/energy-correction-schemes/{ref}/provenance`
(`backend/app/api/routes/admin.py:790-864`), `require_admin`-gated,
accepting `source_literature` / `software` / `workflow_tool_release`,
resolving each through the same services the upload path uses, with
catalogue codes (`code_catalogue.py:1025-1040`) and ten tests
(`tests/api/test_admin_energy_correction_scheme_provenance.py`). Each
field is fill-only: a non-null column returns `409 …_already_set`
(`admin.py:768-787`, `:829-847`). It takes `SoftwareRef` — name only
(`fragments/refs.py:362-375`) — so it cannot express a release.

### 2.7 The archive already knows how to say "TCKDB worked this out, nobody deposited it"

Two precedents, both with the reasoning written into the model:

- `AtomMapSource` (`common.py:256-268`): `declared` | `inferred`, and
  *"The column carrying this token has no default. Defaulting to
  `declared` would manufacture human attribution for a row nobody wrote
  by hand."*
- `CalculationInputGeometrySource` (`common.py:568-589`): `deposited` |
  `extracted_from_artifact`, *"This value is what lets a reader tell 'the
  depositor said so' from 'TCKDB worked it out afterwards' without
  re-deriving it themselves."*

Ruling 9 means this plan writes no inferred value at all, so it needs no
such column (§3.5) — but the rule stands for anything that ever does.

### 2.8 Literature has no standalone deposit route, by design

`backend/app/api/routes/literature.py` (legacy, auth-gated) is `GET` list
and `GET /{id}`; `routes/scientific/literature.py` is `GET /{ref}` and
`GET /{ref}/records`; `routes/uploads.py` has no `/uploads/literature`.
`tckdb_schemas.literature`'s module docstring says so deliberately:
*"There is no standalone `/uploads/literature` route — that is a backend
concern."* Literature is created only as a nested `LiteratureUploadRequest`
inside a request that embeds one — which the admin route of §2.6 does.
`LiteratureKind` includes `dataset` and `webpage` (`common.py:839-846`),
and `docs/literature_policy.md:46-47` allows manual `kind` + `title`
submission with no DOI or ISBN.

## 3. The crux: key on a release, put units in the identity, record nothing TCKDB was not told

**Decision, in four parts:**

1. Replace `energy_correction_scheme.software_id` with
   `software_release_id`, nullable.
2. Add `units` to the unique identity index.
3. Write no inferred provenance: the revision carries **no backfill**,
   and dropping `software_id` removes the one inferred value now live.
4. Consequently, add **no** `software_attribution` column.

### 3.1 Why a correction can be release-specific — argued from the chemistry, not from any producer

An atom-energy set is a table of absolute single-atom electronic
energies at a given method and basis. Those values are the output of a
program's own numerics: its default integration grid, its SCF
convergence thresholds, its internal definition of a named basis set,
and its choice among the variants a functional name admits. All of these
are build-level decisions, and programs change them between releases.
The same is true of a fitted bond-additivity set, which is a regression
against reference energies computed the same way. So two parameter sets
carrying the same method, basis and program name can be two different
libraries, and the thing that separates them is the build.

A published correction table may instead be program-independent in
practice — quoted and reused across programs, with the paper carrying the
identity. That is not a second kind of column; it is a depositor who
records a citation and no release.

The three kinds this applies to are already named identically in three
places in the codebase — `provenance_warnings.py:92`
(`_SOFTWARE_SCOPED_SCHEME_KINDS`), `b6d80e36dcec:145-149`,
`LevelOfTheoryPage.tsx:255` — as `atom_energy`, `bac_petersson`,
`bac_melius`. `atom_hf` / `atom_thermal` / `soc` are physical reference
constants; no program computed them, so no program or release applies.

Two supporting arguments from TCKDB's own contracts, neither of which
depends on any external toolchain:

- **Internal consistency.** Every other provenance-bearing table keys on
  a release (§2.2). These two tables are the exceptions, and the
  exception is what forces three read sites to fabricate a release object
  (§2.3) and one advertised filter to 422 (§2.4).
- **The resolver's own tolerance.** At `1e-10` (§2.5), TCKDB already
  treats build-level numerical differences as a conflict. Without a
  release axis the only way a depositor can record two builds' sets is to
  differentiate them by hand in `name` or `version` — the depositor
  free-text fields this archive has ruled off public pages (#440,
  `correctionSchemeFormat.ts`). The schema currently forces the practice
  the display rules forbid.

### 3.2 One column, and a null that means one thing

`software_release` already stores releases with no version: the unique
key is `(software_id, version, revision, build)` with
`NULLS NOT DISTINCT`, and `resolve_software_release`
(`backend/app/services/software_resolution.py:49-103`) resolves
`{name: "X"}` with no version to exactly one such row, forever. So
"program known, build not stated" is a first-class value of
`software_release_id`, not a sentinel — distinct from a versioned release
and distinct from `NULL`.

| `software_release_id` | Meaning | For which kinds |
|---|---|---|
| a release with a version | that build produced these parameters | the three software-scoped kinds |
| a release with `version IS NULL` | that program produced them; the build is not stated | the three software-scoped kinds |
| `NULL` | no program attribution is recorded | the three software-scoped kinds |
| `NULL` | not applicable — this kind has no program | `atom_hf`, `atom_thermal`, `soc` |

The last two rows are the "one null, two meanings" hazard, and the model
already resolves it **without another column**: `kind` is `NOT NULL`, so
applicability is a total function of a column that is always present.
Every layer that needs the distinction already computes it from `kind`
alone — `provenance_warnings.py:698-702`, `b6d80e36dcec:145-149`,
`LevelOfTheoryPage.tsx:255`. Nothing reads the null and guesses.

**Rejected: a kind-conditional CHECK** (`software_release_id IS NULL` for
the three constant kinds). It forbids a depositor from recording a
constants table taken from one program's own documentation — a real thing
to want to record — and it makes a null illegible to anyone who does not
already know the rule.

**Rejected: keep `software_id` and add `software_release_id` beside it.**
Two columns can disagree, which Postgres cannot prevent without a trigger
or a composite FK onto a new unique index on `software_release (id,
software_id)` — real machinery to defend an invariant that one column
makes unfalsifiable. It also doubles the null vocabulary inside a
`NULLS NOT DISTINCT` unique index. The program is one join away
(`software_release.software_id`), which is how every other table already
gets it (§2.2).

### 3.3 `units` belongs in the identity (ruling 13)

The reasoning is in the resolver, not in taste. §2.5: a deposit of the
same correction in a different energy unit resolves onto the existing row
and dies with an error that misdescribes the situation and names a remedy
the schema does not offer. With `units` in the identity, that deposit is
what it actually is — a second row, same library, different unit
convention — and each depositor's own digits are preserved exactly as
sent.

**Rejected: convert on ingest to a canonical unit.** It would destroy the
source precision a depositor sent (these tables are routinely quoted to
full double precision), and it would make TCKDB re-derive a number a
depositor stated, which is the opposite of recording what it was told.

`public_refs.py`'s `_canonical_energy_correction_scheme` docstring
already calls `units` identity-relevant ("a different unit convention…
means a scientifically distinct scheme"). This makes that true rather
than aspirational, and closes the gap between the public-ref hash and the
constraint.

**Adding a column to a unique index can only split rows, never merge
them**, so this half of the change is safe for existing data by
construction. The replacement half (`software_id` → `software_release_id`)
is the direction that can merge, and §4 handles it explicitly.

### 3.4 Nothing is inferred (rulings 9 and 10)

TCKDB records what a depositor states. Where no one stated a program, the
column is `NULL` and the page says so. Where nobody knows which tool
release produced a row, that is the final answer until someone attests
otherwise — not a placeholder, not a TODO, not a most-likely value
carried with a caveat.

This has a concrete consequence for the live data and it is deliberate:
the inferred `software_id` currently on both live rows is **removed** by
this revision, because dropping the column drops the claim. See §9.3 for
what that looks like on the deployed pages.

### 3.5 Therefore no `software_attribution` column

v1's revision of this plan proposed a `deposited` |
`derived_from_archive` label in the shape of §2.7's two precedents. It
was justified entirely by the need to mark an inferred value. With ruling
9, no inferred value is ever written, so every row would carry the same
single reachable value, and a column with one reachable value states
nothing. It is also no longer needed to unblock the admin route: with the
inferred value gone, both live rows are `NULL` on program attribution, so
the existing fill-only guard (§2.6) has nothing to refuse.

**Dropped — and the rule it encoded is recorded here instead:** if any
future path (a backfill, an extractor, an LLM precheck) ever writes a
program attribution TCKDB inferred rather than received, it must carry a
source label at that time, and `AtomMapSource` /
`CalculationInputGeometrySource` are the template to copy. That is a rule
for whoever proposes such a path, not a column to carry now against a
path nobody is proposing.

### 3.6 What stays out of the identity

Nothing further is added. The index becomes `(kind, name,
level_of_theory_id, version, units, source_literature_id,
software_release_id, workflow_tool_release_id)`, still
`NULLS NOT DISTINCT`. Two schemes identical on all eight remain one row —
the residual ambiguity v1 identified (same kind, same level of theory,
neither cited, neither attributed) is still stated rather than
manufactured into two indistinguishable rows, and the warning that states
it (`provenance_warnings.py:704-734`) is unchanged.

## 4. Migration shape — one revision, no data step

`energy_correction_scheme` is an already-deployed table (real rows, and
dependent `applied_energy_correction` rows), so: **a new revision**,
`down_revision` = the head resolved at implementation time (deployed head
measured 2026-09-13 is `b6d80e36dcec`), both `upgrade()` and
`downgrade()` implemented, never an edit to `d861dfd60891` or to
`b6d80e36dcec`.

**`upgrade()`, in order:**

1. **Pre-flight collision check** (see below) — raise a legible error
   rather than let a later `CREATE UNIQUE INDEX` fail with a raw unique
   violation.
2. `op.add_column` `software_release_id` — `BigInteger`, nullable, FK
   `software_release.id`, `deferrable=True, initially="IMMEDIATE"`, with
   an **explicit constraint name**: the convention-derived name for this
   table/column pair exceeds PostgreSQL's 63-byte identifier limit, the
   same reason `b6d80e36dcec:173` names the workflow-tool FK by hand.
3. `op.drop_index("uq_energy_correction_scheme_identity", …,
   postgresql_nulls_not_distinct=True)`.
4. `op.create_index("uq_energy_correction_scheme_identity", …,
   ["kind","name","level_of_theory_id","version","units",
   "source_literature_id","software_release_id","workflow_tool_release_id"],
   unique=True, postgresql_nulls_not_distinct=True)` — same name, `units`
   added, `software_id` replaced by `software_release_id`.
5. `op.drop_column("energy_correction_scheme", "software_id")`.

**There is no step 6.** No backfill, no data migration, no derivation
(ruling 9). The revision only makes it *possible* to record a release; it
records none. This is the whole of the difference from v1's revision, and
it is why this one is easier to review: the only data it touches is the
data it deletes by dropping a column.

**What dropping `software_id` deletes, stated plainly.** Any value that
column holds — including the values `b6d80e36dcec:242-261` derived — goes
with it. That is the intended effect of ruling 9 and not a side effect:
the claim was never deposited, so removing the column is how TCKDB stops
making it. §9.3 records what that changes on the deployed pages.

**Collision analysis.** Under `NULLS NOT DISTINCT`, two rows collide when
every indexed column is equal, nulls included.

- Adding `units` can only **split** rows. A wider unique key is strictly
  more specific; no pair that was distinct can become identical.
- Replacing `software_id` with an all-`NULL` `software_release_id` can
  **merge**: two rows whose only difference was their program would
  become identical. That is the one real hazard in this revision, and
  with no backfill it is not hypothetical — the new column is null on
  every row at index-creation time.
- The pre-flight check is therefore mandatory, and it is the whole of
  step 1:

```sql
SELECT kind, name, level_of_theory_id, version, units,
       source_literature_id, workflow_tool_release_id, count(*)
FROM energy_correction_scheme
GROUP BY 1,2,3,4,5,6,7
HAVING count(*) > 1;
```

Any row returned means two schemes were distinguished *only* by the
program column this revision drops. The revision must abort with a
message naming those schemes' public refs — never merge them, never pick
one.

**Correction (review of #458):** an earlier draft of this section, and
the revision's first error message, told the operator to re-record the
program attributions as releases via §5's admin route before re-running.
That instruction cannot be carried out. When the check fires, the
database is still at `b6d80e36dcec`: `software_release_id` does not
exist, and every colliding row already has `software_id` set, so the
route returns `409 …_software_already_set` against the deployed code and
fails on a missing column against the new code. The achievable remedies
are to make the rows distinct on a column the new identity still checks
(most naturally `version` or `name`), or to delete the redundant scheme
and repoint its `applied_energy_correction` rows — a curation decision,
which is the honest reason the migration refuses to make it. The error
message now says this. Measured on the
deployed database 2026-09-13: **0 rows**, so this deployment upgrades
cleanly; the check exists for every other database, which this plan
cannot see and does not assume anything about.

**`downgrade()`:** drop the new index; re-add `software_id` (nullable,
left `NULL` — the down path restores the *column*, never a value, because
re-deriving one is exactly what ruling 9 forbids); re-create the index
with `software_id` and without `units`; drop `software_release_id`.
Losses, stated: every recorded release and every recorded unit
distinction. A downgrade attempted after two schemes have been
distinguished only by release, or only by units, fails on a genuine
unique violation — correct, and loud, rather than silently merging two
libraries.

**Regen steps.** ORM change → regenerate `backend/schema.dbml`
(`/generate-dbml`). Read-schema and filter changes →
`UPDATE_OPENAPI_GOLDEN=1 pytest tests/api/test_openapi_snapshot.py`.
`software_version` leaving `_DEFERRED_FILTER_FIELDS` changes an error
response the catalogue tests cover (`test_api_code_catalogue.py`).
`EnergyCorrectionSchemeRef.software` becoming a `SoftwareReleaseRef` is a
wire-contract change to `schemas/python/tckdb-schemas` → version bump per
`feedback_tckdb_client_version_bump`, plus `clients/python`'s typed
fields.

## 5. Recording provenance: the write paths

### 5.1 Upload

`EnergyCorrectionSchemeRef.software`
(`schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py:72`)
becomes a `SoftwareReleaseRef` (`fragments/refs.py:75-91` — `name`,
`version`, `revision`, `build`, and the existing composite-version
normalizer that already handles a banner-shaped `version` string).
`resolve_or_create_scheme` resolves it with
`resolve_software_release_ref` and adds `software_release_id` **and**
`units` to its `_match` chain (`energy_correction_resolution.py:186-198`),
so the resolver's lookup matches the index exactly. Without that, the
widened index sits unused and every upload keeps collapsing onto the
first row — the failure mode v1 named and avoided, still live here.

A depositor who knows only the program sends `{name: "X"}` with no
version, which resolves to the one version-less release row for that
program (§3.2). That is a complete, honest deposit, not a degraded one.

The literature warning is unchanged (advised, never required, ruling 3,
`provenance_warnings.py:695-696`). The software warning's message
(`:702`) should be reworded for the release grain, since "no program" and
"no build" are now different absences and only the first is worth
warning about.

### 5.2 Correcting an existing row (ruling 14)

`PATCH /admin/energy-correction-schemes/{ref}/provenance` (§2.6) is the
path. Two changes:

- `software: SoftwareRef` → `SoftwareReleaseRef`, resolved with
  `resolve_software_release_ref`.
- Nothing else. The fill-only guard **stays as it is**: with no inferred
  value ever written (§3.4), a non-null program attribution can only have
  come from a depositor, and refusing to overwrite it is exactly right.
  v1's proposed "deposited supersedes derived" relaxation is dropped
  along with the column that would have justified it.

**A re-deposit is not an adequate alternative**, and this is measured, not
argued: a corrected upload creates a *different* scheme row under the
widened identity (that is what the identity is for), leaving every
existing `applied_energy_correction` row pointing at the uncorrected
original. Nothing on this deployment is frozen by the accepted-science
triggers today (§9.1), so re-pointing them is mechanically possible, but
no route does it and building one is strictly more work than using the
route that already exists.

### 5.3 Literature stays the discriminator, and what that means when there is no paper (ruling 11)

Ruling 11 keeps `source_literature_id` as the discriminator between two
otherwise-identical parameter sets. Honouring it needs one thing said
plainly, because the obvious reading of it produces a false attribution:

**Cite what published these numbers, never what named the method.** A
scheme kind may be named after a published method while its stored
parameters are somebody's later refit at a different level of theory. The
paper that named the method is a real citation and would attach cleanly —
and it would be a false statement about where these values came from.
§9.2 records a live instance of exactly this trap.

**When the parameters have no paper**, the mechanism that honours ruling
11 is a `dataset`-kind literature row: `LiteratureKind.dataset`
(`common.py:839-846`) with a manual `kind` + `title` submission, which
`docs/literature_policy.md:46-47` allows with no DOI or ISBN — naming the
data file, repository and release the values were copied from. That is a
real, citable identity in the sense ruling 2 asked for, it discriminates
between two refits of the same method, and it does not require anyone to
pretend a paper published numbers it did not. There is no standalone
literature deposit route (§2.8) and none is needed: the admin route of
§5.2 embeds a `LiteratureUploadRequest`.

This is a policy statement, not new machinery — nothing in §4 changes for
it. It belongs here because "literature is the discriminator" is
unimplementable for a large class of real correction sets without it.

## 6. `frequency_scale_factor` — the sibling revision (ruling 12)

Same defect, same shape, separate revision, sequenced after the scheme
one so each is reviewable on its own.

- Replace `frequency_scale_factor.software_id` with
  `software_release_id` (`energy_correction.py:247-252`).
- Rebuild `uq_frequency_scale_factor_identity` (`:286-292`) as
  `(level_of_theory_id, software_release_id, scale_kind, value,
  source_literature_id, workflow_tool_release_id)`, still
  `NULLS NOT DISTINCT`. **No `units` member** — a scale factor is
  dimensionless, so §3.3's argument does not transfer.
- Same pre-flight collision check, adapted:
  `GROUP BY level_of_theory_id, scale_kind, value, source_literature_id,
  workflow_tool_release_id HAVING count(*) > 1`. Measured on the deployed
  database 2026-09-13: **0 rows**.
- No backfill, for the same reason (ruling 9).
- Delete the two remaining fabricated release summaries
  (`frequency_scale_factors.py:313-317`, `statmech.py:720-721`) in favour
  of the real join. After this revision, no site in the codebase
  synthesizes a `SoftwareReleaseSummary`.
- The same `software_version` filter deferral exists on the FSF search
  surface and is lifted the same way.

Until this lands, the ECS/FSF asymmetry is real and temporary; this plan
does not claim otherwise anywhere.

## 7. Trust rubric (ruling 15)

The rubrics live in `backend/app/services/trust/rubrics.py` as
`EvidenceRubric(name=…, version=…, record_type=…, checks=(…))` —
`COMPUTED_THERMO_V1` at `:1919`, `COMPUTED_STATMECH_V1` nearby, each
check an `EvidenceCheckSpec(name, kind, explain, runner)` with
`EvidenceCheckKind` ∈ `required` | `optional` | `warning`
(`trust/models.py:73-83`).

Add, as `optional` checks (they describe completeness; their absence must
never block a label — ruling 3's "advised, not required" applies to the
rubric exactly as it applies to the upload):

- `correction_scheme_software_release_present` — for each
  `applied_energy_correction` whose scheme is one of the three
  software-scoped kinds, the scheme carries a `software_release_id`.
  `not_applicable` when the record cites no scheme, or cites only
  constant-kind schemes.
- `correction_scheme_literature_present` — the cited scheme carries a
  `source_literature_id`. `not_applicable` on the same condition.

The existing `_check_frequency_scale_factor_present_if_applicable`
(`rubrics.py:998-1008`) is the pattern to copy for the applicability
branch, including its habit of returning `not_applicable` rather than
passing vacuously.

Scope note: these check whether the *cited scheme* documents itself, a
different axis from the existing checks, which ask whether a correction
was applied at all. Both are worth having; neither substitutes.

The owner's reservation about the `_v1` naming is **not** resolved here —
it is §11's new open question, and nothing in this section depends on its
answer, because `EvidenceRubric` already carries `name` and `version` as
separate fields and only renders them joined (`models.py:281-284`,
`qualified_name` → `computed_thermo@v1`).

## 8. Display

Owner rulings already honoured by #440 and unchanged: expandable boxes
named by the software, literature advised not required, no
depositor-typed labels on public pages. What this plan changes:

- **Titles gain the build for free.** `softwareLabel`
  (`frontend/src/domain/provenanceFormat.ts:16-24`) already renders
  `{name} {version}` with a stutter guard, so a box retitles itself the
  moment the release is real. No new formatting code.
- **The ref becomes linkable.** `software_release_ref` stops being `""`
  (§2.3), so the two deliberate refusals to link it
  (`LevelOfTheoryPage.tsx:222-225`, `methodsApi.ts:112-122`) become real
  links, and those comments are deleted rather than left describing a
  fixed defect.
- **"Software not recorded" becomes the honest state of the live rows.**
  The muted-pill treatment #440 already ships
  (`LevelOfTheoryPage.tsx:284`) is what both boxes will show after §4
  lands, because the inferred program name is removed (§3.4, §9.3). A
  version-less release renders the program name alone; no separate
  "version not recorded" text is needed, and inventing one would make an
  honest partial deposit look deficient.
- **No derived-provenance disclosure.** v1's revision of this plan
  proposed a sentence inside each box explaining that a program
  attribution was TCKDB's inference. With ruling 9 there is no inference
  to disclose, so the sentence is dropped and `evidence_summary`'s
  `has_software` (`energy_correction_schemes.py:139`) stays the simple
  presence flag it already is.

## 9. The live archive today — data, not design input

Everything in this section is a measurement of one deployment (the Pi,
`https://tckdb.homecalvin.com`) and of one producer's data files. Per
`feedback_tckdb_is_sovereign` it is recorded so the rulings can be
audited and the data can be fixed — **not** as justification for any
shape in §3–§7. Nothing above depends on it.

### 9.1 The two rows

```
 id |           public_ref           |     kind      |     name      | lot_id | source_literature_id | software_id | workflow_tool_release_id | version |  units   |                       note
  1 | ecs_q5potmkzrmm6ynh2behv5kbfdu | atom_energy   | atom_energy   |      4 |               (null) |           1 |                   (null) |  (null) | hartree  | Per-species AEC computed by Arkane.
  2 | ecs_5dzse4an2emubgyxge2dpj4ae4 | bac_petersson | bac_petersson |      4 |               (null) |           1 |                   (null) |  (null) | kcal_mol | Per-species BAC computed by Arkane (bac_type=p).
```

`level_of_theory.id = 4` is `b3lyp/def2tzvp`
(`lot_rrmbqrod3suvzkez2ta76hj76u`); `software.id = 1` is `Gaussian`.
Scheme 1 has 8 atom params, scheme 2 has 45 bond params, neither has
component params. Each is cited by **82** `applied_energy_correction`
rows (164 total, every one `record_review.status = not_reviewed`, so
nothing here is frozen by the accepted-science triggers). `literature`
holds **0 rows** archive-wide. Note the two rows already differ in
`units` — `hartree` vs `kcal_mol` — which is why §3.3's index change is
inert for them and must be tested against a constructed fixture instead.

### 9.2 Where these numbers came from, and the citation trap

Both parameter sets are bit-for-bit copies of one producer's curated
tables, verified against a local checkout of
`RMG-database/input/quantum_corrections/data.py`:

- Scheme 1's 8 atom energies == `atom_energies["LevelOfTheory(method='b3lyp2023',basis='def2tzvp',software='gaussian')"]`
  (`data.py:106-115`; H `-0.5010929786112002` through Br
  `-2574.144347409212`, all eight identical).
- Scheme 2's 45 bond corrections == `pbac["LevelOfTheory(method='b3lyp2023',basis='def2tzvp',software='gaussian')"]`
  (`data.py:1168-1215`; e.g. `C-H -0.23472079981166077`,
  `N=N 4.064303078434774`, all 45 identical with identical keys).

**The citation trap §5.3 warns about is live here.** Scheme 2's kind is
`bac_petersson`, and Petersson 1998 (`DOI 10.1063/1.477794`) is a real,
citable paper — but in that same producer's file it is cited only against
a `cbsqb3` key whose values are two-decimal published table entries
(`data.py:1006-1030`, e.g. `"C-H": -0.11`), while the values this archive
holds are sixteen-significant-digit refits at a different level of
theory. Citing Petersson 1998 for scheme 2 would attach a real paper to
numbers it never published. The same file also shows a single correction
set can be a mosaic of sources (`data.py:1014-1030`: one paper for some
bonds, another for others, and `"H-H": 1.1,  # Unknown source`), so "one
scheme, one citation" is not always expressible at all.

That producer's own key for these tables names a program and no build.
**This is a fact about that producer's file, not a constraint on TCKDB**
— it is why the honest recorded value for these two rows would be a
version-less release, and it is not a reason for TCKDB to model
corrections at program grain.

### 9.3 What the rulings do to these rows, and to the deployed pages

- `software_id = 1` (Gaussian) is **removed** when §4 drops the column.
  It was derived by `b6d80e36dcec:242-261` from this deployment's own
  calculation records, never deposited (ruling 9).
- `software_release_id` is `NULL` on both rows after the migration, and
  stays `NULL` until someone attests to a program.
- `workflow_tool_release_id` stays `NULL`. Ruling 10 settles it: nobody
  knows which ARC or Arkane release produced these rows. This plan
  records no placeholder and schedules no investigation.
- `source_literature_id` stays `NULL`. §5.3 describes the mechanism if
  and when someone deposits one.
- **On `/methods/lot_rrmbqrod3suvzkez2ta76hj76u`, both correction boxes
  will read "software not recorded"** where they today read "Gaussian".
  That is a visible regression in apparent completeness and a correction
  in accuracy, chosen deliberately by ruling 9. It should not be softened
  on the page, and nobody should re-add the inference later without
  re-opening ruling 9.

### 9.4 Why the derived value was wrong, not merely unproven

Recorded because it is the evidence behind ruling 9 and because the same
derivation will look tempting again.

`b6d80e36dcec:242-261` sets `software_id` from the single distinct
software across the `calculation` rows at the scheme's level of theory.
Measured per level of theory on this deployment:

| lot_id | method/basis | calculations | distinct software | distinct software_release | distinct workflow_tool_release |
|---|---|---|---|---|---|
| 1 | wb97xd/def2tzvp | 62 | 1 | 1 | 0 |
| 2 | MRCI+Davidson/aug-cc-pV(T+d)Z | 31 | 1 | 1 | 0 |
| 3 | CCSD(T)-F12/cc-pVTZ-F12 | 39 | 1 | 1 | 0 |
| 4 | **b3lyp/def2tzvp** | **416** | **1** | **1 (Gaussian 16 C.02)** | **10** |

- A release-grain derivation was equally available and equally unanimous
  — all 416 calculations at `lot 4`, and all 82 applied rows per scheme
  traced through `source_calculation_id`, resolve to one release.
- It would nonetheless have been **false**. The derivation answers "which
  program ran the calculations recorded at this level of theory" — which
  program *consumed* the correction. A scheme's software is meant to say
  which program *computed* the parameters, and §9.2 shows these were
  computed elsewhere, by someone else, with a build nobody here recorded.
  The coarse answer was right only by being coarse.
- `workflow_tool_release_id` is null because the same unanimity rule
  abstained (10 distinct releases at `lot 4`). That abstention concealed
  a second category error: a calculation's workflow-tool release is the
  orchestrator that ran the job, while a scheme's is meant to be the tool
  release whose curated data file was the source
  (`energy_correction.py:79-81`). Deriving one from the other asserts the
  wrong thing even when unanimous.

### 9.5 Frequency scale factors on this deployment

12 rows, 0 with a literature source. 10 of the 12 share
`(b3lyp/def2tzvp, Gaussian, fundamental, 0.999)` and differ only by
`workflow_tool_release_id` — which is already in the FSF identity index,
so they are legitimately distinct rows today. All ten carry the same
free-text `note`: `http://cccbdb.nist.gov/vibscalejust.asp` — an informal
citation sitting in prose on every row that lacks a structured one, and a
candidate for §5.3's `dataset`/`webpage` literature mechanism whenever
someone decides to deposit it. The other two (wb97xd/def2tzvp → 0.986,
ORCA CCSD(T)-F12 → 0.998) carry no note and no tool provenance.

## 10. Slicing into PRs

Each PR states red-first criteria and, where a test could pass
vacuously, the mutation that must break it.

**PR 1 — schema, migration, resolver, upload schema (one revision).** §4
plus §5.1. These cannot split: an index the resolver does not match is
inert, and an upload schema that cannot carry a release cannot populate
one.

Red first:
- The pre-flight check aborts the upgrade, with both offending public
  refs in the message, on a fixture holding two schemes that differ only
  by `software_id`; and does not fire on a fixture where they differ by
  anything else. *Mutation:* delete the check — the first fixture must
  then fail with a raw `IntegrityError` from `CREATE UNIQUE INDEX`, which
  is the failure the check exists to replace.
- After upgrade on a fixture in the live rows' shape, both rows keep
  their `public_ref`, parameters and dependent applied rows, and
  `software_release_id IS NULL`. *Mutation:* add any backfill at all —
  the NULL assertion must fail. **This is the criterion that pins ruling
  9 into the test suite**, and it replaces v1's backfill tests rather
  than joining them.
- Two schemes identical but for `units` (`hartree` vs `kcal_mol`) insert
  as two rows under the new index and are rejected under the old one.
  *Mutation:* drop `units` from the index — the two-row assertion must
  fail with `IntegrityError`.
- Two schemes identical but for `software_release_id` (a versioned
  release vs. the version-less release of the same program) insert as two
  rows. *Mutation:* drop the column from the index.
- A deposit of the same parameters in a second unit now resolves to a
  second row instead of raising from
  `_assert_param_value_compatible` — assert on the *absence* of the
  conflict error and on two rows existing, not merely on a 2xx.
- Two schemes identical on all eight indexed columns still collapse to
  one row through the resolver (unchanged behaviour;
  `test_energy_correction_resolution_provenance.py:135`).
- An upload supplying `{name, version, revision}` creates a row whose
  `software_release_id` resolves to that exact release; an upload
  supplying `{name}` alone resolves to the version-less release row for
  that program, and a second such upload reuses it rather than creating a
  second one.
- Upgrade → downgrade → upgrade round trip: columns the narrow schema
  still has are byte-identical, refs unchanged, and `software_id` comes
  back `NULL` rather than re-derived.
- `backend/tests/db/test_energy_correction_scheme_backfill.py` is
  **deleted**, not ported: it tests a derivation this plan removes.
  Deleting it is part of the PR, and the migration test
  (`test_energy_correction_scheme_provenance_migration.py`) is extended
  to cover the new revision.

Deploy: back up first (`backend/docs/deployment/migrations.md`), apply,
then re-read both rows and confirm the program attribution is gone.

**PR 2 — read layer.** Replace `_build_software_release_summary`'s
fabrication (`energy_correction_schemes.py:429-450`) with the real
`software_release` join; lift `software_version` out of
`_DEFERRED_FILTER_FIELDS` (`energy_correction_schemes_search.py:72-75`)
and implement it.

Red first:
- For a fixture scheme carrying a versioned release, the detail response
  returns a **non-empty ref that resolves**, and a `version` equal to the
  joined release's. *Mutation:* restore the `software_release_id=0,
  software_release_ref=""` literal — a test asserting non-emptiness alone
  would still pass, so the assertion must follow the ref to a real
  record.
- `search?software_version=16` returns matching schemes and no longer
  `422`s; a scheme on a version-less release reports `version: null` and
  is **not** returned by that filter.
- `search?software=<name>` still returns schemes whose release belongs to
  that program, via the release→software join.
- For a scheme with `software_release_id IS NULL`, `software_release` is
  `null` — never a synthesized object with an empty ref.
- OpenAPI golden regenerated; the `unsupported_filter` catalogue entry
  for this endpoint updated.

**PR 3 — admin route grain.** §5.2: `SoftwareRef` → `SoftwareReleaseRef`.
The guard is unchanged, and that is worth a test of its own.

Red first:
- PATCH with `{name, version, revision}` onto a scheme with
  `software_release_id IS NULL` sets exactly that release and returns its
  real ref.
- The same PATCH against a scheme that already carries one returns `409
  …_software_already_set` and leaves the value untouched. *Mutation:*
  remove the guard — this test must fail.
- A PATCH that would make the row collide with another under the widened
  identity returns 409 and writes nothing (`admin.py:849-858`, re-pinned
  at the new grain and with `units` now in the tuple).
- Literature and workflow-tool-release paths unchanged; the ten existing
  tests pass or are ported deliberately, not deleted.

**PR 4 — frontend.** §8.

Red first:
- A fixture scheme on release `{name: "X", version: "16", revision:
  "C.02"}` renders a box title containing the version and a
  `software_release_ref` that renders as a link. *Mutation:* revert to a
  software-only object — the title assertion must fail on the missing
  version.
- A fixture with `software_release: null` renders the muted "software not
  recorded" pill, never a blank, an em-dash, or `scheme.name`.
- A fixture on a version-less release renders the program name alone and
  no "version not recorded" text.
- Collapsed summaries still show real parameter counts from
  `evidence_summary`, preserving #440's guard against constant-looking
  summaries.

**PR 5 — trust rubric.** §7. Red first: a thermo record citing a
software-scoped scheme with no release reports the new `optional` check
as missing; one citing a scheme with a release reports it satisfied; one
citing only `atom_hf`/`atom_thermal`/`soc` schemes, and one citing no
scheme at all, both report `not_applicable`. *Mutation:* return a pass
instead of `not_applicable` for the no-scheme case — the
vacuous-pass test must fail. Neither check may change any label a record
can reach (assert a `well_supported` record stays `well_supported` with
both checks missing).

**PR 6 — the `frequency_scale_factor` sibling revision.** §6. Same
red-first shape as PR 1, minus the units criteria, plus: the 10 live rows
that differ only by workflow-tool release remain 10 distinct rows across
the upgrade, and no site in the codebase synthesizes a
`SoftwareReleaseSummary` afterwards (assert by grep in a test, the way
this repo already pins other absence rules).

**Not a PR: recording the real provenance of the live rows.** Ruling 10
says nobody knows it. There is nothing to schedule. If someone later
attests to a program, a build or a data-file citation, §5.2's route
records it in one call and no code changes are needed.

## 11. Open questions

### 11.1 New: rubric versioning names a product version that does not exist

Ruling 15, verbatim: *"I think yes for trust rubric but i dont like we do
v1 when there is no released product version right."*

The rubrics are declared as `EvidenceRubric(name="computed_thermo",
version=1, …)` (`trust/rubrics.py:1919`) and rendered as
`computed_thermo@v1` by `qualified_name` (`trust/models.py:281-284`);
the constants are spelled `COMPUTED_THERMO_V1` / `COMPUTED_STATMECH_V1`.
So the `v1` a reader sees is a *rubric* version, structurally independent
of any TCKDB release — but nothing on the page or in the API says that,
and TCKDB has no released product version for it to be confused with yet.

*The fact that settles it:* whether a reader of a public trust report is
meant to be able to tell "rubric revision 1" from "TCKDB version 1". If
yes, the fix is in how the pair is spelled and labelled wherever it
surfaces (a rendering and naming change, no schema impact, since `name`
and `version` are already separate fields). If the distinction does not
need to survive contact with a reader, the current spelling is fine and
the discomfort is cosmetic.

Deliberately not answered here: it is a question about how TCKDB versions
its own published artifacts, which is larger than this plan and should
not be settled as a side effect of a correction-scheme migration.

### 11.2 Closed, for the record

| Question | Ruling |
|---|---|
| Show the derived attribution, or withhold it? | Withhold — NULL (ruling 9); §3.4, §9.3 |
| What are the two rows' real provenance values? | Unknown, final (ruling 10); §9.3 |
| Is literature still the discriminator for a library with no paper? | Yes (ruling 11); mechanism in §5.3 |
| Does `frequency_scale_factor` get the same treatment? | Yes (ruling 12); §6 |
| `units` in the identity index? | Yes (ruling 13); §3.3 |
| Correct in place, or re-deposit? | In place (ruling 14); §5.2 |
| Extend the trust rubric? | Yes (ruling 15); §7, reservation → §11.1 |
| Should derived provenance be labelled as derived? | Moot — none is written (ruling 9); §3.5 keeps the rule for whoever needs it later |

## 12. Verification log

Read-only `psql` on the Pi (`docker exec -i tckdbv2-db-1 psql -U tckdb -d
tckdb`), 2026-09-12 and 2026-09-13: `alembic_version`; full row dump of
`energy_correction_scheme`; `\d` on `energy_correction_scheme`,
`frequency_scale_factor`, `software`, `software_release`,
`workflow_tool_release`, `applied_energy_correction`; `software`,
`software_release`, `workflow_tool_release` and `level_of_theory` row
dumps; per-`lot_id` counts of distinct `software_id` /
`software_release_id` / `workflow_tool_release_id` over `calculation`;
per-scheme counts of the same over `applied_energy_correction` joined
through `source_calculation_id`; parameter-row counts and the full 8-atom
and 45-bond dumps; `literature` count (0); `frequency_scale_factor` row
dump; `record_review` status counts; and both pre-flight collision
queries from §4 and §6 (0 rows each).

Live API, anonymous:
`GET /scientific/energy-correction-schemes/ecs_q5potmkzrmm6ynh2behv5kbfdu?include=literature`
(confirms `software_release_ref: ""`);
`…/search?software_version=16` (confirms `422 unsupported_filter`);
`…/search?software=Gaussian` (2 records).

Code read at `c2633156`: `backend/app/db/models/energy_correction.py`;
`backend/app/db/models/common.py` (`AtomMapSource`,
`CalculationInputGeometrySource`, `EnergyUnit`, `LiteratureKind`);
`backend/alembic/versions/b6d80e36dcec_…py`;
`backend/app/services/energy_correction_resolution.py`;
`backend/app/services/software_resolution.py`;
`backend/app/services/provenance_warnings.py`;
`backend/app/services/trust/rubrics.py` and `trust/models.py`;
`backend/app/services/scientific_read/energy_correction_schemes.py`,
`…_search.py`, `…/frequency_scale_factors.py`, `…/statmech.py`;
`backend/app/api/routes/admin.py`, `…/scientific/corrections.py`,
`…/literature.py`, `…/scientific/literature.py`,
`backend/app/api/code_catalogue.py`;
`schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py` and
`…/fragments/refs.py`; `frontend/src/pages/LevelOfTheoryPage.tsx`,
`…/CorrectionSchemePage.tsx`, `frontend/src/domain/provenanceFormat.ts`,
`frontend/src/api/methodsApi.ts`; and the four existing test modules
named in §10.

External, read-only, and quarantined to §9 by
`feedback_tckdb_is_sovereign`: a local checkout of
`RMG-database/input/quantum_corrections/data.py`.
