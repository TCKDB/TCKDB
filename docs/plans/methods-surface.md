# Methods surface — implementation plan

Status: draft v2, supersedes the v1 draft published on `plan-methods-surface`.
The owner reviewed v1 and ruled it onto a different shape before any code was
written — see §0. Base: `main` at `2f9966ba`. Plan sibling:
`docs/plans/reaction-entry-page.md` (structure and review method assumed
here); `docs/plans/provenance-first-website.md` (house rules, items 12–30).
This plan replaces the `/methods` `RecordPlaceholderPage` and the home
page's "Methods … Coming soon" card.

## 0. The owner's rulings

Quoted verbatim so nobody relitigates them.

1. **Group the index by `level_of_theory_ref`, not by method text.** Settled
   in v1 review, restated here as final: the "Levels of theory" table is one
   row per distinct `level_of_theory_ref`. v1 had this as an open question
   in its §8 ("owner should confirm this is intentional") — it is now
   confirmed, and the risk v1 flagged (two LOTs sharing a method string the
   day one differs only in dispersion or spin treatment) is the reason, not
   a caveat.

2. **The premise of v1 — a bare vocabulary index — is rejected.** The
   owner's words: *"we could do method pages with the method and basis and
   the software and version so we can see specific freqs for those params?
   and corrections of AEC and BAC? ... it could be just a reference page for
   users too like how in `~/code/RMG-database/input/quantum_corrections/data.py`"*.
   This is not a bigger version of the v1 index — it is a different object.
   v1 sized a page to "4 rows of method/basis/dispersion/solvent." The
   owner is asking for a page **per level of theory** that shows what a
   computational chemist would look up before trusting a number computed at
   that level: the correction parameters, not just the identity that names
   it. §3 below reverses v1's rejection of "record page per level of
   theory" for exactly this reason — the object the owner wants has real,
   already-deposited content, described in §2.5–2.7.

## 1. What is being decided

