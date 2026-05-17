# Conformer semantic boundary

Status: **policy.** Applies to the public-beta builder layer
(`tckdb_client.builders`) and to anyone extending it. Companion to
[`builder_api_mvp.md`](builder_api_mvp.md) and
[`builder_api_stability.md`](builder_api_stability.md).

This document spells out *what TCKDB is and is not for, with respect
to conformers* — and therefore why the builder layer ships one
conformer per species upload by design, not by accident.

---

## 1. TCKDB is not a conformer-search scratchpad

TCKDB should not receive every conformer candidate considered by a
workflow tool.

Workflow-local conformer searches, ranking, filtering, and
"selected from N options" logic are workflow provenance /
debugging details, not the default scientific submission model.

In particular, if a tool explored ten rotamers and converged on one,
TCKDB should hear about the converged one — not the ten the tool
walked past on the way. The builder's job is to make submitting the
one easy. It is **not** the builder's job to be a faithful mirror
of a workflow's internal candidate list.

This is the design principle every other section follows from.

---

## 2. Default builder model

The builder's default model is **one scientifically relevant
conformer / geometry per species upload**.

```python
ComputedSpeciesUpload(
    species=ethanol,
    calculations=[opt, freq, sp],     # one optimised structure
    primary_calculation=opt,
)
```

`opt + freq + sp` here describes a single converged conformer plus
its supporting calculations. For a workflow that ran a conformer
search and picked one — by lowest electronic energy, lowest Gibbs
free energy, RMSD-pruning, or any other producer-side criterion —
that picked conformer is what the upload contains. The workflow's
own selection logic is invisible to TCKDB and stays that way.

The same applies to the reaction-side path: each species in a
`ComputedReactionUpload.species_calculations` mapping is associated
with one geometry / one opt. Per-reaction TS handling is similarly
single-structure (`TransitionState` + its primary opt). The builder
never bundles N candidates with one "selected" flag.

This rule is endpoint-agnostic — it applies equally to
computed-species, computed-reaction, primitive uploads, and any
future endpoint that grows out of the same producer pattern.

---

## 3. No `selected_conformer` concept

The builder must not introduce a global `selected_conformer` field
on `ComputedSpeciesUpload`, `ComputedReactionUpload`, or anywhere
else.

"Selected" is semantically ambiguous. A workflow tool might mean
any of:

- lowest electronic energy at the optimisation LoT,
- lowest electronic energy at a single-point LoT,
- lowest Gibbs free energy at 298 K,
- the conformer the tool's clustering chose as the cluster centroid,
- the conformer used to compute thermo,
- the conformer used to compute transport properties,
- the workflow-tool-preferred conformer,
- the curator-preferred conformer.

Surfacing one knob called `selected_conformer` collapses all of
those into an ill-defined field. The preferred model is the opposite:

> Scientific products and calculations *point to* the calculation
> / geometry that supports them.

TCKDB should know what a thermo / statmech / kinetics / transport
record was derived from — that information is already present in
`source_calculations` references on those blocks — and that's
where the question of "which calc, which geometry" gets answered
**per scientific product**, not via a generic option-set-plus-flag.

The corollary: there will not be a `Conformer` builder, a
`conformers=[…]` kwarg, or a `species_conformers={…}` mapping in
`tckdb_client.builders`. These names are listed in the upload
classes' docstrings as known-rejected alternatives so future
contributors don't reinvent them.

---

## 4. Multiple submissions for the same species

TCKDB **can** contain multiple independently submitted records for
the same species. That is **different from** one upload bundling
all conformer candidates from a workflow search.

The two cases that motivate the distinction:

| Case                                                                                   | Acceptable?                                       |
|----------------------------------------------------------------------------------------|---------------------------------------------------|
| Two users independently upload ethanol geometries / results.                           | **Yes.** Independent scientifically meaningful records, each with its own provenance trail. |
| One user uploads ethanol once today, re-optimises at a higher LoT next month, uploads again. | **Yes.** Independent records over time.            |
| One workflow run wants to ship 10 ethanol conformers it considered, marking one "selected". | **No.** Workflow scratchpad behaviour; bundle one upload around the conformer the workflow stands behind. |
| Two workflows independently arrived at distinct ethanol minima (different LoTs / methods) and both want to submit. | **Yes.** Each producer submits the structure their pipeline stands behind. |

The submission **count** is not the test. The test is whether each
record reflects an independent scientifically meaningful result.
If yes, submit. If the records describe one workflow's
internal-search candidates, don't.

---

## 5. Advanced edge cases

