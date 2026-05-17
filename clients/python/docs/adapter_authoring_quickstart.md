# Adapter authoring quickstart

Status: **producer-facing quickstart.** No code or schema changes
in this document. Audience: developers writing an adapter for a
workflow tool (ARC, RMG, KinBot, AutoTST, a custom Gaussian /
ORCA / Arkane pipeline, …) that needs to push data into TCKDB via
`tckdb-client`.

This is the *short* path. It points at three boundary documents
and four working example demos so an adapter author can ship a
first upload without reading every design note first.

---

## 1. Purpose

An adapter's job is to map *workflow-specific* outputs into TCKDB
upload objects. The adapter knows where files live, which job is
opt / freq / sp, which structure the workflow stands behind, and
which artifacts matter. The builder layer takes structured Python
values and produces a payload; the thin client posts it; the
backend stores it.

You should **not** need to know:

- every raw API endpoint URL;
- the wire-level Pydantic schema field names;
- the backend's deduplication or identity logic.

You **should** know the four layers below (§2), the three
boundaries (§3), and the minimal adapter flow (§4). The rest is
worked examples (§10).

---

## 2. The layering model

```text
workflow outputs
   ↓        (adapter-specific interpretation — your code)
tckdb-client builders          (structured types, local validation,
   ↓                            payload assembly, diagnostics)
tckdb-client transport         (HTTP, auth, idempotency, retries)
   ↓
TCKDB API                      (schema, identity, dedupe, auth,
                                moderation, artifact storage)
```

Layer-by-layer:

- **Adapters** know the workflow — file layout, conventions, which
  conformer the workflow stands behind, which artifacts to ship.
- **Builders** (`tckdb_client.builders`) know the TCKDB upload
  objects (`Species`, `Calculation`, `ComputedSpeciesUpload`, …)
  and how to validate them locally.
- **Thin client** (`TCKDBClient`) knows HTTP, auth, idempotency,
  and the two-phase artifact upload contract.
- **The backend** remains the only authoritative validator —
  schema, identity, deduplication, moderation, artifact hashing.

The rule for adapter code: stay above the builder line. Don't
reach into builder internals; don't reach below the client.

---

## 3. The three boundary rules

Three short read-or-skim documents pin the boundaries adapters
must respect:

- [`parser_validation_boundary.md`](parser_validation_boundary.md)
  — **Do not put parser logic in generic builders.** ESS file
  parsing (Gaussian / ORCA / Arkane / RMG) is an upper layer above
  the builders and is intentionally not part of the base
  `tckdb-client` install.
- [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  — **Do not use TCKDB as a workflow conformer-search scratchpad.**
  One scientifically meaningful conformer / geometry per species
  upload. A workflow that explored ten rotamers and converged on
  one ships the one; the candidate list is not a TCKDB record.
- [`calculation_note_conventions.md`](calculation_note_conventions.md)
  — **Do not use `Calculation.note` as a replacement for artifacts
  or structured provenance.** Notes are short one-line annotations;
  bytes ride on artifacts; structured fields are for everything the
  backend can index, dedupe, or validate.

These rules govern adapter design. If you find yourself fighting
them, ask whether a builder gap is missing (file an issue) — don't
work around the policy with creative field repurposing.

---

## 4. Minimal adapter flow

The recommended six-step shape, in code:

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    SourceCalculations,
    Species,
    Thermo,
)

# 1. Interpret workflow output.
#    Read your workflow's manifest / output files / object model and
#    decide which structure to ship.
opt_geometry_xyz, software, lot, energies = my_workflow_outputs()

# 2. Create builder objects.
sr   = SoftwareRelease(software=software.name, version=software.version)
lot_ = LevelOfTheory(method=lot.method, basis=lot.basis)
opt  = Calculation.opt(sr, lot_, output_geometry=Geometry.from_xyz(opt_geometry_xyz),
                       final_energy_hartree=energies["opt"], converged=True,
                       label="opt", note="converged structure from workflow")
