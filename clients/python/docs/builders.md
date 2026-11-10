# Builder layer (public beta)

The package ships a builder layer under `tckdb_client.builders` that
constructs upload payloads from scientific Python objects and sends
them through the existing thin client. The thin HTTP client is
production-stable; the builder layer is **public beta / preview** —
recommended for early adopters who want a typed, validated upload
surface, with a known set of forward-compat gaps documented below.

The supported flows are computed-species upload (one species + its
opt/freq/sp + optional thermo / statmech / transport) and
computed-reaction upload (reactant/product species + TS + kinetics +
per-species thermo / statmech / transport).

See [`docs/builder_api_stability.md`](builder_api_stability.md)
for the public-beta API surface, deprecation policy, and the list of
things that may still change before v1.

### Conformer boundary

The builder intentionally models one scientifically meaningful
conformer/geometry per species upload. It is not intended for
uploading every conformer candidate a workflow considered. See
[`docs/conformer_semantic_boundary.md`](conformer_semantic_boundary.md).

### Source-calculation ergonomics

`SourceCalculations` is an opt-in helper that lets producers tag
calcs by role once and reuse them across `Thermo` / `Statmech` /
`Kinetics` / `Transport` blocks:

```python
from tckdb_client.builders import SourceCalculations, Thermo, Statmech

sources = SourceCalculations(opt=opt, freq=freq, sp=sp)

thermo   = Thermo.nasa(..., source_calculations=sources.only("opt", "freq", "sp"))
statmech = Statmech(...,    source_calculations=sources.only("opt", "freq"))
```

For duplicate-role cases (e.g. bimolecular kinetics with two
reactant SPs) pass a list value:

```python
kin_sources = SourceCalculations(
    reactant_energy=[ch3_sp, h_sp],
    product_energy=ch4_sp,
    ts_energy=ts_sp,
    freq=ts_freq,
)
kinetics = Kinetics.modified_arrhenius(..., source_calculations=kin_sources.as_list())
```

`.only(*roles)` emits entries in caller-requested role order and
raises on a misspelled role (typo guard). `.as_list()` emits all
entries in insertion order. Use `.add("k-inf", calc)` for role
tokens that aren't valid Python identifiers.

Full design rationale and rejected alternatives:
[`docs/source_calculation_ergonomics.md`](source_calculation_ergonomics.md).
Explicit lists, dicts, and dict-of-list shapes keep working
unchanged — the helper is purely additive.

### Parser / validation boundary

The base `tckdb-client` is and remains **parser-free**. It does not
read Gaussian, ORCA, Arkane, or RMG output files; it does not infer
charge / multiplicity from logs; it does not run RDKit identity
checks. ESS file parsing is an upper layer above the builders, and
is intentionally **not** part of the base install. See
[`docs/parser_validation_boundary.md`](parser_validation_boundary.md)
for the layering between builders, future parser helpers,
workflow-tool adapters, and the backend.

### `Calculation.note` conventions

`Calculation.opt/freq/sp(..., note="…")` is a builder-local free-text
annotation for producer/adapter ergonomics. It is preserved on the
builder (visible via `upload.iter_calculations()`) but **not emitted
on the wire** today. Producer conventions for what belongs in a note
(short one-line *why*, never logs / candidate-conformer narratives /
machine paths / scratchpad text) live in
[`docs/calculation_note_conventions.md`](calculation_note_conventions.md).

### Writing a workflow-tool adapter?