A narrow escape hatch exists, but it is **not** the default builder
API:

> If a future producer has a concrete scientifically meaningful need
> to submit multiple conformer-like records as part of one logical
> submission, that should be designed as a separate advanced feature
> with a clear scientific product requirement.
>
> It must not be framed as "upload all candidates and selected one".

This document does **not** define the API for that future feature.
A real producer requirement (e.g. a thermochemical fit that
genuinely consumes Boltzmann-weighted data across several conformer
geometries, where each conformer has its own published thermo and
the producer wants to ship all of it) is the entry condition. Until that requirement
exists in concrete form, no API is designed.

Workarounds available today:

- **Independent uploads.** As §4 notes, multiple submissions for the
  same species are first-class. The producer can issue several
  uploads, each describing one scientifically meaningful record,
  with shared external metadata (e.g. a common DOI in the
  `Literature` ref) tying them together at the curation layer.
- **Raw payload form.** The thin `client.upload(endpoint, payload_dict)`
  surface accepts the full backend schema, including
  `BundleSpeciesIn.conformers: list[ConformerInBundle]`. A producer
  with a one-time need can shape the payload by hand. This is
  deliberately not what the builder layer makes ergonomic.

---

## 6. Artifacts and provenance

Artifacts are the correct place to preserve **source files** for
auditability.

If the chosen geometry came from a workflow conformer search, the
uploaded artifacts may document the run (log files, input decks,
checkpoint files, ancillary CSVs). That's the right home for
"here's the trail that led to this geometry". The builder does **not**
turn that search history into first-class conformer records.

Stated as a rule:

> A workflow's conformer-search run log is an artifact. The
> conformer-search candidates are not records.

This keeps the auditability signal where it belongs (artifacts,
keyed by calculation, which are the wire-transport surface for
producer-supplied bytes) and keeps the queryable layer (conformer
groups, observations, assignment schemes on the backend) reserved
for scientifically meaningful structures.

---

## 7. Non-goals

- **No candidate-list builder API.** No `Conformer` value type, no
  `conformers=[…]` kwarg on `ComputedSpeciesUpload`, no
  `species_conformers={…}` mapping on `ComputedReactionUpload`. No
  workflow scratchpad behavior of any kind.
- **No `selected_conformer` field.** Not now, not in a future minor.
  The ambiguity it introduces (§3) is the whole reason this policy
  exists.
- **No ARC-specific conformer archive.** ARC is a workflow tool, not
  a privileged producer. The same boundary applies to any workflow
  tool — ARC, RMG, KinBot, hand-rolled scripts. ARC submits the
  conformer it stands behind, like everyone else.
- **No backend schema changes.** The backend's
  `BundleSpeciesIn.conformers: list[ConformerInBundle]` shape stays
  exactly as it is. The boundary is enforced *in the builder layer*
  — the schema's flexibility remains available to raw-payload
  producers, deliberately not surfaced through the builder.
- **No conformer search or ranking logic in the builder.** No
  selection algorithm, no scoring helpers, no Boltzmann weighting,
  no "default best" heuristics. The producer makes the scientific
  choice before reaching the builder.
- **No RDKit / CREST / ARC / xTB conformer deduplication in
  `tckdb-client`.** The client stays chemistry-library-free; any
  geometry de-duplication or clustering happens at the producer
  side or — for hosted instances — on the backend.

---

## 8. Open questions

A short, deliberate list. Each is something the project *could*
take up, but only with a concrete scientific requirement driving
the design:

- **Boltzmann-averaged thermo.** If a producer ships thermo derived
  from a Boltzmann average over several conformer geometries, is the
  single resulting thermo block adequate? Today's `Thermo` carries
  `source_calculations`; if those source calcs all come from one
  conformer's calcs, the answer is yes — and the producer ships
  one upload. Revisit if a real workflow stretches that and the
  schema-level shape needs to evolve.
- **Cross-LoT same-species records.** Are two submissions of the
  "same species" at very different levels of theory two records, or
  one record with multiple LoT-tagged calcs? Today: two records,
  consistent with §4. The backend's deduplication / curation layer
  is the right place to express the relationship.

Neither of these motivates a builder API change at the time of
writing.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the broader builder
  spec; the upload-class section notes that the single-conformer
  shape is policy, not a deferred feature.
- [`builder_api_stability.md`](builder_api_stability.md) — the
  public-beta surface and deprecation policy; the "conformer model"
  entry under "what may still change before v1" points at this doc.
- [`adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
  — the producer-facing quickstart that names this policy as one
  of the three boundary rules every adapter respects.