freq = Calculation.freq(sr, lot_, n_imag=0, zpe_hartree=energies["zpe"],
                        depends_on=opt, label="freq")
sp   = Calculation.sp(sr, lot_, electronic_energy_hartree=energies["sp"],
                      depends_on=opt, label="sp")

sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
upload  = ComputedSpeciesUpload(
    species=Species(smiles="CCO", charge=0, multiplicity=1, label="ethanol"),
    calculations=[opt, freq, sp],
    primary_calculation=opt,
    thermo=Thermo.nasa(coeffs_low=[...], coeffs_high=[...],
                       t_low=200, t_mid=1000, t_high=5000,
                       h298_kj_mol=-234.0, s298_j_mol_k=281.6,
                       source_calculations=sources.only("opt", "freq", "sp")),
)

# 3. Attach artifacts (logs, decks, scan tables — bytes stay local
#    until phase 2).
opt.add_artifact("ethanol_opt.log", kind="output_log")

# 4. Inspect summary + diagnostics before posting.
print(upload.summary().to_text())
for diag in upload.emission_diagnostics():
    print(f"[{diag.level}] {diag.code}: {diag.path}")

# 5. Upload the scientific payload.
with TCKDBClient(base_url, api_key=api_key) as client:
    result = client.upload(upload, warn_on_dropped_fields=True)

# 6. Upload artifacts in the second phase.
    plan = upload.artifact_plan(result)
    client.upload_artifacts(plan, idempotency_key_prefix="my-adapter:")
```

This sequence is faithfully exercised by every demo listed in §10.
Adapters that grow more sophisticated (multi-species reaction
uploads, transition states, kinetics, transport) extend the same
shape — never replace it.

---

## 5. Mapping responsibilities

What an **adapter decides**:

- Which species / reaction is being submitted (chemistry shape).
- Which geometry / result the workflow stands behind (one per
  species — see §3 conformer boundary).
- Which calculations are `opt` / `freq` / `sp`, including their
  software releases, levels of theory, and dependency edges.
- Which `source_calculations` support each scientific block
  (`Thermo`, `Statmech`, `Kinetics`, `Transport`).
- Which artifacts should be attached and at which `kind`.
- Which labels are useful and stable (e.g. `"ethanol opt"`).
- Which workflow-side notes are worth preserving on the builder
  (per the §3 conventions doc).

What an **adapter does not decide**:

- Database IDs — every primary key is server-minted.
- Deduplication — the backend collapses identical records.
- Moderation status — approved / pending / rejected lives
  server-side, not in the adapter.
- Server-side chemical identity normalisation (SMILES canonicals,
  InChI, hash equivalence).
- Server-side artifact hashes — the client / backend compute them
  at upload time; adapters do not need to pre-hash.
- Wire-shape canonicalisation — `to_payload()` owns that.

If an adapter wants to control something in the second list, the
right move is almost always to *ask the backend* (via a search /
lookup call) and let the server's answer drive subsequent
adapter logic — not to reimplement the policy locally.

---

## 6. Source calculations

`SourceCalculations` is the public helper for the repeated
`source_calculations=[(role, calc), …]` pattern:

```python
from tckdb_client.builders import SourceCalculations, Thermo, Statmech

sources = SourceCalculations(opt=opt, freq=freq, sp=sp)