The
[`docs/adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
is the short producer-facing path. One document covers the four-layer
model (workflow → adapter → builders → client → backend), the three
boundary rules, the minimal six-step adapter flow, `SourceCalculations`
use, the two-phase artifact upload contract, the pre-upload inspection
surface (`summary()` + `emission_diagnostics()`), the raw-payload
escape hatch, and pointers to the worked example demos.

### Upload summary / describe API

`upload.summary()` returns a small `UploadSummary` value with two
emission methods — `.to_text()` (human-readable; formatting may
change between minor versions) and `.to_dict()` (public-beta
stable keys, JSON-serialisable). Both
`ComputedSpeciesUpload.summary()` and
`ComputedReactionUpload.summary()` are supported:

```python
summary = upload.summary()

print(summary.to_text())          # CLI / notebook / log preview

data = summary.to_dict()          # structured for tests + observability
assert data["artifact_count"] == 2
assert data["diagnostic_codes"]   # codes only — no long messages
```

The summary is a builder-side **viewer** of upload state; it is
not a substitute for `upload.to_payload()`, which remains the
canonical wire representation. Stability layering and the full
field list (per upload kind) are documented in
[`docs/builder_summary_design.md`](builder_summary_design.md).
No new dependencies, no impact on `to_payload()` — purely additive.

### Quickstart

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
)

sr  = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
lot = LevelOfTheory(method="B3LYP", basis="6-31G(d)")
opt = Calculation.opt(sr, lot, output_geometry=Geometry.from_xyz(out_xyz),
                      final_energy_hartree=-76.4, converged=True)
freq = Calculation.freq(sr, lot, input_geometry=Geometry.from_xyz(out_xyz),
                        n_imag=0, zpe_hartree=0.0214, depends_on=opt)

upload = ComputedSpeciesUpload(
    species=Species(smiles="O", charge=0, multiplicity=1),
    calculations=[opt, freq],
)

# Inspect what the bundle endpoint will / won't accept before sending.
for diag in upload.emission_diagnostics():
    print(f"[{diag.level}] {diag.code} @ {diag.path}: {diag.message}")

with TCKDBClient(base_url, api_key=api_key) as client:
    # warn_on_dropped_fields=True re-emits each warning diagnostic via
    # warnings.warn(...) before the request is dispatched.
    result = client.upload(upload, warn_on_dropped_fields=True)
```

End-to-end demos:

- [`examples/builder_computed_species_demo.py`](../examples/builder_computed_species_demo.py)
  — **start here.** Simplest single-species upload: one `Species`,
  one `opt + freq + sp` triple, one `Thermo`, one `Statmech`, one
  `Transport`, two attached artifacts. ~250 lines of fully-typed
  example code with no domain modelling beyond the molecule itself.
- [`examples/builder_computed_reaction_demo.py`](../examples/builder_computed_reaction_demo.py)
  — multi-species reaction upload: three `Species` (CH3 + H → CH4),
  a `TransitionState`, modified-Arrhenius `Kinetics` with duplicate
  `reactant_energy` source roles, per-species thermo / statmech /
  transport, and two TS / species-side artifacts. Use this once the
  species demo makes sense.
- [`examples/builder_computed_reaction_demo.ipynb`](../examples/builder_computed_reaction_demo.ipynb)
  — Jupyter notebook walk-through of the reaction demo, section
  headings included, ideal for interactive exploration.
- [`examples/builder_arc_style_dry_run.py`](../examples/builder_arc_style_dry_run.py)
  — **ARC-style dry-run example.** Workflow-shaped data — four
  species (`CH4 + OH → CH3 + H2O`, H-abstraction), a TS, mixed
  Gaussian-opt / ORCA-SP releases, modified-Arrhenius kinetics
  with `SourceCalculations`, thermo + statmech on one product,
  and two attached artifacts — mapped into builders without
  requiring ARC itself. Demonstrates the `note=` kwarg on
  `Calculation.opt/freq/sp` for workflow-side context (local-only;
  not emitted on the wire). Uses only public APIs and respects the
  one-conformer-per-species
  [conformer boundary policy](conformer_semantic_boundary.md).
- [`examples/builder_arc_style_dry_run.ipynb`](../examples/builder_arc_style_dry_run.ipynb)
  — Jupyter notebook walk-through of the ARC-style dry-run, split
  into twelve named sections (imports → workflow mapping → artifact
  plan → optional live upload). Same flow as the `.py` sibling,
  same public-API contract, ideal for stepping through cell-by-cell.

All five demos:

- print the payload summary, emission diagnostics, an attached-artifact
  summary (via `upload.iter_calculation_entries(with_artifacts_only=True)`),
  and a mock-IDs plan preview (via `upload.artifact_plan_preview()`);
- short-circuit before any HTTP when `TCKDB_BASE_URL` /
  `TCKDB_API_KEY` are not set;
- with both env vars set, run the full two-phase flow —
  `client.upload(upload, warn_on_dropped_fields=True)` followed by
  `client.upload_artifacts(upload.artifact_plan(result), idempotency_key_prefix=…)`.

### What emits today

The matrix below is the source of truth for which blocks make it onto
the wire on which endpoint. The same values appear in
`emission_diagnostics()` and stay aligned with the diagnostic codes.

| Block       | `ComputedSpeciesUpload`        | `ComputedReactionUpload`              |
|-------------|--------------------------------|---------------------------------------|
| `Thermo`    | emits + `source_calculations`  | emits, `source_calculations` not emitted ⚠️ |
| `Statmech`  | emits + `source_calculations`  | emits + `source_calculations`         |
| `Transport` | accepted, not emitted ⚠️       | accepted, not emitted ⚠️              |
| `Kinetics`  | n/a                            | emits + `source_calculations`         |
| Artifacts   | emits inline (budgeted)        | emits inline (budgeted)               |

⚠️ entries surface a warning-level entry in `emission_diagnostics()`
with a stable code (see `tckdb_client.builders.DIAG_CODES`). When the
backend bundle schemas grow the missing fields, the assemblers flip
emission on and those diagnostics simply stop appearing — no client
API change.

### Artifact upload (inline by default)

Attach files (input decks, output logs, checkpoints) to any `Calculation`:

```python
opt = Calculation.opt(..., label="ethanol_opt")
opt.add_artifact("ethanol_opt.gjf", kind="input")
opt.add_artifact("ethanol_opt.log", kind="output_log")
```

`upload.to_payload()` (and so `client.upload(upload)`) reads those files and
sends them inline in the same request, in each calculation's `artifacts`
list. The server stores them in that request and runs its evidence checks
afterwards, so the deposit is not told "no artifact" about a calculation
whose log is in the same upload. The files must exist when `to_payload()`
runs.

Size rules (the server caps one artifact at 50 MiB and one upload at 200 MB
of decoded files):

- A file over 50 MiB is refused when the payload is built, naming the file.
- Inline files total at most 64 MiB decoded per upload (about 89 MB
  base64-encoded). `output_log` and `input` files are admitted first. Files
  past the budget are left out of the payload and returned by
  `upload.artifact_plan(result)`, to send in a second request as below.
- `ComputedSpeciesUpload(..., inline_artifacts=False)` (also on
  `ComputedReactionUpload`) defers every file to that second request.
- `artifact_plan(result, payload)` takes the dict you posted and skips the
  files in it (matched on calculation, kind, filename and content, so two
  different files with one name on a calculation are told apart). **Pass it
  whenever files may change after posting**: without `payload` the plan is
  recomputed from the builders and the files on disk, which can disagree with
  what was posted (a file that shrank, so a deferred checkpoint now fits and
  is never uploaded).

A file the server refuses now refuses the whole deposit, because the files
travel with the science: an `output_log` from a program the server does not
recognise, or an object-store outage, fails the request instead of leaving
the science stored and the file missing. The builders catch what they can
before any request and raise `TCKDBBuilderValidationError` naming the file: a
missing, empty or oversized file, a kind/extension the schema disallows, a
declared `sha256`/`bytes` that does not match. The server's list of
recognised output-log programs is not available client-side, so that case is
still the server's to refuse. `inline_artifacts=False` restores the old
split behaviour, where a bad file fails alone after the science is stored.

### Second-phase artifact upload

Files that did not go inline ride on a separate POST per calculation once
the server has assigned `calculation.id` values. After an inline upload,
`artifact_plan(result)` is empty and there is nothing to do.

```python
payload = upload.to_payload()                   # reads the attached files once
result  = client.upload(upload.upload_kind, payload)   # the scientific bundle
plan    = upload.artifact_plan(result, payload) # what did not go inline → ids

# Simple and easiest to debug: one POST per artifact.
client.upload_artifacts(plan, idempotency_key_prefix="my-run:2026-05-16")

# Or — fewer HTTP requests, one batch per calculation:
client.upload_artifacts(plan, idempotency_key_prefix="my-run:2026-05-16",
                        batch_by_calculation=True)
```

`batch_by_calculation=True` groups planned items by
`calculation_id` and sends one POST per group to
`/calculations/{id}/artifacts`. The server endpoint is **batch-atomic**:
any per-artifact validation failure rejects the whole batch with 422
(no DB rows, no S3 writes), and a pass-2 storage failure compensates
earlier S3 writes before returning 503. Returns one
`ArtifactUploadBatchResult(calculation_id, calculation_keys,
artifact_count, response, calculation_ref, status_code, request_id,
replayed, warnings)` per group: `response` is the server's body, `status_code`
and `request_id` (`X-Request-ID`) are the HTTP answer's, `replayed` is `True`
when the server replayed a stored `Idempotency-Key` receipt, and `warnings` is
the body's warning list. Only `batch_by_calculation=True` returns these; the
default sequential mode still returns the bare response bodies. Idempotency keys in batch mode
are `f"{prefix}:{first_calculation_key}:artifact-batch"` (one per
batch, deterministic across runs).

The default remains sequential one-artifact-per-request — pick the
batched form once you've confirmed the simple flow works.

Supported artifact kinds match the backend `ArtifactKind` enum:
`input`, `output_log`, `checkpoint`, `formatted_checkpoint`,
`ancillary`. The server's per-kind extension allow-list (e.g.
`.gjf`/`.in` for `input`, `.out`/`.log`/`.orca` for `output_log`)
still applies on upload — `add_artifact(...)` does not pre-check
extensions, but it does validate the `kind`, `sha256` (64 lowercase
hex), and `bytes` (non-negative integer) up front so typos surface
deterministically.

