# Correction-scheme provenance — implementation plan

Status: draft v1. Base: `main` at `a21cb67e`. This plan extends
`docs/plans/methods-surface.md` (`plan-methods-surface-v2`) — its three
slices are built and deployed on `main` today: `MethodsIndexPage.tsx`,
`LevelOfTheoryPage.tsx`, `CorrectionSchemePage.tsx`,
`FrequencyScaleFactorPage.tsx`, routed at `/methods`, `/methods/:lotRef`,
`/methods/schemes/:ecsRef`, `/methods/frequency-scale-factors/:fsfRef`.
Governing decision records: `docs/decisions/0003-energy-correction-two-layer-architecture.md`
(reference layer vs. applied layer) and `docs/literature_policy.md`
(DOI/ISBN normalization, nested-only literature upload). Follows
`methods-surface.md`'s document shape: measured facts, a crux decision,
numbered slices with acceptance criteria, an owner's-rulings section.

**This plan grew twice while in progress.** It started as a literature-
discriminator question and picked up a second, larger defect along the
way: `energy_correction_scheme` has no software dimension at all, while
its sibling `frequency_scale_factor` does. Both are folded in below —
§0 records all three rulings verbatim, §1 measures both gaps together
(they turn out to share one root cause and, mostly, one fix), and the
schema/migration section proposes widening the identity on both axes in
one revision rather than two, because reviewing "the unique index on
this table changes" twice, a week apart, is worse than reviewing it once
correctly.

## 0. The owner's rulings, quoted verbatim

1. The question that opened this plan: what happens when two depositors
   provide different atom-energy or bond-additivity corrections for the
   same level of theory, software and version.
2. The literature discriminator: **use `source_literature_id`** — "a
   citation like Petersson 1998 is a real, citable identity rather than
   a depositor string."
3. Optionality, the load-bearing constraint on the literature side:
   **"i think a lit source is not required but strongly advised."** Not
   `NOT NULL`. The plan must work when it is absent, make the absence
   visible, and make citing one the obvious default action.
4. The software gap, raised after §2's crux was already being answered:
   **"going back to AEC/BAC, they will differ per software combined
   with LoT so to have AEC and BAC on the LoT page but not indicating
   which software is problematic."** Framed explicitly as a scientific
   defect, not a display defect.
5. The display ruling that follows from (4), verbatim: **"should be
   more like also expandable boxes for AEC and BAC but their names are
   the software or something."** Each correction scheme becomes its own
   collapsible box on the level-of-theory page, and software — not the
   depositor's free-text `name` — is what titles the box.

## 1. Measured facts

All against the live archive (`https://tckdb.homecalvin.com`, anonymous,
2026-09-09) and the code at the base commit above.

### 1.1 What is deposited today

| Table | Rows | With `source_literature_id` |
|---|---|---|
| `literature` | **0** | n/a — nothing exists to cite |
| `energy_correction_scheme` | 2 | 0 |
| `frequency_scale_factor` | 12 | 0 |

The two `energy_correction_scheme` rows, re-verified live via
`GET /energy-correction-schemes/search?has_corrections=true`:

| `energy_correction_scheme_ref` | `kind` | `name` | `version` | `level_of_theory_ref` | `has_literature_source` |
|---|---|---|---|---|---|
| `ecs_q5potmkzrmm6ynh2behv5kbfdu` | `atom_energy` | `atom_energy` | `null` | `lot_rrmbqrod3suvzkez2ta76hj76u` (b3lyp/def2tzvp) | `false` |
| `ecs_5dzse4an2emubgyxge2dpj4ae4` | `bac_petersson` | `bac_petersson` | `null` | `lot_rrmbqrod3suvzkez2ta76hj76u` (b3lyp/def2tzvp) | `false` |

`name` is identical to `kind` on both rows and `version` is blank on
both — today's only two rows are distinguishable *only* by `kind`, and
(§1.6) neither carries a software identity either, so nothing on either
row would survive as a distinguishing label once `name` is correctly
taken off public pages.

The atom-energy scheme's 8 element values are bit-for-bit identical to
RMG's `input/quantum_corrections/data.py` `atom_energies` table for
`b3lyp2023/def2tzvp` (re-confirmed: H = `-0.5010929786112002` in both).
This is a finding from reading two independent sources side by side, not
a citation either source recorded — see §7 for why that distinction
matters to what this plan is allowed to write into the database, and why
it now applies to a software claim as well as a literature one.

`frequency_scale_factor`, re-verified live via
`GET /frequency-scale-factors/search?used_by_statmech=true`: 12 rows, 0
with `has_literature_source: true`. 10 of the 12 share `(level_of_theory
= b3lyp/def2tzvp, software = Gaussian, scale_kind = fundamental, value =
0.999)` and are distinguished *only* by `workflow_tool_release_id` — 10
distinct `wfr_…` refs, all reporting the same `version: "1.1.0"` string
(so "differ only by ARC release" means ten distinct `workflow_tool_release`
rows, not ten distinct version strings). All ten carry the same free-text
`note`: `"http://cccbdb.nist.gov/vibscalejust.asp"` — an informal citation
already present as prose, on every one of the rows that lacks a
structured one. The other 2 rows (wb97xd/def2tzvp → 0.986, CCSD(T)-F12 →
0.998) have no `note` and no workflow-tool-release provenance at all.
All 12 rows *do* carry a `software` (bare `Software`, not a release —
§1.6 below).

### 1.2 The DB constraint, and a documented inconsistency that already argues for widening it

`energy_correction_scheme.__table_args__`
(`backend/app/db/models/energy_correction.py:97-106`):

```python
Index(
    "uq_energy_correction_scheme_kind_name_lot_version",
    "kind", "name", "level_of_theory_id", "version",
    unique=True, postgresql_nulls_not_distinct=True,
)
```

Created in the baseline migration
(`backend/alembic/versions/d861dfd60891_create_intial_schema.py:307`,
dropped at `:1837`) via `Base.metadata.create_all()` — this table holds
real, live data on the deployed Pi, so it is an **already-deployed
table** under the migration rules: any index change is a **new
revision**, never an edit to `d861dfd60891`.