Not "is `/methods` worth building" (v1's question) — the owner has already
answered that by proposing more scope, not less. The question this
revision answers is: **what does a page keyed on one level of theory
actually show, given what the archive has genuinely deposited against it,
and what does it show honestly when nothing is deposited?** A level of
theory is still provenance vocabulary, not a reviewed record — that part of
v1's framing (§1 there) is unchanged and still matters: no review pill, no
submission history, because none of these rows have one. What changes is
the *content* below the identity block.

## 2. Measured facts

All against the live archive (`https://tckdb.homecalvin.com`, anonymous,
2026-09-08) and the code at the base commit above. §2.1–2.4 restate v1's
findings, corrected where this revision's direction makes them wrong;
§2.5–2.7 are new.

### 2.1 How much is actually here (unchanged from v1)

| Axis | Distinct values | Endpoint used |
|---|---|---|
| Species | 59 (`pagination.total`) | `species/browse?limit=1` |
| Transition-state entries | 34, all `not_reviewed` | `transition-states/browse?limit=1` |
| Reaction entries | 42, all `not_reviewed` | `reactions/browse?limit=1` |
| Pressure-dependent networks | 1 (hydrazine) | `networks/search?has_species=true&limit=1` |
| Calculations | **572** total (416 + 78 + 39 + 39) | `calculations/search?method=…` |
| Levels of theory (`level_of_theory` rows in use) | **4** | derived, see §2.2 |
| Software packages | **3** — Gaussian, Molpro, ORCA | `/meta/software` |
| Software releases in use | Gaussian: 2 (`09`, `16`); Molpro: 0 versions recorded; ORCA: 0 versions recorded | `/meta/software-versions?software=…` |
| Workflow tools | **1** — ARC | `/meta/workflow-tools` |
| Energy correction schemes | **2** — `atom_energy`, `bac_petersson` | `energy-correction-schemes/search?has_corrections=true` |
| Frequency scale factors | **12** | `frequency-scale-factors/search?used_by_statmech=true` |

The four levels of theory, confirmed by resolving each `/meta/methods`
value through `calculations/search?method=…` to its `level_of_theory`
block, **now cross-checked against which software release and which
correction records actually attach to each one** (§2.5–2.6 add the new
columns):

| `level_of_theory_ref` | method | basis | calculations | software (release) | ECS deposited | FSF deposited |
|---|---|---|---|---|---|---|
| `lot_rrmbqrod3suvzkez2ta76hj76u` | `b3lyp` | `def2tzvp` | 416 | Gaussian 16 | atom_energy + bac_petersson | 10 rows, all `0.999` |
| `lot_nq2fhijglv66bpotxxgnhgz3e4` | `wb97xd` | `def2tzvp` | 78 | Gaussian 09 | — | 1 row, `0.986` |
| `lot_tssicw5koffreue62hl5ldm4o4` | `CCSD(T)-F12` | `cc-pVTZ-F12` | 39 | ORCA | — | 1 row, `0.998` |
| `lot_br4gmxj2xjsl5oaumonbr5mh64` | `MRCI+Davidson` | `aug-cc-pV(T+d)Z` | 39 | Molpro | — | — |

The software column is new and was not free: `/meta/software` only reports
totals across the whole archive (Gaussian 494, Molpro 39, ORCA 39), with no
way to see which LOT each package belongs to. It took **12 separate
`calculations/search?lot_ref=…&software=…` calls** (4 LOTs × 3 packages) to
build that column by hand — cross-checked so every LOT's calculation count
is accounted for by exactly one package, with zero overlap:
`lot_rrmbqrod3suvzkez2ta76hj76u × Gaussian = 416`,
`× Molpro = 0`, `× ORCA = 0`, and so on for all 4×3 combinations. §2.5.5
turns this into the endpoint gap it is.

Today there is exactly one `level_of_theory` row per method (no method is
split across two basis sets), so grouping by "method" and grouping by
`level_of_theory_ref` happen to coincide — a coincidence of small data, not
a structural guarantee (§2.3), which is exactly why ruling 1 in §0 settles
it as `level_of_theory_ref`.

### 2.2 What the API already serves — the vocabulary layer (unchanged from v1)

The vocab endpoints consumed by the browse filters
(`frontend/src/api/vocabApi.ts`) are `loadMethods` → `/meta/methods`,
`loadBasisSets` → `/meta/basis-sets`, `loadSoftwareNames` →
`/meta/software`, `loadSoftwareVersions` → `/meta/software-versions`,
`loadWorkflowToolNames` → `/meta/workflow-tools`,
`loadWorkflowToolVersions` → `/meta/workflow-tool-versions`
(`backend/app/api/routes/scientific/meta.py`,
`backend/app/services/scientific_read/meta.py`).

**They count two different things, and the module docstring says so, but a
methods page built naively on top would render the wrong number.**

- `/meta/software` and `/meta/workflow-tools` are **usage-derived**: `count`
  is the number of *calculations* attributing that software/tool
  (`INNER JOIN` from `calculation`).
- `/meta/methods` and `/meta/basis-sets` were **not given the same fix**.
  `_counted_distinct` (`meta.py:52`) groups directly over the
  `level_of_theory` table's own rows — `count` is *how many
  `level_of_theory` rows share that method string*, not how many
  calculations use it. Measured live, unchanged since v1:
  `/meta/methods` reports `{"value": "b3lyp", "count": 1}` while 416
  calculations actually run at it — a **416× understatement**.

**Confirmed still true, re-verified live at this commit:** no
`/scientific/level-of-theories/{ref}` route exists at all — `grep -rn
"level-of-theor" backend/app/api/routes/scientific/*.py` matches nothing
but the `/meta/methods` docstring and an unrelated comment in
`networks.py:257`. `level_of_theory` has no handle resolver in
`app/services/scientific_read/handles.py` — every other addressable
scientific reference type does (`resolve_frequency_scale_factor_handle`,
`resolve_energy_correction_scheme_handle`, `resolve_literature_handle`, …,
`handles.py:487-599`). **A level of theory is the one first-class
provenance identity in the whole scientific-read surface with no detail
endpoint of its own.** This is the gap the owner's ruling makes central —
see §5.1.

### 2.3 The domain model (unchanged from v1)

- `level_of_theory` (`backend/app/db/models/level_of_theory.py`) is a
  first-class deduplicated identity keyed by `lot_hash` (a hash over
  `method, basis, aux_basis, cabs_basis, dispersion, solvent,
  solvent_model, keywords`, plus — since DR-0034 — `spin_treatment`), not
  a per-calculation string.
- **`display` (`method/basis`) is explicitly documented as not an
  identity** (`LevelOfTheorySummary.display`,
  `backend/app/schemas/reads/scientific_common.py:241-260`, re-read at this
  commit): "two different `level_of_theory` rows can render the same
  string when they differ only in dispersion, solvent or spin treatment.
  `level_of_theory_ref` is the handle to compare on; `display` is for
  reading. **Nothing should key, group or deduplicate on it.**" This is
  ruling 1's justification, restated: the methods index groups on
  `level_of_theory_ref`, never on `(method, basis)` text.
- **`spin_treatment` is part of the identity but is still not served
  anywhere**, re-checked at this commit:
  `LevelOfTheorySummary` (`scientific_common.py:223-260`) has
  `level_of_theory_id`, `level_of_theory_ref`, `method`, `basis`,
  `dispersion`, `solvent`, `label`, and the derived `display` — no
  `spin_treatment` field. DR-0034 added the column to identity and to
  `lot_hash`, never to any read schema. Still flagged, still needs fixing,
  now for a page whose entire point is showing what distinguishes one
  level of theory from another (§5.4).
- **`method`/`basis`/etc. are free text, not a controlled vocabulary** —
  unchanged, DR-0034's normalization deferral still stands. The RMG
  reference file (§2.7) makes this concrete rather than abstract: its keys
  are Python `repr()` strings of a `LevelOfTheory` object
  (`"LevelOfTheory(method='b3lyp2023',basis='def2tzvp',software='gaussian')"`)
  — a different casing and tokenization convention than TCKDB's own
  `method='b3lyp'` lowercase-with-year-suffix-stripped. **A methods page
  that shows both TCKDB's stored strings and cites the RMG file as
  provenance must not silently normalize one to match the other** — see
  §2.7 for exactly how close the numbers match despite the string
  mismatch.
- **`level_of_theory_id` on `energy_correction_scheme` and
  `frequency_scale_factor` is nullable**
  (`backend/app/db/models/energy_correction.py:61-64`, `:218-224`). A
  scheme or scale factor can exist with no LOT at all — the model
  anticipates atom_hf/atom_thermal/SOC constants that are element
  properties, not method-dependent ones (§2.7). None of the two live
  schemes exercises this today (both point at LOT id 4), but a level-of-
  theory page must not assume every scheme belongs to exactly one LOT, and
  a schemes index (§4.3) is where an unattached scheme would have to live.

### 2.4 What existing pages already show — corrected and extended

`level_of_theory` is rendered, as **plain unlinked text**, in at least
eight places (`ConformerGeometryTab.tsx`, `ConformerSinglePointTab.tsx`,
`EntryStatmechSection.tsx`, `EntryThermoSection.tsx`,
`ReactionTransitionStatesSection.tsx`, `ConformerGroupPage.tsx`,
`ConformerObservationPage.tsx`, `TransitionStateEntryPage.tsx`,
`SourceCalculationsTable.tsx`, `CalculationDetailPage.tsx`,
`ReactionEntryPage.tsx`) via `lotLabel()`
(`frontend/src/api/scientificSchemas.ts:24`).

**Two more dead-ref sites, missed in v1 because v1 never looked at the
correction-application UI — they render `energy_correction_scheme_ref` and
`frequency_scale_factor_ref` as literal unlinked text/`<code>`, exactly the
same pattern:**

- `CalculationDetailPage.tsx:1256-1286` — `EnergyCorrectionsSection`
  renders a `.data-table` of applied corrections per calculation with
  columns `Scheme`, `Scheme ref`, `Frequency scale factor ref`; both ref
  columns print the raw string
  (`row.energy_correction_scheme_ref ?? "not recorded"`,
  `row.frequency_scale_factor_ref ?? "not recorded"`) with no link.
- `EntryStatmechSection.tsx:853-891` — `FrequencyScaleFactorDetail` prints
  `fsf.frequency_scale_factor_ref` in a `<code>` element, unlinked, right
  next to the scale factor's own value and the LOT it was measured at
  (`lotLabel(fsf.level_of_theory)`, also unlinked).

**This changes the render-site count from "11+" to 13+, and it changes
what the anchor has to be.** v1 only needed one anchor kind
(`/methods/:lotRef`). This revision needs three, because three different
ref kinds are dead text on the archive today: `level_of_theory_ref`,
`energy_correction_scheme_ref`, `frequency_scale_factor_ref`. §4.3 and
§4.4 give each its own thin page; §6 lists every call site.

### 2.5 The correction-reference layer is already built and already live

This is the finding that overturns v1's premise. **The backend work the
owner is describing — a page with "the software and version," "corrections
of AEC and BAC" — already exists as a read API**, shipped and documented
in `backend/docs/specs/scientific_correction_reads.md`, live on the Pi
today. v1 never looked past `/meta/*` and `calculations/search`, so it
never found this.

#### 2.5.1 What's live

```
GET /api/v1/scientific/frequency-scale-factors/{ref}
GET /api/v1/scientific/frequency-scale-factors/search

GET /api/v1/scientific/energy-correction-schemes/{ref}
GET /api/v1/scientific/energy-correction-schemes/search
```

Verified live, not just read from the spec doc:

```
GET /energy-correction-schemes/search?has_corrections=true
→ 2 records: ecs_q5potmkzrmm6ynh2behv5kbfdu (atom_energy),
             ecs_5dzse4an2emubgyxge2dpj4ae4 (bac_petersson)
  both level_of_theory = lot_rrmbqrod3suvzkez2ta76hj76u (b3lyp/def2tzvp)

GET /energy-correction-schemes/ecs_q5potmkzrmm6ynh2behv5kbfdu?include=corrections,used_by
→ corrections: 8 atom rows (H, C, N, O, F, S, Cl, Br), each a per-element
  Hartree value
→ used_by: 82 species_entry applications, application_role=aec_total,
  each carrying applied_value, applied_value_unit, source_calculation_ref

GET /frequency-scale-factors/search?used_by_statmech=true
→ 12 records total, matching the DB row count exactly
```

The route file, service files, schema files, and test file named in the
spec doc all exist at this commit (verified with `test -f` against every
path the spec cites) — this is not aspirational documentation.

#### 2.5.2 The RMG numbers are already TCKDB's numbers

The atom-energy row values are not approximately what RMG's
`quantum_corrections/data.py` has for `b3lyp2023/def2tzvp` — they are
**bit-for-bit identical**:

| Element | TCKDB `ecs_q5potmkzrmm6ynh2behv5kbfdu` atom param (Hartree) | RMG `atom_energies["...b3lyp2023...def2tzvp..."]` |
|---|---|---|
| H | `-0.5010929786112002` | `-0.5010929786112002` |
| C | `-37.86564131254805` | `-37.86564131254805` |
| N | `-54.60589708581987` | `-54.60589708581987` |
| Br | `-2574.144347409212` | `-2574.144347409212` |

This is strong evidence TCKDB's `atom_energy` scheme *is* Arkane's
generated table for this level of theory (the scheme's own `note` says
"Per-species AEC computed by Arkane," `energy-correction-schemes/…` core
block), not an independently-fitted set — worth stating on the page as
provenance, not as coincidence.

#### 2.5.3 The bond-additivity table is the same shape as RMG's `pbac`

`ecs_5dzse4an2emubgyxge2dpj4ae4` (`bac_petersson`) carries 45 bond-keyed
parameters (`C-H`, `C=C`, `N#N`, `O=S`, …) in `kcal_mol`, structurally
identical to a `pbac[...]` entry in the RMG file (bond-type key → float).
Not independently value-diffed against RMG here (RMG's `pbac` table has no
`b3lyp/def2tzvp` key — Petersson-type BACs there are keyed by composite
`freq=…,energy=…` or `cbsqb3`-family LOTs, not by a plain DFT/basis pair),
but the *shape* match — same bond-type vocabulary, same per-bond scalar —
is the point: **this is not a novel table TCKDB invented; it is the same
correction-table shape the field already uses**, which is exactly the
"reference page for users" framing the owner asked for.

#### 2.5.4 What RMG's file has that TCKDB's live data does not

Reading `~/code/RMG-database/input/quantum_corrections/data.py` in full
(2502 lines):

| RMG dict | Keyed by | TCKDB equivalent | Deposited today? |
|---|---|---|---|
| `atom_hf` (atom enthalpy of formation, global) | element only | `EnergyCorrectionSchemeKind.atom_hf` | **No** — 0 schemes of this kind |
| `atom_thermal` (thermal contribution, global) | element only | `EnergyCorrectionSchemeKind.atom_thermal` | **No** — 0 schemes |
| `SOC` (spin-orbit correction, global) | element only | `EnergyCorrectionSchemeKind.soc` | **No** — 0 schemes |
| `atom_energies` (per-LOT atomic energies) | LOT repr string | `EnergyCorrectionSchemeKind.atom_energy` | **Yes** — 1 scheme, 8 elements |
| `pbac` (Petersson BAC) | LOT repr string | `EnergyCorrectionSchemeKind.bac_petersson` | **Yes** — 1 scheme, 45 bonds |
| `mbac` (Melius BAC, 4 sub-components) | LOT repr string | `EnergyCorrectionSchemeKind.bac_melius` + `MeliusBacComponentKind` (`atom_corr`/`bond_corr_length`/`bond_corr_neighbor`/`mol_corr`) | **No** — 0 schemes, 0 component params |
| `freq_dict` (frequency scale factor) | LOT repr string, one float | `frequency_scale_factor.value` | **Yes** — 12 rows (§2.6) |

**TCKDB's schema already models every dict in RMG's reference file** —
`atom_hf`/`atom_thermal`/`SOC` are real `EnergyCorrectionSchemeKind` enum
members (`common.py:1076-1084`) with a nullable `level_of_theory_id`
because, like RMG's dicts, they are genuinely LOT-independent (§2.3). None
has ever been deposited. **This must be stated as an absence, not
inferred:** "no atom_hf scheme is deposited" is a fact about what has been
uploaded, not a claim that TCKDB's schema can't represent one or that no
depositor needs one. A methods/schemes page must say this plainly rather
than omitting the row or (worse) rendering a blank/zero.

### 2.6 Coverage, LOT by LOT — honestly

Only 1 of the 4 live levels of theory has any correction scheme, and it has
both live scheme kinds. Only 3 of 4 have a frequency scale factor. These
are two different absences and the reason for each is different — this
table is the one the owner's "reference page" ask needs to render
correctly, and it is the one place in this plan where getting the wording
wrong would violate the house rule against asserting from absence.

| `level_of_theory_ref` | calculations | atom-energy scheme | BAC scheme | FSF |
|---|---|---|---|---|
| `lot_rrmbqrod3suvzkez2ta76hj76u` (b3lyp/def2tzvp) | 416 | ✓ (8 elements) | ✓ (45 bonds) | ✓ (10 rows, all `0.999`) |
| `lot_nq2fhijglv66bpotxxgnhgz3e4` (wb97xd/def2tzvp) | 78 | — | — | ✓ (1 row, `0.986`) |
| `lot_tssicw5koffreue62hl5ldm4o4` (CCSD(T)-F12/cc-pVTZ-F12) | 39 | — | — | ✓ (1 row, `0.998`) |
| `lot_br4gmxj2xjsl5oaumonbr5mh64` (MRCI+Davidson/aug-cc-pV(T+d)Z) | 39 | — | — | **—** |

**Two different absences, and the page must say which one it is:**

- For wb97xd and CCSD(T)-F12, "no correction scheme is deposited for this
  level of theory" is a plain gap statement — nothing in the archive says
  *why* no AEC/BAC was applied at these levels, and the page must not
  imply one. This is the "no scheme deposited" case the task brief asks
  to distinguish.
- For MRCI+Davidson's missing FSF, this plan found a *structural* reason,
  not a gap: querying `statmech/search?method=MRCI%2BDavidson` returns 27
  records, and every one of them carries a `frequency_scale_factor` block
  pointing at a **different** LOT —
  `fsf_hjk4k4jqs6egv5n3cipsflfemm`, whose own `level_of_theory` is
  `lot_nq2fhijglv66bpotxxgnhgz3e4` (wb97xd/def2tzvp), value `0.986`. Read
  together with the archive's own three-levels-of-theory statmech model
  (geometry / frequency / energy — named directly in
  `EntryStatmechSection.tsx`'s doc comment on `statmechRecordProductLevels`
  and `ProductLevelsFact`), this is consistent with MRCI+Davidson serving
  only as the **energy** leg of a composite calculation in every deposit
  this archive has — never the frequency-computing level — which is
  structurally why no frequency scale factor has ever been recorded
  against it here. **The page must render this as "no frequency scale
  factor is deposited for this level of theory," the same wording as the
  wb97xd/CCSD(T)-F12 scheme gap, not as "not applicable"** — the archive
  has evidence consistent with a structural reason, but not a stored fact
  that says so (no field marks a LOT as "energy-only"), and the house rule
  is that a value the API does not state is never rendered as if the API
  stated it. The finding belongs in this plan as *context for the reader
  writing the page*, not as text the page itself asserts.

### 2.7 The RMG reference file — what it actually is

`~/code/RMG-database/input/quantum_corrections/data.py`, read in full (not
sampled): a single 2502-line Python module, no classes, no functions — six
module-level dicts (`atom_hf`, `atom_thermal`, `SOC`, `atom_energies`,
`pbac`, `mbac`, plus `freq_dict` at the bottom) and nothing else. Every
per-LOT dict is keyed by the Python `repr()` of an RMG `LevelOfTheory` (or
`CompositeLevelOfTheory`) object, e.g.
`"LevelOfTheory(method='b3lyp2023',basis='def2tzvp',software='gaussian')"`
— a string a Python program would `eval()` back into an object, not a
display string a chemist reads. Every entry is inline-commented with its
source (a DOI, a CCCBDB URL, or "Unknown source" — RMG is candid about its
own provenance gaps in-line, e.g. `"H-H": 1.1,  # Unknown source`).

This is the comparison point the owner named, and it clarifies the ask
precisely: **RMG's file is a static, hand-maintained, version-controlled
Python dict — the numbers are right, but there is no notion of "how many
molecules used this," no link from a number back to the calculation it was
computed from, and updating it means editing a `.py` file and cutting a
new RMG-database release.** TCKDB's `energy_correction_scheme` /
`applied_energy_correction` pair (DR-0003, §2.5) is the same content
shaped as **live, queryable, provenance-linked, append-only data** instead
of a static file: the atom-energy numbers match RMG's bit-for-bit (§2.5.2)
*and* the page can additionally say "82 species entries have this scheme
applied, reached through these calculations." That is strictly more than
RMG's file can offer a reader, which settles §3's framing question below —
a level-of-theory page that shows correction parameters *is* the reference
page, not a separate thing next to it.

## 3. The decision

**v1's rejection of "record page per level of theory" is reversed.** v1
rejected it because a bare LOT identity plus a link to
`calculations/search?lot_ref=…` has "no additional facts beyond the six
identity fields." That premise is false: §2.5–2.7 show a level of theory
that has real, deposited, structured content beyond its identity fields —
correction schemes with per-element and per-bond parameter tables, a
frequency scale factor, and a specific software release — and v1 simply
never queried the endpoints that already serve it.

**The methods surface is now:**

1. **`/methods` — an index**, still thin, still grouped by
   `level_of_theory_ref` (ruling 1). Same three provenance families as v1
   (levels of theory, software, workflow tools), because the index's job —
   "what does this archive have provenance vocabulary for" — hasn't
   changed. What changed is where each LOT row links to.
2. **`/methods/:lotRef` — a real record page**, not v1's thin anchor.
   Method/basis/dispersion/solvent/spin treatment; the software release(s)
   observed running it; its correction schemes rendered as the atom/bond
   parameter tables they are; its frequency scale factor(s); and how many
   calculations, species, reactions, and TS entries used it. This is the
   page the owner described.
3. **`/methods/schemes/:ecsRef` and `/methods/frequency-scale-factors/:fsfRef`
   — thin anchor pages**, one tier down from the LOT page, for the two
   dead-ref kinds found in §2.4 (`CalculationDetailPage.tsx`,
   `EntryStatmechSection.tsx`) and for the case §2.3 names explicitly: a
   scheme with `level_of_theory_id IS NULL` (an `atom_hf`/`atom_thermal`/
   `SOC` scheme, if one is ever deposited) has nowhere to live on a
   LOT-keyed page at all, because it isn't keyed to one. These pages are
   nearly free — the detail endpoints already exist (§2.5.1) — and they
   are exactly what makes the RMG comparison land: RMG's `atom_hf` dict has
   no per-LOT key at all, and neither does a TCKDB `atom_hf` scheme, so its
   only honest home is a scheme-keyed page, not a LOT-keyed one.

Rejected alternatives, updated from v1:

- **A bare vocabulary index with no per-LOT content (v1's shipped
  decision)** — rejected now for the reason given above: it was sized to
  what v1 measured, and v1 measured the wrong endpoints. Section §2.5
  shows the content was already there.
- **Fifth `BrowseKind`** — still rejected, same reasoning as v1: no review
  state, no submission, not paginated at this size. Unchanged by this
  revision; a LOT record page still isn't a `BrowseKind` member, it is its
  own route family the way FSF and ECS already are their own route
  families (`/scientific/frequency-scale-factors/*`,
  `/scientific/energy-correction-schemes/*`) rather than being folded into
  `species/browse`-style machinery.
- **A separate static "reference/explainer" page, apart from the per-LOT
  pages** (the owner's "reference page for users" framing, taken as a
  distinct route) — rejected per §2.7's conclusion: a per-LOT page *already
  is* the reference page, and building a second, static one duplicates
  content that is better live. The intro paragraph v1 planned for `/methods`
  (§4.1) still carries the prose framing ("here is what TCKDB records as a
  level of theory and why"); it does not need its own route.
- **Folding corrections into the LOT page only, with no standalone scheme
  page** — considered and rejected because of the nullable-LOT case
  (§2.3, §2.7's `atom_hf`/`atom_thermal`/`SOC` gap): a scheme not tied to
  any LOT has no LOT page to fold into. It's also the more honest answer to
  the two already-dead-in-the-UI ref kinds found in §2.4 — they need
  somewhere to link *to*, same as v1's original `level_of_theory_ref`
  problem, and the fix is the same shape: give the ref an anchor.

## 4. Information architecture

### 4.1 `/methods` — the index

One route, `RecordPlaceholderPage`'s replacement. Unchanged from v1 in
shape (intro paragraph, then three `.data-table` sections — levels of
theory, software, workflow tools), reduced in one respect and extended in
another:

- **Reduced:** the "Levels of theory" table's link target is no longer a
  thin anchor — it is the real record page in §4.2, so the index itself
  stays exactly as small as v1 planned (one row per `level_of_theory_ref`:
  method, basis, dispersion, solvent, calculation count, a link). The
  index does not need to grow columns for corrections or software — that
  content lives one click in, per §3.
- **Extended:** the intro paragraph (still the reference-surface framing,
  folded in per §3's third rejected alternative) should say, briefly, what
  a LOT page contains beyond identity — "levels of theory below link to
  their correction parameters and observed software, where any are
  deposited" — so a reader lands on the index knowing the deeper page
  exists before they click.

Software and workflow-tool sections are unchanged from v1 (§4.1 there):
`.data-table`s from `/meta/software` / `/meta/workflow-tools` and their
`-versions` counterparts, no new endpoint needed for those two sections.

### 4.2 `/methods/:lotRef` — the level-of-theory record page

This is the page that changed. Sections, in the order a chemist checking a
number would want them:

1. **Identity block** (`RecordIdentityHeader`-style, no review pill — LOT
   rows carry no review state, unchanged from v1's own caution about not
   synthesizing one): method, basis, dispersion, solvent, spin treatment
   (once §5.4 lands), `lot_hash` in a `RefsDisclosure`, the `lot_…` ref
   with a copy button.
2. **Observed software.** One or more `{software, version, calculation
   count}` rows — today exactly one per LOT (§2.1's table), each linking to
   `calculations/search?lot_ref=…&software=…&software_version=…`. Backed
   by a new aggregation, §5.3 — this is not free the way §4.1's software
   section is, because no existing endpoint answers "which software ran
   this LOT," only "which software ran anything."
3. **Correction schemes.** Zero, one, or two `.data-table` blocks — one
   per deposited scheme (§2.6's coverage table), each rendered as its real
   shape: an `atom_energy` scheme as an element→value table (8 rows today,
   RMG-style, per §2.5.2), a `bac_petersson` scheme as a bond-type→value
   table (45 rows, per §2.5.3), a future `bac_melius` scheme as its four
   `MeliusBacComponentKind` sub-tables. Each scheme's row links to
   `/methods/schemes/:ecsRef` (§4.3) for the standalone view (used-by
   count, literature source, etc.) — the LOT page shows the recipe inline,
   the scheme page is where its own provenance and application list live,
   matching the "recipe vs. application" split DR-0003 already draws.
   **When a LOT has no scheme deposited (2 of 4 today), state it once per
   possible scheme kind that exists on this archive at all, don't imply
   absence of need:** "No atom-energy correction scheme is deposited for
   this level of theory" — not blank, not omitted, not "not applicable"
   (§2.6's house-rule caution).
4. **Frequency scale factor.** The FSF value(s) actually observed for this
   LOT (§2.6) — today, b3lyp/def2tzvp genuinely has 10 distinct FSF rows
   that all display `0.999`/`fundamental` and differ only in which ARC
   workflow-tool-release row produced them (§2.5.1's measurement). **Do not
   collapse these into one row silently** — public-ref identity treats
   them as 10 distinct records for a reason (DR-0003's provenance-per-
   application principle), and a page that silently deduplicates to "0.999"
   would hide that 10 separate depositor actions produced the same number.
   Render as one value with a disclosure listing the distinct
   `frequency_scale_factor_ref`s and their workflow-tool-release
   provenance, not as 10 identical-looking rows. When no FSF is deposited
   (MRCI+Davidson today), state "No frequency scale factor is deposited
   for this level of theory" — plain absence, per §2.6's careful wording;
   the structural-reason context in §2.6 is this plan's explanation for the
   builder, not copy for the page.
5. **Usage.** "Calculations at this level of theory" —
   `calculations/search?lot_ref=…` results, same as v1's §4.2 anchor page,
   reusing whatever component the calculation-search results already
   render elsewhere, or a minimal `.data-table` if none exists yet.

### 4.3 `/methods/schemes/:ecsRef` and `/methods/frequency-scale-factors/:fsfRef`

Thin pages, one layer below §4.2, new in this revision (§3's third
architecture point). Each wraps the existing detail endpoint's response
almost directly:

- **Scheme page**: `energy-correction-schemes/{ref}?include=corrections,used_by,literature`
  already returns everything needed — core block, its LOT (nullable — when
  absent, say "not tied to a specific level of theory," the honest reading
  of a null FK, never "any level of theory" or blank), its full parameter
  table, its up-to-50 `used_by` applications with applied values (§2.5.1).
  This is near-zero new backend work: the endpoint is done, the frontend
  page is a thin wrapper matching the LOT page's own correction-table
  rendering (§4.2 item 3) so the two don't diverge in how an atom/bond
  table looks.
- **FSF page**: same shape, wrapping `frequency-scale-factors/{ref}?include=used_by,literature`.

Both are the link target for the two dead-ref sites found in §2.4
(`CalculationDetailPage.tsx`'s `EnergyCorrectionsSection`,
`EntryStatmechSection.tsx`'s `FrequencyScaleFactorDetail`), exactly as
`/methods/:lotRef` is the link target for every `lotLabel()` site.

### 4.4 Home page (unchanged from v1)

`ArchiveHomePage.tsx`'s methods card action text changes from `"Coming
soon"` to `"Open index →"`. `ArchiveHomePage.test.tsx`'s assertion of
`"Coming soon"` for Methods is the mutation to flip.

## 5. Backend gaps and the endpoints that close them

v1 identified one gap (`/meta/levels-of-theory`, its whole §5). This
revision keeps that need but changes its shape, and adds three more —
because a record page needs more than a vocabulary list needs.

### 5.1 Level of theory needs a real detail endpoint, not a `/meta/*` list

v1 proposed `GET /api/v1/scientific/meta/levels-of-theory` — a bare
`{"results": [...]}` list, same family as `/meta/software`. That shape is
right for §4.1's index (cheap, no envelope, no include tokens) but **wrong
for §4.2's record page**, which needs the same kind of response FSF and ECS
detail endpoints already give: a core block, an `evidence_summary`, and
`include=` tokens for the sections that cost more to compute
(`corrections`, `used_by` — §2.5.1's pattern). Building the record page on
top of a `/meta/*` list would mean re-deriving that shape client-side or
making 3-4 follow-up calls per page load (§5.3 shows exactly this problem
already exists for software).

**Revised recommendation: promote `level_of_theory` to a full scientific
reference entity, matching FSF and ECS exactly** —

```
GET /api/v1/scientific/level-of-theories/{level_of_theory_ref_or_id}
GET /api/v1/scientific/level-of-theories/search
POST /api/v1/scientific/level-of-theories/search
```

- Core block: `level_of_theory_id` (internal-id-gated), `level_of_theory_ref`,
  `method`, `basis`, `dispersion`, `solvent`, `solvent_model`, `spin_treatment`
  (§5.4), `label`, `lot_hash` (behind `include=internal_ids` or a
  `RefsDisclosure`, consistent with how ECS/FSF handle `*_hash`-shaped
  values today — check the existing pattern at implementation time rather
  than inventing a new one).
- `evidence_summary`: `calculation_usage_count` (usage-derived, `INNER
  JOIN` from `calculation.level_of_theory_id` — same rule as the 2026-08
  software/workflow-tool fix, so a LOT with zero attributing calculations
  does not appear in `search` results at all), `has_correction_schemes`,
  `has_frequency_scale_factors`, `distinct_software_count`.
- `include=correction_schemes`: the LOT's `energy_correction_scheme` rows
  **joined directly on `level_of_theory_id`**, not on method/basis text —
  this is the fix for §2.2's "no `lot_ref=` filter on ECS search" gap,
  solved by not needing that filter at all: the join happens inside this
  endpoint's own query, where the FK is available, instead of being
  exposed as a public filter parameter on `energy-correction-schemes/
  search`. (§5.5 explains why adding `lot_ref=` there directly is not
  proposed.)
- `include=frequency_scale_factors`: same join pattern against
  `frequency_scale_factor.level_of_theory_id`, returned as a deduplicated
  list (one row per distinct `(scale_kind, value)`, each carrying the list
  of underlying `frequency_scale_factor_ref`s and their workflow-tool-
  release provenance — §4.2 item 4's "don't collapse silently" requirement
  belongs in the endpoint response, not left to the frontend to compute
  from 10 near-identical rows).
- `include=software`: §5.3.
- `include=used_by`: bounded calculation list, same shape as
  `calculations/search?lot_ref=…` — may be able to delegate to the
  existing search rather than reimplementing it; check at implementation
  time whether the search service can be called internally.

**Search** mirrors ECS/FSF's search pattern (`method`, `basis`, `dispersion`,
`solvent`, `spin_treatment`, `has_correction_schemes`,
`has_frequency_scale_factors` filters; `include_rejected`/
`include_deprecated`/`min_review_status` accepted as no-ops for shape
parity, same as FSF/ECS since LOT is equally non-reviewable) and becomes
the backing for §4.1's index instead of a `/meta/*` list — **this
supersedes v1's `/meta/levels-of-theory` recommendation entirely**; do not
build both. `/meta/methods` and `/meta/basis-sets` stay as they are for
now (still used by the browse-filter typeahead elsewhere on the site,
per `vocabApi.ts` in §2.2) — their 416×-wrong `count` field is a real bug,
independent of this plan, but not one a caller displays anywhere today
(§2.2's own observation from v1, still true) and fixing it is out of scope
here; noted so it is not silently forgotten.

### 5.2 `spin_treatment` still needs to land on `LevelOfTheorySummary` — unchanged need from v1, now also on the new core block

Add `spin_treatment: SpinTreatment` to
`LevelOfTheorySummary` (`scientific_common.py:223`) — every existing
LOT-citing read gets it for free, matching v1's original reasoning — *and*
to the new `level_of_theory` core block in §5.1, since that block is being
built fresh and should not repeat the omission DR-0034 left behind
(`display`'s own docstring already anticipates spin treatment as a
distinguishing field it cannot itself carry — §2.3).

### 5.3 New: a level-of-theory-scoped software/workflow-tool aggregation

§2.1's software column and §5.1's `include=software` both depend on this.
**No existing endpoint answers "which software (and version) ran
calculations at this level of theory, and how many."** `/meta/software`
and `/meta/workflow-tools` (`meta.py:45-79`) take an optional
`record_kind` (species vs. transition-state) but no LOT scoping at all.
Measured cost of not having it: building §2.1's software column by hand
took **12 `calculations/search?lot_ref=…&software=…` calls**, one per
(LOT, package) pair, each a separate paginated request — workable for a
4×3 archive, not workable as a page-load pattern once either axis grows.

**Closes with:** extend `list_software` / `list_workflow_tools`
(`app/services/scientific_read/meta.py`) with an optional
`level_of_theory_ref` parameter (or build the equivalent query directly
inside §5.1's `include=software` resolver — check at implementation time
whether reusing the existing service function with an added filter, or a
dedicated LOT-scoped query, is the smaller diff), same usage-derived
`INNER JOIN` discipline as the existing endpoints. Response shape:
`{software, version, calculation_count}` rows — §2.1's per-LOT column
is exactly this, pre-computed instead of assembled by 12 client calls.

### 5.4 Non-goal, restated from v1: `lot_ref=` on `energy-correction-schemes/search` and `frequency-scale-factors/search`

v1 named this as a smaller, not-proposed gap for `species/browse` and
`transition-states/browse`; the same reasoning now also covers ECS/FSF
search directly. **Not proposed here.** §5.1's `include=correction_schemes`
/ `include=frequency_scale_factors` solve the actual need (get a LOT's
schemes/FSFs) by querying from the LOT side, where the join is exact
(`level_of_theory_id` FK) rather than approximate (`method`/`basis` text
match, which §2.3 already disqualifies as an identity). Adding `lot_ref=`
directly to the two search endpoints would be redundant with §5.1 and
would reopen the text-vs-identity ambiguity those endpoints currently
avoid by not offering it. If a future consumer needs "schemes for this LOT"
as a standalone search rather than through the LOT detail page, revisit
then.

### 5.5 What does *not* need new backend work

Restated because it's easy to lose in the size of §5.1–5.3: the entire
correction-reference read layer (§2.5.1) — detail and search for both FSF
and ECS, the atom/bond/component param tables, the `used_by` applied-value
lists, public refs, handle resolvers, tests — **already exists and needs
no backend change** to support §4.3's thin scheme/FSF pages. Only the
level-of-theory side (§5.1–5.3) is new work.

### 5.6 Schema and migration impact

No schema or migration change. `spin_treatment` (§5.2) already exists on
`level_of_theory` (added by the migration DR-0034 describes) — this is a
response-schema-only addition, additive, no new column. §5.1's new routes
and §5.3's new aggregation are read-only service/route/schema work over
existing tables.

**Regen steps:** `UPDATE_OPENAPI_GOLDEN=1 conda run -n tckdb_env pytest
tests/api/test_openapi_snapshot.py`; `schema.dbml` untouched (no ORM
change); Python client (`clients/python/`) gains typed accessors for the
new endpoints if the client's existing vocabulary-method pattern extends
cleanly — check `scientific_types.py` / `client.py` at implementation
time, bump the package version per house convention if it does
(`feedback_tckdb_client_version_bump`).

## 6. Frontend work

Files (new unless noted): `frontend/src/pages/MethodsIndexPage.tsx`,
`frontend/src/pages/LevelOfTheoryPage.tsx` (§4.2 — substantially bigger
than v1's thin anchor page: identity block, software section, correction
tables, FSF section, usage list),
`frontend/src/pages/CorrectionSchemePage.tsx`,
`frontend/src/pages/FrequencyScaleFactorPage.tsx` (§4.3, both new),
`frontend/src/api/methodsApi.ts` (wraps §5.1's new level-of-theory
endpoints, §5.3's software aggregation, plus the existing
`energy-correction-schemes`/`frequency-scale-factors` detail endpoints —
`app/api/methodsApi.ts` is now a bigger surface than v1 scoped it),
`frontend/src/methods.css`; edited: `App.tsx` (`/methods` →
`MethodsIndexPage`; new routes `/methods/:lotRef` →
`LevelOfTheoryPage`, `/methods/schemes/:ecsRef` → `CorrectionSchemePage`,
`/methods/frequency-scale-factors/:fsfRef` → `FrequencyScaleFactorPage`),
`ArchiveHomePage.tsx` (§4.4), `ArchiveHomePage.test.tsx`,
`RecordPlaceholderPage.tsx` (drop the `kind="Methods"` call site — check
other consumers before deleting the component, same v1 caution).

Every `lotLabel()` render site named in §2.4 (11 files) **plus the two
correction-ref sites found in this revision**
(`CalculationDetailPage.tsx`'s `EnergyCorrectionsSection`,
`EntryStatmechSection.tsx`'s `FrequencyScaleFactorDetail`) are edited in
the same PR to wrap their ref output in a `<Link>`: LOT refs to
`/methods/:lotRef`, scheme refs to `/methods/schemes/:ecsRef`, FSF refs to
`/methods/frequency-scale-factors/:fsfRef`. Falls back to unlinked text
when the ref is absent, same rule as v1.

**A shared correction-table component is worth extracting once, not
copy-pasted twice** — §4.2 item 3 (LOT page's inline scheme tables) and
§4.3's standalone scheme page render the same atom/bond/component param
shapes. Build one `CorrectionSchemeTable` component (kind-dispatched:
atom-keyed, bond-keyed, or the four Melius sub-tables) and use it in both
places, so the two views cannot silently diverge in how a parameter table
looks.

**Data loading.** `LevelOfTheoryPage` makes one call to §5.1's detail
endpoint with `include=correction_schemes,frequency_scale_factors,software,used_by`
— a single round trip for the whole page, unlike v1's software/workflow-
tool section pattern (1+3+1+1 calls) which stays as-is for §4.1's index
only. `CorrectionSchemePage`/`FrequencyScaleFactorPage` each make one call
to their existing detail endpoint with `include=corrections,used_by,literature`
/ `include=used_by,literature`.

## 7. Slicing into PRs

Three PRs — one more than v1's two, because the backend surface grew
(§5.1's new route family) and the frontend surface grew (two new page
types, a shared table component).

**PR 1 — level-of-theory detail/search endpoint (§5.1, §5.2).** Owned:
new `backend/app/api/routes/scientific/level_of_theory.py` (or added to
an existing scientific router file — name at implementation time
following the existing per-entity file convention, cf.
`corrections.py`), new service files mirroring
`energy_correction_schemes.py` / `energy_correction_schemes_search.py`,
`backend/app/schemas/reads/scientific_level_of_theory.py` (new),
`scientific_common.py` (`spin_treatment` on `LevelOfTheorySummary`),
handle resolver in `handles.py`, tests mirroring
`test_api_scientific_corrections.py`'s coverage matrix, OpenAPI golden.
Red first: `/scientific/level-of-theories/{ref}` returns the correct core
block including `spin_treatment` for a seeded LOT; a LOT with zero
attributing calculations does not appear in `search` (mutate the
join to `OUTER` and watch it fail — same discipline as §5.1 states);
`include=correction_schemes` on the live-shaped fixture (two schemes, one
LOT) returns exactly those two, joined on `level_of_theory_id`, not on
method/basis text (constructed fixture: two LOTs sharing a method/basis
pair but differing `spin_treatment`, only one carrying a scheme — the
wrong-join mutation returns both). Reviewer: confirm against the live
archive that all four LOTs resolve, and that the four calculation-usage
counts equal 416/78/39/39. Deploy: API image, then Pi.

**PR 2 — LOT-scoped software aggregation (§5.3).** Owned:
`app/services/scientific_read/meta.py` (or a new module if the diff is
cleaner standalone — decide at implementation time),
`level_of_theory.py`'s `include=software` resolver (depends on PR 1's
route existing, so ordered after it, but small and independently
testable). Red first: for the live-shaped fixture (4 LOTs, 3 software
packages, one 1:1:1 mapping), `include=software` on each LOT returns
exactly the one package/version pair measured live in §2.1 — b3lyp/
def2tzvp → Gaussian 16 (416), wb97xd/def2tzvp → Gaussian 09 (78),
CCSD(T)-F12/cc-pVTZ-F12 → ORCA (39), MRCI+Davidson/aug-cc-pV(T+d)Z →
Molpro (39); a software package with zero calculations at a given LOT is
absent from that LOT's list (not zero-count). Reviewer: spot-check two of
the four against the 12-manual-call cross-check this plan already ran
(§2.1), confirm the endpoint's numbers match without re-deriving them by
hand. Deploy: API image, then Pi (can ship in the same deploy as PR 1 or
separately — no ordering constraint on the Pi side once both are merged).

**PR 3 — frontend (§4, §6).** Owned: `MethodsIndexPage.tsx`,
`LevelOfTheoryPage.tsx`, `CorrectionSchemePage.tsx`,
`FrequencyScaleFactorPage.tsx`, `CorrectionSchemeTable.tsx` (shared),
`methodsApi.ts`, `methods.css`, `App.tsx`, `ArchiveHomePage.tsx` + test,
and the link-wrapping edit across the 13+ dead-ref sites in §2.4/§6. Red
first: `/methods` DOM shows 4 LOT rows grouped by `level_of_theory_ref`
(mutation: group by `(method, basis)` text instead, watch a constructed
two-LOTs-same-method fixture collapse to 3 rows and a test that asserts 4
distinct rows fail); `/methods/:lotRef` for b3lyp/def2tzvp shows both
correction schemes as real parameter tables (8 atom rows, 45 bond rows)
matching the live values in §2.5.2 exactly, not summarized or truncated;
`/methods/:lotRef` for wb97xd shows "No atom-energy correction scheme is
deposited for this level of theory" (mutation: change the wording to "not
applicable," watch a test asserting the house rule fail, same pattern v1
specified); the 10 near-duplicate FSF rows for b3lyp/def2tzvp render as
one value with a provenance disclosure, not 10 visible rows (mutation:
remove the dedup step, watch a test counting rendered FSF value nodes
fail); every dead-ref site's existing rendered text is byte-identical
before and after, now wrapped in an anchor (same role="link" mutation
check v1 specified). Reviewer: headless Chrome at four widths, both
themes; walk from a calculation detail page's `energy_correction_scheme_ref`
through to `/methods/schemes/ecs_…` and back; walk from
`/methods` through a LOT with no scheme (wb97xd) and confirm the absence
wording; confirm the CCSD(T)-F12/ORCA and MRCI+Davidson/Molpro software
rows against the live payload. Deploy: frontend image.

## 8. Risks and open decisions for the owner

- **Grouping key for the index**: *settled* — ruling 1 in §0. No longer an
  open question; kept here only as a pointer so a future reader doesn't
  go looking for it as unresolved.
- **`display` string collisions**: unchanged from v1 — not possible today
  (§2.1's table has four distinct `display` strings), but recommend
  showing spin treatment as a visible field on the index the day two LOTs
  share a `display` string, per v1's original reasoning. Owner should
  still rule on how an `unknown` spin treatment reads to a user (blank,
  "not recorded," "unspecified") — unresolved from v1, unaffected by this
  revision.
- **Casing/normalization**: unchanged from v1, still deferred by DR-0034,
  now sharpened by §2.3's finding that RMG's own key convention differs
  from TCKDB's stored strings — if the page ever cites RMG-derived values
  as provenance (§2.5.2's exact-match finding invites this), the two
  naming conventions sitting side by side on one page is a real readability
  question the owner should weigh in on, separate from the identity
  question DR-0034 already settled.
- **Does the scheme page need its own review/trust treatment eventually?**
  New question from this revision. `energy_correction_scheme` and
  `frequency_scale_factor` are non-reviewable today (§2.5.1's spec doc,
  "Review/trust behavior" section) — no submission, no approval flow. A
  scheme page that shows "82 species entries rely on this" without any
  review signal is honest today (nothing is reviewed), but if these tables
  ever gain a review flow, the scheme/FSF pages built here inherit
  whatever that flow adds. Not blocking; named so it isn't rediscovered
  cold.
- **`RecordPlaceholderPage` component fate**: unchanged from v1 — check at
  implementation time whether any other route still uses it before
  deleting.
- **Should the "software" section on the LOT page (§4.2 item 2) show
  workflow-tool release too, not just software release?** Measured: every
  live FSF row that has `has_workflow_tool_source: true` carries a
  distinct ARC release (§2.5.1's 10-rows finding), so a LOT-level "observed
  provenance" section arguably wants both dimensions, not just software.
  Not decided here — sizing this against the archive's single workflow
  tool (ARC, 1 tool) suggests it is low-value today, but flagged since
  §5.1's `include=software` naming should not preclude adding
  `include=workflow_tools` later without a route change.

## 9. First-slice acceptance criteria

`/methods` loads without a placeholder, shows exactly the archive's real
levels of theory (grouped by `level_of_theory_ref`), software, and
workflow tools with correct usage counts. Every `level_of_theory_ref`
rendered anywhere else on the site is a working link into
`/methods/:lotRef`, which shows real correction-scheme parameter tables
and a real frequency-scale-factor value for the levels of theory that have
them (b3lyp/def2tzvp today), and a plain, correctly-worded absence
statement for the levels that don't (wb97xd, CCSD(T)-F12,
MRCI+Davidson) — never a fabricated "not applicable," never a blank
row. Every `energy_correction_scheme_ref` and `frequency_scale_factor_ref`
rendered anywhere else on the site (`CalculationDetailPage.tsx`,
`EntryStatmechSection.tsx`) is a working link into its own thin page. The
home page card no longer says "Coming soon." No new record kind, no
fabricated review state, no silent collapsing of the 10 distinct
frequency-scale-factor rows into one without showing their separate
provenance, no invented "not applicable" where the API only knows "not
recorded" or "not deposited."