`emission_diagnostics()` reports
`artifact_upload_requires_second_phase` per calculation that has
artifacts which will not go inline (`inline_artifacts=False`, or over
the inline budget) — it is intentionally a warning-level reminder,
not an error, so the scientific upload can still go through.

#### Introspection helpers

Both upload classes expose four public iteration / preview helpers
so producer code never has to reach into private state to walk
their calculations or stage offline plans:

```python
# Walk every calc in the upload, in payload order.
for calc in upload.iter_calculations():
    ...

# Same walk, but skip calcs without attached artifacts.
for calc in upload.iter_calculations(with_artifacts_only=True):
    ...

# Walk with bucket / species context — TS-side vs species-side on
# the reaction path, identity-only on the species path.
for entry in upload.iter_calculation_entries(with_artifacts_only=True):
    print(entry.bucket, entry.species, entry.calculation)

# Pair every attached artifact with its calculation.
for calc, artifact in upload.iter_artifacts():
    ...

# A real `artifact_plan(server_result)` against synthetic IDs —
# useful for offline demos, CI fixtures, and producer debugging.
# Same upload state, same preview, every time. The IDs are NOT
# real server-side primary keys.
plan_preview = upload.artifact_plan_preview()
```

### Builder validation is not the same as wire emission

The builder layer accepts some fields for **forward compatibility**
before the backend bundle schema carries them. Today's known gaps:

- `ComputedSpeciesUpload.transport` — no bundle field yet
- `ComputedReactionUpload.species_transport` — no bundle field yet
- `ComputedReactionUpload.species_thermo[…].source_calculations` —
  the computed-reaction `BundleThermoIn` lacks `source_calculations`
  (only the computed-species `ThermoInBundle` carries it)

Each upload object exposes `emission_diagnostics()` so producers can
see what data has been validated locally but **will not** travel on
the wire today:

```python
upload = ComputedReactionUpload(
    reaction=rxn,
    calculations=[ts_opt, ts_freq],
    species_calculations={ch4: [ch4_opt, ch4_freq, ch4_sp]},
    species_transport={ch4: Transport(sigma_angstrom=3.8, epsilon_over_k_k=141.4)},
)
for diag in upload.emission_diagnostics():
    print(f"[{diag.level}] {diag.code} @ {diag.path}: {diag.message}")
```

Pass `warn_on_dropped_fields=True` to `client.upload(...)` to re-emit
each warning-level diagnostic through `warnings.warn(...)` before
dispatch — useful in producer pipelines that already filter / escalate
Python warnings:

```python
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("error")          # promote diagnostics to errors
    client.upload(upload, warn_on_dropped_fields=True)
```

The diagnostic codes are stable strings — see
[`docs/builder_api_stability.md`](builder_api_stability.md) for
the policy.

### Computed reaction

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TransitionState,
)

sr  = SoftwareRelease(software="Gaussian", version="16")
lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")

ts_geom = Geometry.from_xyz(ts_xyz)
ts_opt  = Calculation.opt(sr, lot, output_geometry=ts_geom,
                          final_energy_hartree=-270.55, converged=True)
ts_freq = Calculation.freq(sr, lot, n_imag=1, imag_freq_cm1=-1200.0,
                           zpe_hartree=0.201, depends_on=ts_opt)

kin = Kinetics.modified_arrhenius(
    A=1.2e13, A_units="cm3/mol/s", n=0.5,
    Ea=10.0, Ea_units="kJ/mol",        # also accepts kcal/mol
    Tmin=300, Tmax=2500,
    source_calculations={"ts_energy": ts_opt, "freq": ts_freq},
)