`FrequencyScaleFactor`'s own unique index is already wider —
`(level_of_theory_id, software_id, scale_kind, value,
source_literature_id, workflow_tool_release_id)`, also
`nulls_not_distinct` (`energy_correction.py:259-270`) — and its class
docstring states the design intent plainly: *"two rows can legitimately
have the same (LOT, software, scale_kind) with different values if they
come from different sources."* `energy_correction_scheme` was never
given the equivalent fields, on either axis.

**The public-ref layer already assumes the wider literature identity and
says so.** `app/services/public_refs.py:382-404`,
`_canonical_energy_correction_scheme`, verbatim:

> "The database uniqueness constraint and `resolve_or_create_scheme`
> both dedup on `(kind, name, level_of_theory_id, version)`;
> `source_literature_id` and `units` are not part of that key but a
> different value of either still means a scientifically distinct
> scheme (different citation, different unit convention). Two rows that
> the resolver treats as distinct must therefore get distinct refs —
> otherwise the `ix_energy_correction_scheme_public_ref` unique index
> trips on insert."

This docstring is describing a state that **cannot currently occur**:
the resolver (§1.3) never creates a second row with the same
`(kind, name, lot, version)` and a different `source_literature_id` — it
finds and reuses the first one, literature included. The public-ref
hash was built anticipating a widened identity that the unique index and
the resolver were never updated to match. That gap — not a hypothetical
— is direct, code-level evidence for the literature half of this plan's
index change (§3). No equivalent docstring exists yet for software,
because no software column exists yet to write one about — §1.6 is the
new-in-this-revision half of the same argument.

### 1.3 What the resolver actually does today

`resolve_or_create_scheme`
(`backend/app/services/energy_correction_resolution.py:123-181`):

1. Looks up an existing scheme by `(kind, name, level_of_theory_id,
   version)` — **`source_literature_id` is not part of the lookup**, and
   there is currently no software field to look up at all.
2. If found, **reuses the row.** `ref.source_literature` is never even
   read on this branch — a depositor who supplies a citation for a
   scheme whose identity already exists gets it **silently discarded**,
   no error, no warning, nothing persisted. This is a real, present-day
   gap: today, there is no way to add a citation to an existing scheme
   by uploading one, even when a depositor tries. The same silent-drop
   shape will apply to a supplied software identity the moment one is
   added (§3), unless the lookup is widened to match, which §3 makes a
   companion requirement, not an afterthought.
3. If not found, resolves `ref.source_literature` via
   `resolve_or_create_literature` and creates a new row.
4. Either way, calls `_merge_scheme_params`
   (`energy_correction_resolution.py:227-300`), which is the part of
   this system that already answers half of the owner's original
   question: for each parameter key, a new value within
   `1e-10` absolute tolerance of the existing one is a silent no-op; a
   **conflicting** value raises `ValueError` — surfaced as a 422, per
   the module's own comment, "rather than silently overwriting or
   ignoring the new value."

So: **"same kind+name+lot+version, different numbers" is already
rejected today** — not by the unique index (which never even sees a
duplicate insert attempt, because step 1 finds the row first), but by
this application-level value-conflict check. `test_repeated_upload_
reuses_scheme_row` and `test_note_does_not_affect_scheme_identity`
(`backend/tests/workflows/test_computed_species_upload.py:2729-2851`)
pin exactly this dedup key and confirm `note` (and, by the same lookup
logic, `source_literature_id`) is first-write-wins, never merged.

**"Different `name` or `version`, both uncited" is not rejected at
all** — two rows coexist with nothing but free-text `name`/`version` to
tell them apart, and `name` is exactly the field the owner has ruled off
public pages. Per §0.4, that is only half the story: a fraction of what
today looks like an unresolvable collision is not a collision at all —
it is two different programs' outputs for the same method and basis,
which the schema currently has no way to say. §2 answers both.

### 1.4 Literature: works when embedded in a new row, has no update path, and has no standalone deposit route anywhere

`EnergyCorrectionSchemeRef.source_literature: LiteratureUploadRequest |
None` already exists
(`schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py:44`)
and is wired to `resolve_or_create_literature`
(`energy_correction_resolution.py:36,165-167`) — this mechanism is not
broken, and it is tested for the sibling FSF path
(`backend/tests/workflows/test_statmech_upload.py:598-661`, asserting
`fsf.source_literature_id is not None` and the resolved title). It has
just never been exercised for a scheme, because §1.3 step 2 means it
only fires the first time a given `(kind, name, lot, version)` is
created — which already happened, uncited, for both live rows.

There is **no standalone way to deposit literature at all** — confirmed
by reading every literature-touching route in the app, not inferred:

- `backend/app/api/routes/literature.py` (legacy, auth-gated): `GET`
  list, `GET /{id}` — no `POST`.
- `backend/app/api/routes/scientific/literature.py` (public read
  surface): `GET /{ref}` detail, `GET /{ref}/records` (inverse lookup)
  — no list/search endpoint at all, and no `POST`.
- `backend/app/api/routes/uploads.py`: no `/uploads/literature` route.
  `tckdb_schemas.literature`'s own module docstring says so by design:
  *"There is no standalone `/uploads/literature` route — that is a
  backend concern."* Literature is created only as a nested fragment,
  resolved inline, inside whichever request embeds a
  `LiteratureUploadRequest` (thermo, kinetics, conformer, network,
  transport, transition-state, computed-reaction, and — since §1.4
  above — energy-correction uploads).
- There is also **no update route for an existing
  `energy_correction_scheme` row.**
  `backend/app/api/routes/energy_corrections.py` is `list`/`get` only
  for schemes, FSFs, and applied corrections — no `PATCH`/`PUT`
  anywhere. An `EnergyCorrectionSchemeUpdate` Pydantic schema already
  exists at the entity layer
  (`backend/app/schemas/entities/energy_correction.py:136-143`,
  carrying `source_literature_id: int | None` among other fields) but is
  **unrouted** — nothing calls it. This is a smaller gap to close than
  "build an update path from scratch" (§4.3).

**Net answer to "does the literature subsystem work":** creation works,
narrowly, the first time a scheme identity is deposited. There is no way
— today, on any surface — to attach a citation to a scheme that already
exists uncited. That gates §7's answer for the two live rows.

### 1.5 The read side already computes the right thing; the frontend already fetches it and never shows it