thermo   = Thermo.nasa(..., source_calculations=sources.only("opt", "freq", "sp"))
statmech = Statmech(...,    source_calculations=sources.only("opt", "freq"))
```

For bimolecular cases (two reactants / two products), pass a list:

```python
kin_sources = SourceCalculations(
    reactant_energy=[ch4_sp, oh_sp],
    product_energy=[ch3_sp, h2o_sp],
    ts_energy=ts_sp,
    freq=ts_freq,
)
kinetics = Kinetics.modified_arrhenius(..., source_calculations=kin_sources.as_list())
```

Properties to remember:

- `SourceCalculations` is **optional**. The existing three
  source-calc shapes (dict, dict-of-list, list-of-tuples) keep
  working unchanged.
- It does **not** infer provenance. The adapter still chooses
  which roles each block references, explicitly, at the call site.
- `.only(*roles)` raises on a misspelled role token (typo guard).
- `.add(role, calc)` exists for role tokens that aren't valid
  Python identifiers (e.g. `sources.add("k-inf", sp_kinf)`).

Full design rationale and rejected alternatives:
[`source_calculation_ergonomics.md`](source_calculation_ergonomics.md).

---

## 7. Artifacts

Artifacts (input decks, output logs, checkpoint files, scan
tables) ride a **two-phase** flow:

```python
# Phase 1 — scientific payload only (no artifact bytes).
result = client.upload(upload, warn_on_dropped_fields=True)

# Phase 2 — artifact bytes, once the server has assigned calc IDs.
plan = upload.artifact_plan(result)

# Easiest to debug: one artifact per POST.
client.upload_artifacts(plan, idempotency_key_prefix="my-adapter:run-42")

# Fewer HTTP requests: one batch per calculation_id.
# Returns list[ArtifactUploadBatchResult] (one per calc group).
client.upload_artifacts(
    plan,
    idempotency_key_prefix="my-adapter:run-42",
    batch_by_calculation=True,
)
```

Key facts:

- Artifacts are **not embedded** in the scientific payload. Phase 1
  ships structured data; phase 2 ships bytes.
- Artifacts preserve the **original files** for auditability —
  workflow logs, conformer-search histories, raw deck text, etc.
  The conformer-boundary policy (§3) explicitly relies on this:
  search-history detail lives on artifacts, not on the upload
  schema.
- The `idempotency_key_prefix` groups artifacts under a single
  retry namespace; the server uses it to make `upload_artifacts`
  safely re-runnable.
- For offline previews — CI dry-runs, adapter tests — use
  `upload.artifact_plan_preview()` to generate deterministic
  mock IDs without a server round-trip.

### Batch mode (`batch_by_calculation=True`)

- **Default remains sequential one-artifact-per-request.** Start
  there; opt into batch mode once the simple flow works.
- **Returns** `list[ArtifactUploadBatchResult]` — one record per
  `calculation_id` group, in caller-supplied group order. Each
  record carries the server's `ArtifactsUploadResult` body
  verbatim, plus the bundle-local `calculation_keys` the builder
  layer minted, so adapter code can map a batch result back to its
  plan without re-walking the original list.
- **Relies on backend artifact-batch atomicity.** Each per-calc
  POST is server-side atomic: any per-artifact failure (decode,
  hash mismatch, ESS signature, aggregate size cap, pass-2 storage
  outage) rejects the whole batch with no DB rows and no S3 leaks.
  See the backend's `persist_artifact_batch` two-pass design and
  its `TestBatchAtomicity` / `TestStorageFailure` suites in
  `backend/tests/api/test_api_calculation_artifacts.py`.
- **Idempotency keys are per-batch** in this mode:
  `f"{prefix}:{first_calculation_key}:artifact-batch"`, one key per
  group, deterministic across runs.
- **Validation runs before dispatch** in both modes — a malformed
  plan (bad `calculation_id`, missing path, non-file path, empty
  `kind`) raises before the first HTTP request fires.

---

## 8. Pre-upload inspection

Two viewer surfaces let an adapter sanity-check an upload before
posting:

```python
print(upload.summary().to_text())          # human-readable preview
data = upload.summary().to_dict()          # structured: stable keys

for diag in upload.emission_diagnostics():
    print(diag.code, diag.path)