rxn = ChemReaction(
    reactants=[Species(smiles="[CH3]", charge=0, multiplicity=2),
               Species(smiles="[H]",   charge=0, multiplicity=2)],
    products =[Species(smiles="C",     charge=0, multiplicity=1)],
    family="H_Abstraction",
    transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    kinetics=[kin],
)

upload = ComputedReactionUpload(reaction=rxn, calculations=[ts_opt, ts_freq])

with TCKDBClient(base_url, api_key=api_key) as client:
    result = client.upload(upload)
```

#### Attaching transport to a computed species

```python
from tckdb_client.builders import Transport

transport = Transport(
    sigma_angstrom=3.8,
    epsilon_over_k_k=150.0,
    dipole_debye=0.1,
    polarizability_angstrom3=2.6,
    rotational_relaxation=13.0,
    source_calculations=[("supporting_geometry", opt)],
)

upload = ComputedSpeciesUpload(
    species=ethanol,
    calculations=[opt, freq, sp],
    primary_calculation=opt,
    thermo=thermo,
    statmech=statmech,
    transport=transport,           # accepted, validated, NOT yet emitted
)
```

`Transport` is also accepted on the computed-reaction path via
`ComputedReactionUpload(..., species_transport={ch4: transport, ...})`.

> **Forward-compat caveat.** The current bundle schemas
> (`ComputedSpeciesUploadRequest`, `ComputedReactionUploadRequest`)
> do **not** carry a transport field. The bundle assemblers accept
> the `Transport` builder, validate its source-calc references
> against the matching calculations bucket, and intentionally drop
> the data on the wire so producer code stays portable across the
> eventual schema change. Until then, the standalone
> `/uploads/transport` endpoint is the way to ship transport data.
> `Transport.to_payload()` produces a dict the primitive
> `TransportUploadPayload` schema accepts:
>
> ```python
> client.upload("transport", transport.to_payload())
> ```

#### Attaching statmech to a computed species

```python
from tckdb_client.builders import Statmech

statmech = Statmech(
    external_symmetry=2,
    point_group="C2v",
    is_linear=False,
    rigid_rotor_kind="asymmetric_top",     # see backend RigidRotorKind enum
    statmech_treatment="rrho",             # see backend StatmechTreatmentKind enum
    source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
)

upload = ComputedSpeciesUpload(
    species=water_species,
    calculations=[opt, freq, sp],
    primary_calculation=opt,
    thermo=thermo,
    statmech=statmech,
)
```

`Statmech` is also accepted on the computed-reaction path via
`ComputedReactionUpload(..., species_statmech={ch4: statmech, ...})`.
Unlike thermo on the reaction side, **statmech source_calculations
are emitted on the wire** for both endpoints — the backend's
`StatmechInBundle` and `BundleStatmechIn` both carry the field. The
builder validates that each referenced calc belongs to the same
species's bucket either way.

#### Attaching thermo to a computed species

```python
from tckdb_client.builders import (
    Calculation, ComputedSpeciesUpload, Geometry, LevelOfTheory,
    Species, SoftwareRelease, Thermo,
)

opt  = Calculation.opt(sr, lot, output_geometry=geom,
                       final_energy_hartree=-76.4, converged=True, label="opt")
freq = Calculation.freq(sr, lot, n_imag=0, zpe_hartree=0.0214,
                        depends_on=opt, label="freq")
sp   = Calculation.sp(sr, lot, electronic_energy_hartree=-76.45,
                      depends_on=opt, label="sp")

thermo = Thermo.nasa(
    coeffs_low =[a1, a2, a3, a4, a5, a6, a7],
    coeffs_high=[b1, b2, b3, b4, b5, b6, b7],
    t_low=300.0, t_mid=1000.0, t_high=3000.0,
    h298_kj_mol=-241.8, s298_j_mol_k=188.8,
    source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
)