`backend/app/services/scientific_read/energy_correction_schemes.py`
already reports `has_literature_source` (evidence summary) and
`has_literature` (available-sections) correctly, and resolves a full
`LiteratureSummary` under `include=literature` — verified live: every
record above correctly reports `"has_literature_source": false`,
`"literature": null`. The methods-surface frontend's typed API client
(`frontend/src/api/methodsApi.ts:38-39,99,105,111,139,148,163`) already
parses this field on both the scheme and FSF response types, and
`useCorrectionScheme`/`useLevelOfTheory`/the FSF equivalent already
request `include=…,literature` (`methodsApi.ts:331,344`).

**None of the three shipped pages render it.** Read in full:
`frontend/src/pages/CorrectionSchemePage.tsx`,
`frontend/src/pages/FrequencyScaleFactorPage.tsx`, and the correction-
scheme section of `frontend/src/pages/LevelOfTheoryPage.tsx` — zero
references to `literature` or `has_literature_source` in any of the
three. The data is fetched and silently dropped on the client, the
mirror image of §1.3's server-side silent drop.

**`CorrectionSchemePage.tsx` also renders `scheme.name` — depositor free
text — as its `<h1>` and its breadcrumb label**
(`CorrectionSchemePage.tsx:56,66`). This is a live violation of the
house rule against depositor-typed labels on public pages, independent
of this plan's own scope, and it is exactly the field the owner's
literature ruling and software ruling both replace as a discriminator.
`LevelOfTheoryPage.tsx`'s own correction-scheme section already does
better — `SCHEME_KIND_LABELS[scheme_kind] ?? scheme.name`
(`LevelOfTheoryPage.tsx:228`) prefers a kind-derived, machine-controlled
label and only falls back to `name` when no mapping exists — but that
same section already loops one block per scheme
(`LevelOfTheoryPage.tsx:225-251`, keyed on `energy_correction_scheme_
ref`, not collapsed), so it is structurally ready for two schemes of the
same kind. §6 replaces this section's rendering entirely per §0.5.

### 1.6 The software gap: measured, not inferred

Read `backend/app/db/models/energy_correction.py` in full for both
classes side by side. `FrequencyScaleFactor` (lines 200-270):

```python
level_of_theory_id: Mapped[int] = mapped_column(..., nullable=False)
# Software dimension: same LOT in Gaussian vs QChem can yield different factors
software_id: Mapped[Optional[int]] = mapped_column(
    BigInteger, ForeignKey("software.id", ...), nullable=True,
)
...
workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(...)
```

Its unique index: `(level_of_theory_id, software_id, scale_kind, value,
source_literature_id, workflow_tool_release_id)`, `nulls_not_distinct`.

`EnergyCorrectionScheme` (lines 44-106): `id`, `kind`, `name`,
`level_of_theory_id` (nullable), `source_literature_id` (nullable),
`version`, `units`, `note`. **No `software_id`, no `software_release_id`,
no `workflow_tool_release_id` — no software-shaped column of any kind.**
Confirmed by grep, not just by reading the class: `grep -n software
backend/app/db/models/energy_correction.py` matches only inside the FSF
class. The read side matches — `grep -n software
backend/app/schemas/reads/scientific_energy_correction_scheme.py
backend/app/services/scientific_read/energy_correction_schemes.py`
returns nothing at all. Every layer — model, read schema, read service,
public-ref hash, frontend type — is silent on software for
`energy_correction_scheme`, and only for that table; every other
provenance-bearing table this plan touches (`FrequencyScaleFactor`,
`Calculation`) already carries one.

**The upload-schema precedent for exactly this fix already exists, on
the FSF side.** `FreqScaleFactorRef`
(`schemas/python/tckdb-schemas/tckdb_schemas/fragments/refs.py:378-431`):

```python
class FreqScaleFactorRef(SchemaBase):
    level_of_theory: LevelOfTheoryRef
    scale_kind: FrequencyScaleKind = FrequencyScaleKind.fundamental
    value: float = Field(gt=0)
    software: SoftwareRef | None = None
    source_literature: "LiteratureUploadRequest | None" = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None
```

with its own docstring already stating the identity tuple
`(level_of_theory, software, scale_kind, value, source_literature,
workflow_tool_release)` and the exact resolution policy §3 needs to
copy: *"If a workflow tool's curated data file is the proximate source,
pass `workflow_tool_release` and put any descriptive file/source
reference in `note`."* `EnergyCorrectionSchemeRef` has no equivalent
`software` or `workflow_tool_release` field today — this is the upload-
side twin of the model-side gap just above, and the fix is to add the
same two fields in the same shape, not to invent a new pattern.

**Scope check: which scheme kinds does this even apply to.** The
shipped frontend already drew this line for a different reason and drew
it correctly. `LevelOfTheoryPage.tsx:210`:

```ts
// atom_hf/atom_thermal/soc are element-only, never LOT-scoped
const LOT_SCOPED_SCHEME_KINDS = ["atom_energy", "bac_petersson", "bac_melius"] as const
```

`atom_hf` (atomic enthalpy of formation), `atom_thermal` (thermal
contribution), and `soc` (spin-orbit coupling) are, per DR-0003 and the
RMG reference file (`methods-surface.md` §2.5.4), physical/reference
constants — CODATA- or NIST-style values, not outputs of a specific
program run. `atom_energy`, `bac_petersson`, and `bac_melius` are the
three kinds whose numeric values are literally *computed by* a specific
program at a specific level of theory — the owner's "AEC/BAC... differ
per software" is about exactly these three, and only these three. The
software column, its warning treatment (§4), and its display treatment
(§6) all apply to this same three-kind set the frontend already named,
not to all eight `EnergyCorrectionSchemeKind` values. A software-less
`atom_hf`/`atom_thermal`/`soc` scheme is not a gap at all — it is the
correct, honest shape for a scheme kind the software axis does not
apply to (`NOT_APPLICABLE`, in `provenance_warnings.py`'s own vocabulary
— see §4).

**Origin of the two live rows — checked, not assumed.** The owner asked
whether software is recoverable from the ingestion path. Searched every
`.py` file under `backend/scripts/` (both `arc_ingestion/` and
`pdep_ingestion/`) for `atom_energy`, `bac_petersson`,
`EnergyCorrectionSchemeRef`, and the note text `"Per-species AEC
computed by Arkane"` that the live rows actually carry: **zero matches
outside models, schemas, and tests.** `arc_ingestion/extractor.py` and
`builder.py` handle exactly one energy-correction-adjacent thing today —
detecting and attaching `energy_correction_note` when Arkane's log
reports **missing** atom energy corrections
(`extractor.py:276-294`) — they contain no code path that builds an
`EnergyCorrectionSchemeRef` payload with parameters, software, or
literature at all. **The two live scheme rows were not created by any
ingestion script currently in this repository.** They were deposited by
some other, uncommitted process. Software identity is not recoverable
from the codebase. What is available, same evidentiary weight as the
literature correspondence in §1.1 and treated the same way in §7:

- RMG's own dict key for the matching table is a `repr()` string that
  names the software directly — `"LevelOfTheory(method='b3lyp2023',
  basis='def2tzvp',software='gaussian')"` (`methods-surface.md` §2.3,
  §2.7). The bit-for-bit numeric match is to a key that says
  `software='gaussian'`.
- Every calculation this archive has ever recorded at
  `lot_rrmbqrod3suvzkez2ta76hj76u` (b3lyp/def2tzvp) — all 416 of them —
  runs Gaussian 16 and nothing else (`methods-surface.md` §2.1's
  cross-checked table: `× Molpro = 0`, `× ORCA = 0` at this LOT). If
  these two schemes were in fact produced by an Arkane run over this
  archive's own species, the run that produced them touched only
  Gaussian.

Both facts point the same direction and neither is a recorded fact on
the scheme row itself. §7 treats this exactly like the literature
correspondence: real, worth stating as context, not a license to write
`software_id` onto a deployed row via migration.

## 2. The crux: what distinguishes two schemes of the same kind and level of theory

**Decision: three layers, in order of how much they can actually
resolve. Software first — because for the three kinds where it applies,
it is frequently the entire explanation and not a "collision" at all.
Literature second, for what software leaves unresolved. Stated absence
last, and nothing invented beyond that — no curation "selected"/
"preferred" flag is added to `energy_correction_scheme`.**

### 2.1 Software resolves most apparent collisions, because it usually isn't one

The owner's own framing (§0.4) is the correct first cut: "two schemes on
one level of theory may not be a disagreement at all — they may be one
for Gaussian and one for ORCA." Once `software_id` exists (§3), the
identity `(kind, name, level_of_theory_id, version, source_literature_id,
software_id, workflow_tool_release_id)` means two `atom_energy` schemes
at the same LOT with different `software_id` are not a collision at
all — they are two different, individually correct, coexisting facts,
exactly the way `FrequencyScaleFactor` already treats "same LOT,
different software" today. This is new information the schema did not
have before this plan, and it is why the crux got smaller, not just
better-labeled, once §0.4 landed.

### 2.2 What's left once software agrees (or is absent on both sides) is the literature question already answered