```

Treat each surface for its purpose:

- **`summary().to_dict()`** keys are public-beta stable; use them
  in CI assertions (e.g. *"there must be one kinetics record"*).
- **`summary().to_text()`** formatting may change between minor
  versions; do not regex it.
- **`emission_diagnostics()`** are *not* errors by themselves —
  they flag data the builder accepted locally that today's wire
  schemas can't carry, plus the two-phase artifact reminder.
  Adapter CI may promote warning-level diagnostics to failures
  with `client.upload(..., warn_on_dropped_fields=True)` and
  `warnings.simplefilter("error", UserWarning)` — but doing so is
  an *adapter policy choice*, not a builder default.

Stability layering for both surfaces:
[`builder_summary_design.md`](builder_summary_design.md) §7.

---

## 9. Raw payload escape hatch

If the builder layer does not yet expose a backend-supported field
the adapter needs, the **raw payload path** remains supported.
`TCKDBClient` accepts a payload dict directly via the per-endpoint
methods (or, for the bundle endpoints, via the same `upload(...)`
call with a hand-shaped dict). The raw path is the same code path
the builders themselves use to talk to the server.

Use the escape hatch when:

- A backend field genuinely exists but the builder has no kwarg
  for it yet (newer wire shape than the builder generation knew
  about).
- A one-off submission shape is too unusual to justify a builder
  feature.
- You're integration-testing the wire shape directly.

Use it **sparingly**. If the same hand-shaped pattern recurs
across multiple submissions, file a builder gap — that's a
recurring-need signal worth landing in the public API. The raw
payload path is a fallback, not the default.

The raw path does **not** bypass the boundary rules in §3 (the
conformer-boundary policy applies to raw uploads too — the
backend's `BundleSpeciesIn.conformers: list[ConformerInBundle]`
shape is intentionally available to raw producers but should not
be used to ship workflow-search candidate lists).

---

## 10. Example references

Three demos and a notebook cover the recommended adapter shape end
to end:

- [`examples/builder_computed_species_demo.py`](../examples/builder_computed_species_demo.py)
  — **start here.** Single-species upload: one `Species`, one
  `opt + freq + sp` triple, `Thermo` + `Statmech`, attached
  artifacts, summary + diagnostics + plan preview. The smallest
  realistic adapter shape.
- [`examples/builder_computed_reaction_demo.py`](../examples/builder_computed_reaction_demo.py)
  — multi-species reaction upload: three `Species`, a
  `TransitionState`, modified-Arrhenius `Kinetics` with duplicate
  source roles, per-species thermo / statmech / transport.
- [`examples/builder_arc_style_dry_run.py`](../examples/builder_arc_style_dry_run.py)
  — workflow-shaped end-to-end example: four species
  (H-abstraction), mixed Gaussian-opt / ORCA-SP releases,
  `SourceCalculations`, `Calculation.note` annotations, two
  attached artifacts. Closest thing to a *realistic adapter*
  without depending on any workflow tool.
- [`examples/builder_arc_style_dry_run.ipynb`](../examples/builder_arc_style_dry_run.ipynb)
  — Jupyter walk-through of the same flow, split into named
  sections (imports → workflow mapping → artifact plan → optional
  live upload).

All four demos run offline without env vars (no network) and
exercise the same public-API surface this guide describes.

---

## 11. Non-goals

Reiterated so this guide stays small:

- **No parser implementation.** ESS file parsing belongs above
  the adapter layer (see §3).
- **No backend schema change.** This guide is producer-facing only.
- **No ARC-specific (or other workflow-tool-specific) defaults.**
  Workflow habits stay in the *adapter*; the builder layer stays
  generic.
- **No endorsement of workflow scratchpad uploads.** TCKDB is not
  a candidate-list archive (see §3 conformer boundary).
- **No replacement for backend validation.** Local builder
  validation is convenience only; the server is the source of
  truth.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — full builder spec;
  factory signatures, payload assembly, and emission diagnostics
  live there.
- [`builder_api_stability.md`](builder_api_stability.md) —
  public-beta surface and deprecation policy.
- [`source_calculation_ergonomics.md`](source_calculation_ergonomics.md)
  — `SourceCalculations` design rationale.
- [`builder_summary_design.md`](builder_summary_design.md) — the
  `summary()` surface, stability layering, and what is intentionally
  excluded from the digest.