upload = ComputedSpeciesUpload(
    species=water_species,
    calculations=[opt, freq, sp],
    primary_calculation=opt,
    thermo=thermo,
)
```

The `ComputedSpeciesUpload.thermo` path emits `source_calculations`
on the wire — each entry's `calculation_key` is resolved to the
bundle-local key the builder minted for the corresponding
`Calculation`. The backend's `ThermoInBundle` enforces uniqueness on
`(calculation_key, role)` pairs.

> **Per-endpoint distinction.** The same `Thermo` builder is used by
> `ComputedReactionUpload.species_thermo`, but the
> computed-reaction backend's `BundleThermoIn` schema does **not**
> carry `source_calculations` today. The builder still validates
> source-calc references locally for the reaction path; they are
> simply not emitted on the wire there. Only the computed-species
> path emits them.

#### Attaching reactant / product calculations

Pass `species_calculations` to ship per-species opt / freq / sp records
in the same upload. Each species gets one conformer anchored on its
single opt; non-opt calcs attach to that conformer.

```python
ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2)
h   = Species(smiles="[H]",   charge=0, multiplicity=2)
ch4 = Species(smiles="C",     charge=0, multiplicity=1)

# (build species-side opt + sp builders per species, similar to TS side …)

kin = Kinetics.modified_arrhenius(
    A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
    source_calculations={
        "reactant_energy": [ch3_sp, h_sp],   # duplicate role: list value
        "product_energy":  ch4_sp,
        "ts_energy":       ts_opt,
        "freq":            ts_freq,
    },
)
rxn = ChemReaction(
    reactants=[ch3, h], products=[ch4],
    family="H_Abstraction",
    transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    kinetics=[kin],
)
upload = ComputedReactionUpload(
    reaction=rxn,
    calculations=[ts_opt, ts_freq],
    species_calculations={
        ch3: [ch3_opt, ch3_sp],
        h:   [h_opt,   h_sp],
        ch4: [ch4_opt, ch4_sp],
    },
)
```

`source_calculations` accepts three shapes — a `dict[role, Calculation]`,
a `dict[role, list[Calculation]]` (used above for the duplicate
`reactant_energy`), or a `list[(role, Calculation)]` of explicit pairs.
The builder supports **one opt per species**, by design — see
[`docs/conformer_semantic_boundary.md`](conformer_semantic_boundary.md).

#### Attaching thermo to reactant / product species

Pass `species_thermo` to ship per-species thermo blocks. Three flavours
are supported and map to the backend's `BundleThermoIn` shape:

```python
from tckdb_client.builders import Thermo

# NASA 7-coefficient polynomial.
ch4_thermo = Thermo.nasa(
    coeffs_low =[a1, a2, a3, a4, a5, a6, a7],
    coeffs_high=[b1, b2, b3, b4, b5, b6, b7],
    t_low=200.0, t_mid=1000.0, t_high=5000.0,
    h298_kj_mol=-74.6, s298_j_mol_k=186.3,
)

# Scalar h298 / s298 (at least one required).
ch4_scalar = Thermo.scalar(h298_kj_mol=-74.6, s298_j_mol_k=186.3,
                           tmin_k=200, tmax_k=2000)

# Tabulated points.
ch4_points = Thermo.points(
    [{"temperature_k": 298.15, "cp_j_mol_k": 35.3, "h_kj_mol": 0.0,
      "s_j_mol_k": 186.3}],
    tmin_k=200, tmax_k=2000,
)

upload = ComputedReactionUpload(
    reaction=rxn,
    calculations=[ts_opt, ts_freq],
    species_calculations={ch4: [ch4_opt, ch4_freq, ch4_sp]},
    species_thermo={ch4: ch4_thermo},
)
```

`Thermo` factories also accept a `source_calculations=` kwarg for
forward compatibility with future thermo endpoints (the role names
mirror the backend's `ThermoCalculationRole` enum:
`opt` / `freq` / `sp` / `composite` / `imported`). The
computed-reaction endpoint does not consume this field today, so it
is **not emitted on the wire** — but the builder still validates that
each referenced calculation belongs to the same species's bucket so
producers don't ship inconsistent data.

`Kinetics.modified_arrhenius` accepts user-friendly unit strings
(`"cm3/mol/s"`, `"s^-1"`, `"kcal/mol"`, …) and normalises them to the
backend's enum values before sending. `Ea` in `kcal/mol` is converted
to `kJ/mol` automatically.

`client.upload(builder)` and the long-standing
`client.upload(endpoint, payload_dict)` stay structurally distinct —
passing a raw dict into the single-argument form raises `TypeError`
rather than guessing an endpoint. Thermo, statmech, transport and
artifact builders all ship; the table under "What emits today" says which
blocks reach the wire on which endpoint. The original design spec is
[`builder_api_mvp.md`](builder_api_mvp.md).