For same-kind, same-LOT, same-software (or both-software-unknown)
schemes, software cannot discriminate and the question reduces to the
one this plan opened with. **A curation/selection overlay
(conformer-style) is rejected**, for the same reason regardless of which
axis raised it: `energy_correction_scheme` is DR-0003's *reference
layer* — it dedupes on scientific identity the way `species`/
`level_of_theory` do, not the way a per-entry result accumulates. The
house rule this repo already enforces
(`feedback_identity_vs_result_tables`: "Identity tables dedupe, result
tables are append-only; no preferred/selected semantics in DB") applies
directly: a "which scheme is the real one" flag on an identity/reference
table would invent exactly the semantics that rule forbids.
`applied_energy_correction` already carries an explicit `scheme_id` FK
per row (DR-0003) — no consumer of a correction ever needs "the" scheme
for a LOT, only the specific one a specific deposit cited. The ambiguity
that's left is a *browsing* problem (a human reading the LOT page),
never a *correctness* problem.

**Refusing the ambiguity at upload** is rejected as a hard block, for
the reason §0.3 makes load-bearing for literature specifically: making a
second uncited scheme upload fail outright is a de facto `NOT NULL` on
`source_literature_id` in the one case it would bind hardest,
contradicting "not required." Software gets a different answer on this
point — see §2.3.

**The public ref is the honest discriminator, and it already is one.**
Every `energy_correction_scheme` row already has a stable, content-
derived `ecs_…` ref. It is opaque, which is a real cost — but "opaque
and honest" is strictly better than "readable and fabricated," and this
repo's rule against asserting from absence rules out manufacturing a
friendlier label from nothing. §6 pairs it with the scheme's real
`created_at` timestamp and, once one exists, its real citation — never a
synthesized label.

### 2.3 Software is not "copy the literature answer" — it is more load-bearing, and here is the concrete reason why

The owner asked for this explicitly, not a restatement of §0.3. Three
concrete asymmetries, none of them hypothetical:

1. **Unknowability is a legitimate, permanent state for literature; it
   is not for software, going forward.** A computed correction scheme
   frequently has no paper to cite — that is exactly why
   `provenance_warnings.py`'s existing branching logic treats computed
   origins differently from literature-sourced ones (§4). But the
   program that computed an AEC/BAC scheme's numbers is *always* known
   to whatever pipeline produced them — Arkane/ARC always knows what it
   ran. §1.6 already found the concrete case: this archive's own
   ingestion tooling (`arc_ingestion/`) doesn't currently forward that
   information into the scheme payload at all, which is a real,
   nameable gap in the *pipeline*, not an inherent gap in the *world*
   the way an uncited historical paper can be. §5 names closing it as
   its own slice.
2. **It is what the display now keys on.** §0.5 makes the software
   identity the literal title of the box a reader sees — not a footnote,
   not a disclosure one click deep. An absent citation degrades a box's
   completeness; an absent software identity degrades the box's own
   name (§6 specifies exactly how that degradation is written so it
   still reads as an absence, never as an identity).
3. **It is symmetric with `FrequencyScaleFactor` today, and asymmetric
   with it if left out.** `FrequencyScaleFactor` already treats software
   as identity-bearing (§1.6). Leaving `energy_correction_scheme`
   without it is not a neutral omission — it is the one correction
   table in this archive that cannot say the thing its sibling table
   already says routinely.

None of this changes the DB-level answer from §0.3's playbook: nullable,
non-blocking, matching the fact that the two live rows and any future
backfilled/legacy deposit genuinely may not have it. What changes is the
warning's framing (§4) and, concretely, the display consequence (§6) —
software gets the *stronger of two treatments this repo already has for
optional-but-important data*, not a new third mechanism.

### 2.4 Two schemes, same kind, same LOT, same software, both uncited

Named because it is the one case left un-discriminated by every axis
this plan adds. §2.2's answer stands: state the absence — this specific
sentence, on both entries: "This scheme and `ecs_<other ref>` share a
kind, level of theory, and recorded software, and neither carries a
citation, so the archive cannot say whether they are the same correction
deposited twice or two genuinely different sets of numbers." One honest
sentence, not a resolved answer, because the archive does not have one.

## 3. Schema and migration: one widened identity, both axes together

**New columns on `energy_correction_scheme`**, mirroring
`FrequencyScaleFactor` exactly:

```python
software_id: Mapped[Optional[int]] = mapped_column(
    BigInteger,
    ForeignKey("software.id", deferrable=True, initially="IMMEDIATE"),
    nullable=True,
)
workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
    BigInteger,
    ForeignKey(
        "workflow_tool_release.id",
        deferrable=True, initially="IMMEDIATE",
        name="fk_energy_correction_scheme_workflow_tool_release_id",
    ),
    nullable=True,
)
```

Both nullable at the DB level — this must stay true to represent the two
live rows honestly (§1.6, §7) and any future legacy import, and matches
the fact that `FrequencyScaleFactor.software_id` is *also* nullable
despite carrying the identical "differs per software" comment (§1.6): the
codebase's own precedent for this exact scientific situation is
nullable-plus-identity-bearing, not `NOT NULL`.

**Deliberately not `software_release_id`.** `Calculation` uses
`software_release_id` (version-precise); `FrequencyScaleFactor` uses the
coarser `software_id` (bare `Software`, no version — the correction-
reads spec doc already documents this as FSF's shape: "FSF only carries
`software_id`, not a release — version is null"). This plan mirrors FSF,
per the owner's own comparison, rather than introducing a third
precision level. Flagged in §8 as an open question — the coarser grain
means "Gaussian 16" and "Gaussian 09" AEC schemes would be
indistinguishable by software alone, the same coarseness FSF already
lives with — but changing that is a decision about FSF's own grain
first, and picking a different grain for its new sibling would be an
inconsistency this plan should not introduce unilaterally.

**New unique index, replacing the current one:**

```sql
CREATE UNIQUE INDEX uq_energy_correction_scheme_identity
    ON energy_correction_scheme
    (kind, name, level_of_theory_id, version,
     source_literature_id, software_id, workflow_tool_release_id)
    NULLS NOT DISTINCT;
```

**What it newly permits:**

- Two schemes with the same `(kind, name, lot, version)` and different,
  non-null `source_literature_id` — the citation case §0.2 asked for.
- Two schemes with the same `(kind, name, lot, version)` and different,
  non-null `software_id` — the software case §0.4 asked for, and per
  §2.1 the one that most often means "not actually a collision."
- Any combination of the above — a Gaussian-16, Petersson-1998-cited
  scheme coexisting with an ORCA, uncited one, both legitimately
  distinct.

**What it still rejects, exactly per `nulls_not_distinct` semantics:**
two schemes with the same `(kind, name, lot, version)` where
`source_literature_id`, `software_id`, **and** `workflow_tool_release_id`
are all `NULL` on both — nulls are treated as equal under this
constraint, so two fully-uncited, fully-software-less schemes of
otherwise-identical identity still collapse into one row via §1.3's
resolver lookup, exactly as today. This is §2.4's residual case,
answered by stating the absence, not by manufacturing a second row
nothing distinguishes.

**What is deliberately not folded into this same index:** `units`.
`public_refs.py`'s docstring (§1.2) also names `units` as
identity-relevant ("a different unit convention... means a scientifically
distinct scheme") — a real, separate inconsistency between the public-ref
hash and the DB constraint, but a third question this plan was not asked
to answer. Flagged in §8, not built here.

**Migration mechanics — `energy_correction_scheme` is an already-deployed
table** (real rows on the live Pi DB), so per the migration rules this is
a **new Alembic revision**, `down_revision` = current head
(`eb9793f23de6` as of this plan's base commit — resolve the actual head
at implementation time rather than hard-coding it), never an edit to
`d861dfd60891`. One revision, not two — the literature and software
columns are additive changes to the same table's identity, landing in the
same review is more coherent than splitting them, and both share the
identical migration-mechanics discussion below.

```python
def upgrade() -> None:
    op.add_column(
        "energy_correction_scheme",
        sa.Column(
            "software_id", sa.BigInteger(),
            sa.ForeignKey("software.id", deferrable=True, initially="IMMEDIATE"),
            nullable=True,
        ),
    )
    op.add_column(
        "energy_correction_scheme",
        sa.Column(
            "workflow_tool_release_id", sa.BigInteger(),
            sa.ForeignKey(
                "workflow_tool_release.id", deferrable=True, initially="IMMEDIATE",
                name="fk_energy_correction_scheme_workflow_tool_release_id",
            ),
            nullable=True,
        ),
    )
    op.drop_index(
        "uq_energy_correction_scheme_kind_name_lot_version",
        table_name="energy_correction_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_energy_correction_scheme_identity",
        "energy_correction_scheme",
        [
            "kind", "name", "level_of_theory_id", "version",
            "source_literature_id", "software_id", "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_energy_correction_scheme_identity",
        table_name="energy_correction_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_energy_correction_scheme_kind_name_lot_version",
        "energy_correction_scheme",
        ["kind", "name", "level_of_theory_id", "version"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("energy_correction_scheme", "workflow_tool_release_id")
    op.drop_column("energy_correction_scheme", "software_id")
```

The `downgrade()` path is only valid if no row created under the wider
index would collide under the narrower one — true today (both live rows
share `kind`/`name`/`lot`/`version` distinctness already, so no new
collision is introduced by dropping the extra columns), but a future
downgrade after real distinguished-only-by-software-or-citation
duplicates exist could fail on a genuine constraint violation. That is
the correct, honest behavior for a downgrade that would re-introduce
ambiguity the wider index was built to resolve — it should fail loudly,
not silently merge rows.

**Backfill decision:** none, on either axis. No data migration sets
`source_literature_id` or `software_id` on the two existing rows. §7
gives the full reasoning; §1.6 already showed the software evidence is
exactly as circumstantial as the literature evidence, and this plan
treats them identically for that reason.

**Companion service change (same PR as the migration, not a separate
one):** `resolve_or_create_scheme`'s lookup (§1.3 step 1) must add
`source_literature_id`, `software_id`, and `workflow_tool_release_id` to
its `WHERE` clause, matching the new index — otherwise the widened index
sits unused and the resolver keeps collapsing every upload into the
first row regardless of what the new payload fields say. Same one-line-
per-field shape the lookup already uses for its `is None` vs.
exact-match branching on `level_of_theory_id` and `version`.

**Companion upload-schema change:** add `software: SoftwareRef | None`
and `workflow_tool_release: WorkflowToolReleaseRef | None` to
`EnergyCorrectionSchemeRef`
(`schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py`),
identical in shape and resolution policy to `FreqScaleFactorRef`'s
existing fields (§1.6) — resolved the same way
`resolve_software`/`resolve_workflow_tool_release_ref` already resolve
them for FSF and calculation uploads (`energy_correction_resolution.py`
already imports both from `software_resolution` and
`calculation_resolution` for other purposes — reuse, don't reimplement).
Per `tckdb_schemas`' package-version convention
(`feedback_tckdb_client_version_bump`), this bumps
`schemas/python/tckdb-schemas`'s version.

## 4. How "strongly advised" is expressed, on both axes

Reusing what already exists, per the brief's own instruction — this
archive already has a non-blocking, structured warning vocabulary for
exactly this shape of "should have, didn't" gap:
`backend/app/services/provenance_warnings.py`'s `UploadWarning` +
`W_MISSING_*_PROVENANCE` codes, already wired into thermo, transport,
kinetics, and statmech uploads (`collect_thermo_provenance_warnings` and
its siblings), returned in the upload response's `warnings: list[
UploadWarning]` — never rejecting the request. `energy_correction_upload`
is not yet a caller of this module. It should become one, for both the
literature gap and the software gap.

**Literature — three warning moments, unchanged from this plan's first
draft:**

1. **On creating a genuinely new scheme with no `source_literature`.**
   Reuse the existing `W_MISSING_LITERATURE_PROVENANCE` code with a
   scheme-scoped `field` path (`applied_energy_corrections[i].scheme.
   source_literature`).
2. **On creating a new scheme whose `(kind, level_of_theory_id,
   software_id, workflow_tool_release_id)` already has an existing
   scheme with `source_literature_id IS NULL`** — new code
   `W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE`, naming the
   existing scheme's ref. Note this condition is now scoped past
   software: two schemes that differ by software are not ambiguous
   (§2.1), so this warning only fires when software (and workflow-tool
   release) also match or are both absent — the case §2.4 actually
   names.
3. **On a reuse where `ref.source_literature` was supplied but silently
   discarded** (§1.3 step 2) — code
   `W_ENERGY_CORRECTION_SCHEME_LITERATURE_NOT_ATTACHED`, pointing at
   §4.3's admin path as the remedy.

**Software — one new warning moment, scoped to the three kinds §1.6
identified:**

4. **On creating a new `atom_energy`/`bac_petersson`/`bac_melius`
   scheme with no `software` supplied.** New code
   `W_MISSING_ENERGY_CORRECTION_SCHEME_SOFTWARE`. Message text carries
   the weight §2.3 argued for, rather than a severity field (`UploadWarning`
   has no severity field — `field`/`code`/`message` only, and adding one
   is out of scope here): *"No software was supplied for this
   atom_energy/bac_petersson/bac_melius scheme. Unlike a literature
   citation, this archive's own calculations already know which program
   produced these numbers — atom-energy and bond-additivity corrections
   are software-dependent, so an unattributed scheme cannot be
   distinguished from a different program's values at the same level of
   theory."* Never fires for `atom_hf`/`atom_thermal`/`soc` — those kinds
   are `NOT_APPLICABLE` on this axis, using the exact sentinel
   `provenance_warnings.py` already defines for "this record type has no
   such anchor, so do not judge it" (`_NotApplicable`, `NOT_APPLICABLE`,
   `provenance_warnings.py:150-170`) — reused, not reinvented.
5. **On creating a new scheme whose `(kind, level_of_theory_id)` already
   has an existing scheme with `software_id IS NULL`** (an ambiguity
   §2.1 cannot resolve because neither side states a program) — folds
   into the same `W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_
   LITERATURE` warning from item 2 above when literature is also absent
   on both, since by that point the two facts (no citation, no software)
   are stating the same underlying problem — one warning, not two,
   naming both missing dimensions in its message.

**Read surface:** already computes `has_literature_source` /
`has_literature` correctly (§1.5) — the fix there is entirely frontend.
The equivalent `has_software`/software summary field is new (§3 adds the
column, the read service needs the equivalent one-line addition
`has_software=ecs.software_id is not None` plus a `software` summary
under `include=` — small, mechanical, same shape as the existing
`literature` inclusion).

**Review/trust layer:** not extended in this plan on either axis.
§1.6's/methods-surface's rubric checks a different axis (was a
scheme/FSF *applied*, not whether it *cites* or *attributes* anything).
Flagged in §8 for the owner to weigh, not decided here.

### 4.3 Closing the "no way to add a citation after the fact" gap

Small, scoped, admin-only, and now covers both axes rather than
literature alone. `EnergyCorrectionSchemeUpdate` already exists
(`backend/app/schemas/entities/energy_correction.py:136-143`) but is
unrouted. Do not route it as-is — as written it can rewrite `kind`,
`name`, `level_of_theory_id`, `version`, and `units` on a deployed
reference row, which is a far bigger surface than "attach missing
provenance" and reopens the exact ambiguity this plan is trying to close
(an admin silently renaming a scheme's identity out from under every
`applied_energy_correction` that cites it). Instead:

- New admin route, same pattern as `backend/app/api/routes/admin.py`'s
  existing `require_admin`-gated endpoints:
  `PATCH /admin/energy-correction-schemes/{ref}/provenance`.
- Accepts `source_literature` (a `LiteratureUploadRequest`-shaped body,
  or an `existing_literature_ref` per the `existing_*_id` upload
  convention — check which is the smaller diff at implementation time)
  and/or `software`/`workflow_tool_release` refs, each resolved via the
  same resolution services the upload path already uses.
- **Refuses to overwrite any field that is already non-null** — per-
  field, not all-or-nothing: an admin can fill `software_id` on a row
  that already has a citation, or vice versa, but can never silently
  swap or overwrite a value someone already recorded. This keeps the
  endpoint append-only in spirit on both axes: it can fill a gap, it
  cannot manufacture a correction to something already stated.
- Response includes the scheme's own public ref and the resolved
  literature's/software's, so the action is auditable from the response
  alone.

This is genuinely small on the literature side — the resolution
machinery, `require_admin`, and the `Update` schema's field all already
exist. The software side needs the same small addition once §3's
columns and resolvers land.

### 4.4 The ingestion-tooling gap (§1.6) is its own slice

Once §3's schema and upload-schema changes exist, `backend/scripts/
arc_ingestion/builder.py`/`extractor.py` still won't populate them —
today they build no `EnergyCorrectionSchemeRef` payload at all (§1.6).
Making the schema capable of recording software/literature and having
the one committed ingestion pipeline that produces these rows keep not
supplying them would just move the gap one layer down. Scoped as a
follow-up slice, not blocking this plan's schema/API/frontend work,
because it depends on knowing what `arc_ingestion` actually has
available from an ARC run (an `output.yml`'s own software/version
fields, most likely — `extractor.py` already parses ARC output for
other purposes) rather than on anything this plan itself decides.

## 5. Whether the literature subsystem works

Definitive: partially. Creation works when embedded in a **new**
scheme/FSF/etc. row (tested, e.g. `test_statmech_upload.py`); no
standalone deposit route exists anywhere (by design, confirmed via 3
separate route files); no `PATCH`/update route for scheme or literature
exists at all, so a literature citation can never be attached to an
**existing** scheme — this is the load-bearing gap that gates the
"backfill via re-upload" idea and motivates §4.3's admin route. Also
flag: `CorrectionSchemePage.tsx` / `LevelOfTheoryPage.tsx` /
`FrequencyScaleFactorPage.tsx` never render literature client-side
despite the API already serving it — the read half is silently
incomplete too (§1.5, closed by §6).

## 6. Frontend — expandable, software-titled boxes (§0.5)

`LevelOfTheoryPage.tsx`'s `CorrectionSchemesSection` currently renders
each scheme as a plain, always-open `<div className="correction-scheme-
block">` (§1.5). Per §0.5, replace this with the app's one canonical
disclosure primitive — `frontend/src/components/Disclosure.tsx` — one
`<Disclosure>` per scheme, matching the shape `EvidenceChecklist` and
every other card on this codebase's record pages already use.

**Titling — the rule from `540febb4` ("Fix misleading evidence-card
summaries") applies directly and is the reason this section is specific
about it.** That commit's own finding: a collapsed card's summary text
must be a true fact about *that* card's own content, never a constant
that happens to look like one (measured live: the fixed pre-fix fallback
showed "6 rows" on a card with 4 real records, and two structurally
opposite conformer groups both collapsed to the identical "3 rows").
The same discipline applies to a scheme box's title and its collapsed
summary:

- **Title (the `<summary>` heading), in priority order:**
  1. `{kind label} — {software}` when software is recorded — e.g.
     "Atom energies — Gaussian 16" if/when release-level granularity
     exists, or "Atom energies — Gaussian" at today's `software_id`-only
     grain (§3's open question). This is the case §0.5 asked for by
     name.
  2. `{kind label} — software not recorded` when software is absent.
     **This is the fallback §8's open question named — it must read as
     an absence, never as an identity.** Concretely: never fall back to
     `scheme.name` (that reintroduces exactly the depositor-free-text
     label the owner has twice now ruled off this page — once directly
     for literature/name, once implicitly by making software the
     namer); never render a blank or an em-dash where "software not
     recorded" would go, matching this archive's house rule that an
     absence is always stated, never implied.
- **Collapsed summary (the `Disclosure`'s `count`/roll-up, visible
  without opening the box):** a real, computed fact about this specific
  scheme's parameters — e.g. "8 elements" for an atom-energy scheme, "45
  bonds" for a Petersson BAC scheme — using the same
  `atom_param_count`/`bond_param_count` fields `evidence_summary` already
  returns (§1.5's read-schema audit found these already present and
  already correct, just unrendered). Never the row count of some other,
  unrelated list, and never a bare "correction scheme" placeholder — the
  exact defect `540febb4` fixed elsewhere on this codebase, now avoided
  here before it ships rather than fixed after an independent review
  finds it again.
- **Citation, inside the opened box:** the real formatted citation when
  `literature` is present (author/year/title, the pattern
  `LiteratureSummary` already carries elsewhere); "No literature source
  is recorded for this scheme" when absent and no ambiguous sibling
  exists; the two-sentence sibling-ambiguity statement from §2.4 when
  one does.

Same treatment, same component, for `CorrectionSchemePage.tsx`'s own
standalone view (§1.5's `<h1>`/breadcrumb fix folds in here — replace
`scheme.name` with the same `{kind label} — {software}` /
`{kind label} — software not recorded` title used on the LOT page, so
the two views can never diverge in how a scheme is named) and for
`FrequencyScaleFactorPage.tsx` (citation-or-absence block only — FSF
already has its software column and its own existing display; the
CCCBDB `note` URL continues to render as a note, not promoted into the
citation slot — conflating the two is exactly the free-text-as-citation
move §2 rejects).

## 7. The two existing rows — no fabricated backfill, on either axis

**Decision: `source_literature_id` and `software_id` both stay `NULL` on
both rows. No data migration touches them.**

The bit-for-bit match to RMG's `atom_energies` table, and the RMG key's
own `software='gaussian'` naming, and this archive's 100%-Gaussian-16
calculation history at this level of theory (§1.6) are all findings this
plan's own investigation made by reading independent sources side by
side — none of them is a fact either depositor (nor whatever produced
these two rows, which §1.6 confirmed is not any ingestion script
currently in this repository) ever recorded in TCKDB. Writing either
`source_literature_id` or `software_id` onto these rows via migration
would assert a provenance link the archive itself never received —
exactly the house rule this repo already states plainly: **never assert
from absence.** The absence here is not "we forgot to write down which
paper this came from" or "we forgot to record which program ran this,"
it is "nobody deposited that fact," and those are different claims even
when, as here, the surrounding evidence makes the true answer fairly
guessable.

What *is* available and does not require inventing anything:

- **Leave both rows exactly as deposited.** `has_literature_source:
  false` and (once §3 lands) `has_software: false` are the correct,
  honest signals, once the frontend actually renders them (§6).
- **The RMG correspondence and the single-software calculation history
  belong in this plan and in the paper's provenance discussion, not in
  the database.** They are documentation about the archive, not facts
  the archive asserts about itself. `methods-surface.md` §2.5.2 already
  states the RMG half this way — this plan does not change that, and
  extends the same treatment to the software half.
- **The real fix is a real deposit, by whoever can actually attest to
  it** — the person or team that ran Arkane and can confirm both the
  citation and the program — through §4.3's admin path once it exists,
  or a corrected future upload once §4.4's ingestion-tooling gap is
  closed. Until then, the honest state is "not recorded," stated as such
  (§2, §6), not silently patched over.

## 8. Open questions for the owner

- **`units` in the identity.** §3 named this as a real, adjacent
  inconsistency in `public_refs.py`'s own docstring, deliberately left
  out of this migration because it wasn't a question this plan was asked
  to answer. If the owner wants it folded in, it is the same shape of
  change and can ride the same revision — needs an explicit yes.
- **`software_id` vs. `software_release_id` grain.** §3 mirrors FSF's
  coarser `software_id` (name only, no version) deliberately, to avoid
  introducing a third precision level unilaterally. If the owner wants
  release-level precision (Gaussian 16 vs. Gaussian 09 as distinct AEC
  sources — plausible, since program-version defaults can genuinely
  change BAC-relevant behavior), the cleaner fix is upgrading
  `FrequencyScaleFactor` and `EnergyCorrectionScheme` together in a
  follow-up, not giving the new sibling a different grain than the one
  it's explicitly modeled on.
- **Trust-rubric extension (§4).** Whether `computed_thermo_v1` /
  `computed_statmech_v1` should gain `O`-kind checks for
  scheme/FSF-own-citation and scheme-own-software completeness. Not
  proposed as required here; flagged so it isn't rediscovered cold.
- **Frequency scale factors are a sibling slice for the frontend/admin
  work, not folded into this plan's migration.** FSF's unique index
  already includes `source_literature_id` and `software_id` (§1.2,
  §1.6) — the DB-level half of this plan's fix is already done for FSF
  on both axes. What FSF is missing is exactly §6's frontend rendering
  gap and an equivalent to §4.3's admin attach-provenance route
  (`FrequencyScaleFactorUpdate` exists at
  `backend/app/schemas/entities/energy_correction.py:173-179`, same
  unrouted-schema shape as the scheme side). Recommend a short follow-up
  plan scoped to FSF's admin/frontend gap alone rather than doubling
  this one's size.
- **Should `_merge_scheme_params`'s value-conflict error (§1.3) become a
  coded, catchable error** in the same family as
  `W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED`, rather than a bare
  `ValueError`? Out of scope here (it already does the right thing —
  reject rather than silently pick a value) but its error shape is
  inconsistent with the rest of this module's coded-error convention.
  Noted, not fixed, to avoid scope creep into unrelated error-handling
  cleanup.
- **§4.4's ingestion-tooling fix** depends on what `arc_ingestion`
  actually has available from an ARC run's `output.yml` — needs a look
  at the extractor's existing parsing before it can be scoped
  precisely; flagged as a real gap, not sized here.

## 9. Slice acceptance criteria

**Slice A — schema + migration + resolver (§3).** New Alembic revision
passes `alembic upgrade head` on a fresh DB and on the existing dev DB
rebuild path; both live rows still resolve, unchanged, after upgrade
(`source_literature_id` and `software_id` both `NULL` on both, still 2
rows, still findable by their existing refs — the migration must not
force new refs onto rows that didn't change identity). A constructed
fixture — two schemes, same `(kind, name, lot, version)`, one
`source_literature_id = NULL` one non-null — inserts cleanly under the
new index and is rejected under the old one (mutation check: revert the
index, watch the test that expects two rows fail with an
`IntegrityError`). The same for two schemes differing only by
`software_id`. Two schemes both fully `NULL` on all three new/adjusted
dimensions under otherwise-identical identity still collapse to one row
via the resolver (mutation: widen the resolver's lookup to ignore any
one of the three fields, watch a test asserting exactly one row fail).

**Slice B — warnings (§4).** A LOT-scoped-kind scheme upload with no
`source_literature` and no `software` returns both
`W_MISSING_LITERATURE_PROVENANCE` and
`W_MISSING_ENERGY_CORRECTION_SCHEME_SOFTWARE`; an `atom_hf`/
`atom_thermal`/`soc` scheme upload with no `software` returns neither
software warning (mutation: remove the kind guard, watch a test
asserting silence on these three kinds fail). A second same-kind-
same-LOT-same-software uncited scheme upload returns
`W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE`; the identical
upload with a *different* software does not (mutation: drop the
software match from the ambiguity check, watch a test asserting silence
on differing-software siblings fail). A reuse where `source_literature`
or `software` was supplied but the identity already existed returns the
"not attached" warning and the existing row's fields are confirmed
unchanged.

**Slice C — admin attach-provenance route (§4.3).** `PATCH
/admin/energy-correction-schemes/{ref}/provenance` on a scheme missing
`software_id` sets it without touching an already-present
`source_literature_id`, and vice versa; the identical call attempting to
overwrite an already-non-null field on either axis returns a
conflict/4xx and leaves the existing value untouched (mutation: drop the
per-field guard, watch a test asserting the original value survives a
second call with a different one fail). Route is `require_admin`-gated.

**Slice D — frontend (§6).** `/methods/schemes/ecs_q5potmkzrmm6ynh2behv5kbfdu`
no longer shows `scheme.name` as its heading; shows "Atom energies —
software not recorded" as the title and "No literature source is
recorded for this scheme" in the body. A constructed fixture with two
same-kind, same-LOT schemes on `/methods/:lotRef` — one Gaussian, one
ORCA — renders as two separately-titled, separately-collapsible boxes,
neither flagged as ambiguous (mutation: remove the software distinction
from the ambiguity check, watch a test asserting no ambiguity sentence
appears fail). A fixture with two same-kind, same-LOT, same-software,
both-uncited schemes renders the §2.4 sibling-ambiguity sentence on both,
naming each other's ref. Every box's collapsed summary shows a real
parameter count, never a placeholder (mutation: hard-code a fixed string
in place of the count, watch the `540febb4`-style mutation guard —
written the same way that commit's own guard was — fail).
