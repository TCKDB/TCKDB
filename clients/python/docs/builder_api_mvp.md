# Builder API — MVP Spec

Status: **Phase 0 — design only.** No code in this spec is implemented yet.
Audience: TCKDB client maintainers and workflow-tool authors who want to
contribute scientific data without learning the raw upload payload shape.

---

## 1. Purpose

`tckdb-client` is a thin HTTP layer. It accepts a fully-formed JSON
payload and sends it. That is the right contract for sophisticated
producers like ARC, which already own a `TCKDBAdapter`. It is the wrong
contract for everyone else.

A new workflow-tool author or a scientific user who wants to push a
single species or reaction into TCKDB has to know:

- the upload endpoint matrix and how short names map to URLs
- the JSON shape of `ComputedSpeciesUploadRequest` / `ComputedReactionUploadRequest`
- the bundle-local-key convention used to wire calculations together
- how `depends_on` dependency edges encode opt → freq → sp restart chains
- which calculation result block (`opt_result` / `freq_result` / `sp_result`) goes
  with which `type`, and which fields each one requires
- how source-calculation roles attach to a kinetics or thermo record
- how artifacts attach to a calculation (two-phase upload)
- which fields are tolerated server-side as "unknown" vs which trigger 422

That cognitive load is the reason most adapter code today is producer-specific
glue. The **builder layer** lowers that load. It lets a user write
chemistry-shaped Python and have a payload generated from it:

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import Species, ChemReaction, Kinetics

client = TCKDBClient(base_url="https://tckdb.example.com", api_key="...")

ch4 = Species(smiles="C", charge=0, multiplicity=1)
ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2)

rxn = ChemReaction(reactants=[ch4], products=[ch3], family="H_Abstraction")
rxn.add_kinetics(
    Kinetics.modified_arrhenius(
        A=1.2e6, A_units="cm3_mol_s",
        n=1.5, Ea=42.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2000,
    )
)
client.upload(rxn)
```

The builder layer wraps, never replaces, the thin HTTP client.

---

## 2. Non-goals

The builder layer explicitly does **not**:

- change any backend schema, route, or validation rule
- alter ARC or any other downstream tool
- require a PyPI release or change the publishing story
- introduce OpenAPI code-generation or a TypeScript client
- give builder objects database IDs before upload
- query, update, or delete database records (no CRUD against the server)
- depend on RDKit, ASE, cclib, Arkane, or any chemistry library
- clone the backend's Pydantic schemas as a local typed mirror
- impose typed response models (responses stay parsed JSON dicts, matching
  current client behavior)
- replace the existing raw-dict upload methods — they remain valid

The server remains the **authoritative** source of validation, deduplication,
ID assignment, and persistence. The builder is a payload-construction aid,
not a parallel data model.

---

## 3. Layering model

```text
┌──────────────────────────────────────────────────────────────┐
│ workflow-tool adapters                                       │
│   ARC / RMG / KinBot / user scripts                          │
│   may use builders OR continue emitting raw payloads         │
├──────────────────────────────────────────────────────────────┤
│ tckdb_client.builders   ← new                                │
│   scientific Python objects                                  │
│   local validation                                           │
│   payload construction                                       │
│   no HTTP, no I/O                                            │
├──────────────────────────────────────────────────────────────┤
│ tckdb_client.client                                          │
│   HTTP, auth, version headers, idempotency, replay, errors   │
└──────────────────────────────────────────────────────────────┘
```

**Hard rule.** `tckdb_client.builders.*` may not import:

- `app.*` or `backend.*` (any backend module)
- SQLAlchemy, Alembic, FastAPI
- backend Pydantic schemas
- database models, migrations, or RDKit

It may import from `tckdb_client.errors` and from the standard library, plus
a permissive light typing dependency already used by the client (`httpx`
types only if needed for re-export — typically not).

The builder layer is forward-compatible with deployment scenarios where
the client is shipped as a slim wheel without the monorepo present.

---

## 4. Public import style

The top-level package continues to export only the thin client and
errors:

```python
from tckdb_client import (
    TCKDBClient,
    TCKDBError,
    TCKDBValidationError,
    TCKDBAuthenticationError,
    # …existing exports…
    make_idempotency_key,
    replay_bundle,
)
```

Builder classes live under a single subpackage:

```python
from tckdb_client.builders import (
    Species,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Calculation,
    ChemReaction,
    TransitionState,
    Kinetics,
    Thermo,
    ComputedSpeciesUpload,
    ComputedReactionUpload,
)
```

Rationale for keeping builders off the top-level namespace:

- The thin client stays the documented entry point for advanced producers.
- A user who imports `tckdb_client` for the HTTP API never pulls in
  builder code or its validators.
- Tooling (`pip-tools`, `pyright`) sees a smaller surface at the package
  root; builder breakage cannot crash imports for HTTP-only consumers.

---

## 5. Builder package layout

```text
clients/python/src/tckdb_client/
    __init__.py
    client.py
    replay.py
    errors.py
    idempotency.py
    cli.py
    types.py
    builders/
        __init__.py          # re-exports the public builder classes
        base.py              # BuilderObject / Upload protocol, key minting
        species.py           # Species
        geometry.py          # Geometry, Geometry.from_xyz
        provenance.py        # LevelOfTheory, SoftwareRelease
        calculation.py       # Calculation, Calculation.opt/freq/sp
        reaction.py          # ChemReaction, TransitionState
        kinetics.py          # Kinetics + factories
        thermo.py            # Thermo (Phase 3)
        uploads.py           # ComputedSpeciesUpload, ComputedReactionUpload
        validation.py        # shared local-validation helpers + error type
```

File names may be adjusted in the actual implementation, but the
**single subpackage** layout is the recommendation: it gives the builder
layer one obvious folder to govern, separate from the HTTP transport.

---

## 6. Builder objects are not ORM models

The spec stance is intentionally narrow:

> Builder objects are **temporary local upload-construction objects.**
> They are not database records. They do not have database IDs before
> upload. They do not query, update, or delete server-side state. The
> server remains the authoritative source of IDs, deduplication,
> validation, and persistence.

Implications:

- No `.refresh()`, `.save()`, `.delete()` methods on builder objects.
- No background sync — a builder object knows only what its constructor
  was given.
- After `client.upload(...)`, the server-returned `*_id` / `*_ref` values
  are surfaced on the returned **result** object, not back onto the
  source builder. Mutating the builder post-upload is out of scope.
- A builder object can be reused across uploads as a value, but each
  upload is independent and produces an independent payload.

---

## 7. Raw payload compatibility

Existing raw-dict methods stay supported, unchanged:

```python
# raw dict — explicit, unambiguous, no builder layer involved
client.upload("computed_reaction", payload_dict)
client.upload("/uploads/some-future-endpoint", payload_dict)
client.bundle_submit(bundle_dict, idempotency_key=key)
```

The builder layer adds **one new method** with a different signature:

```python
# builder object — implements an Upload protocol
client.upload(upload_object)
```

The two signatures are kept distinct on purpose:

- `client.upload(endpoint_short_name_or_path, payload_dict)` — current
  behavior, takes a string + dict, hits a fixed endpoint by name.
- `client.upload(upload_object)` — new behavior, takes a single object
  that knows its own endpoint via `upload_kind` and serializes itself
  via `to_payload()`.

`client.upload(...)` must dispatch by argument type, not by guessing.
Specifically, accepting a raw dict in the single-argument form is
**rejected** — silent dispatch on dict shape is the kind of behavior
that loses producers an afternoon when they hand it a half-built
bundle. Builder objects must implement `Upload`; dicts must use the
string-keyed form.

```python
class Upload(Protocol):
    upload_kind: str            # short name in UPLOAD_ENDPOINTS
    def to_payload(self) -> dict: ...
```

---

## 8. MVP builder objects

For each object the spec lists: **purpose**, **minimum required fields**,
**optional fields**, **local validation**, **payload contribution**.
Field lists are illustrative; the final implementation may tighten or
expand them in coordination with the server schema.

### 8.1 `Species`

- **Purpose** — identifies a chemical species. Becomes a
  `SpeciesEntryIdentityPayload` in the bundle.
- **Required** — one of `smiles` / `inchi`, plus `charge`, `multiplicity`.
- **Optional** — `label`, `formula`, `notes`, `electronic_state_kind`.
- **Local validation** — at least one identifier; `multiplicity >= 1`;
  `charge` is an int; `label`, if provided, becomes the basis for the
  local key (see §11).
- **Payload contribution** — emits identity fragment; in computed-species
  uploads, also seeds the bundle-local `species` block.

### 8.2 `Geometry`

- **Purpose** — Cartesian coordinates for a calculation input/output or a TS.
- **Required** — `symbols` (list[str]), `coords` (list[(x, y, z)]).
- **Optional** — `units` (default `"angstrom"`), `comment`.
- **Constructors** — `Geometry.from_xyz(xyz_str)`, `Geometry(symbols=…, coords=…)`.
- **Local validation** — `len(symbols) == len(coords)`; each coord is a 3-tuple
  of floats; element symbols are non-empty strings (no periodic-table check —
  the server owns that).
- **Payload contribution** — `GeometryPayload` fragment inside calculation
  input/output lists. Multiple calculations may share the same `Geometry`
  instance; the builder emits one local geometry per unique instance.

### 8.3 `LevelOfTheory`

- **Purpose** — identifies a quantum-chemistry method/basis combination.
- **Required** — `method`. `basis` is required for methods that take one.
- **Optional** — `auxiliary_basis`, `dispersion`, `solvent`, `solvation_method`.
- **Local validation** — `method` non-empty; if the SDK has a fixed vocabulary
  for a field (e.g. `solvation_method`) and the value is outside it, raise.
- **Payload contribution** — emits a `LevelOfTheoryRef` fragment.

### 8.4 `SoftwareRelease`

- **Purpose** — identifies which ESS produced a calculation.
- **Required** — `software`, `version`.
- **Optional** — `revision`, `platform`.
- **Local validation** — `software` and `version` non-empty strings.
- **Payload contribution** — emits a `SoftwareReleaseRef` fragment.

### 8.5 `Calculation`

- **Purpose** — one quantum-chemistry calculation. Constructors are
  result-type-specific so the user doesn't have to know which result
  block matches which `type`.
- **Constructors (MVP)** —
  - `Calculation.opt(software_release, level_of_theory, *, input_geometry=None, output_geometry, final_energy_hartree, converged=True, depends_on=None, label=None)`
  - `Calculation.freq(software_release, level_of_theory, *, input_geometry, frequencies_cm1, zpe_hartree=None, depends_on=None, label=None)`
  - `Calculation.sp(software_release, level_of_theory, *, input_geometry, electronic_energy_hartree, depends_on=None, label=None)`
- **Deferred** — `Calculation.irc(...)`, `Calculation.scan(...)`, `Calculation.neb(...)`, `Calculation.path_search(...)` (Phase 3+).
- **Local validation** —
  - opt requires `output_geometry` and `final_energy_hartree`;
  - freq requires `input_geometry` and a non-empty `frequencies_cm1` list;
  - sp requires `input_geometry` and `electronic_energy_hartree`;
  - `depends_on` must refer to a `Calculation` builder (not a payload dict).
- **Payload contribution** — emits a `CalculationInBundle` entry with the
  correct `*_result` block. `depends_on` is rewritten to
  `CalculationDependencyInBundle` using local keys.
- **API shape note (artifacts)** — see §13. Artifacts attach via
  `calc.add_artifact(path=..., kind=...)` and are emitted in the
  calculation's `artifacts: [ArtifactIn]` list.

### 8.6 `TransitionState`

- **Purpose** — identifies a TS structure. Counterpart to `Species` on
  the reaction side.
- **Required** — `charge`, `multiplicity`, `geometry`.
- **Optional** — `label`, `imaginary_frequency_cm1` (sanity hint —
  server is still authoritative).
- **Local validation** — `multiplicity >= 1`; geometry is a `Geometry`
  builder, not a dict.
- **Payload contribution** — `BundleTransitionStateIn` fragment.

### 8.7 `ChemReaction`

- **Purpose** — top-level reaction object grouping reactants, products,
  family, optional TS, and zero-or-more kinetics records.
- **Required** — `reactants: list[Species]`, `products: list[Species]`.
- **Optional** — `family`, `transition_state: TransitionState | None`,
  `direction`, `label`.
- **Local validation** —
  - reactants and products are non-empty;
  - reactants/products are `Species` builders, not dicts;
  - if `transition_state` is given, it must be a `TransitionState`
    builder;
  - duplicate participants (same `Species` instance on both sides) raise
    unless an `allow_duplicate_participants=True` opt-in is set.
- **Mutator** — `reaction.add_kinetics(kinetics_builder)` appends; the
  same effect can be achieved via the `kinetics=[…]` constructor kwarg.
- **Payload contribution** — produces the reaction identity block in
  `ComputedReactionUploadRequest`, with reactant/product participants
  resolved through species local keys.

### 8.8 `Kinetics`

- **Purpose** — kinetics record attached to a reaction.
- **Factories (MVP)** —
  - `Kinetics.modified_arrhenius(A, A_units, n, Ea, Ea_units, Tmin, Tmax, *, source_calculations=None, pressure=None, label=None)`
- **Optional fields** — `source_calculations: dict[str, Calculation]` mapping
  role names (e.g. `"ts_energy"`, `"freq"`) to `Calculation` builders.
- **Local validation** — `A > 0`, `Tmin > 0`, `Tmin < Tmax`, `A_units` and
  `Ea_units` from the SDK's fixed unit vocabulary (see §10), every
  `Calculation` referenced via `source_calculations` must also appear in
  the parent `ComputedReactionUpload.calculations`.
- **Payload contribution** — `BundleKineticsIn` row with
  `source_calculations` rewritten to `KineticsSourceCalculationIn` rows
  using local keys.

### 8.9 `Thermo` (Phase 3B — shipped)

- **Purpose** — one thermo block attached to a species. Targets the
  backend's `BundleThermoIn` (computed-reaction) today; a future
  computed-species path will switch on the `source_calculations`
  field that `ThermoInBundle` exposes.
- **Factories** — `Thermo.scalar(...)`, `Thermo.nasa(...)`, `Thermo.points(...)`.
  - `scalar(h298_kj_mol?, s298_j_mol_k?, tmin_k?, tmax_k?)` — at least
    one of `h298_kj_mol` / `s298_j_mol_k` required.
  - `nasa(coeffs_low, coeffs_high, t_low, t_mid, t_high, …)` —
    `coeffs_low` / `coeffs_high` are length-7 float lists; the builder
    translates to flat `a1..a7` / `b1..b7` wire fields. `t_low <
    t_mid < t_high` enforced locally.
  - `points([{"temperature_k": …, "cp_j_mol_k": …, …}, …])` —
    non-empty list of point dicts; each temperature must be > 0.
- **`source_calculations`** — accepted as a kwarg on every factory in
  three shapes (`dict[role, Calc]`, `dict[role, list[Calc]]`,
  `list[(role, Calc)]`) for forward compatibility. Role tokens follow
  the backend `ThermoCalculationRole` enum: `opt`, `freq`, `sp`,
  `composite`, `imported`. **Not emitted** on the computed-reaction
  wire today; the assembler still validates source calcs against the
  same species's bucket.
- **Local validation** — at least one representation supplied;
  temperature bounds positive and ordered; NASA coefficient lists
  length-7; non-empty points; numeric scalars; role tokens in the
  thermo enum; identity-only source-calc references.
- **Payload contribution** — `BundleThermoIn` block under
  `species[i]["thermo"]` of the computed-reaction payload.

### 8.10 `ComputedSpeciesUpload`

- **Purpose** — top-level upload object for `POST /uploads/computed-species`.
- **Required** — `species: Species`, `calculations: list[Calculation]`,
  `primary_calculation: Calculation`.
- **Optional** — `thermo: list[Thermo]`, `statmech: list[Statmech]` (Phase 3),
  `notes`.
- **Local validation** — `primary_calculation in calculations`; every
  calculation `depends_on` target is itself in `calculations`; species
  identity present.
- **`upload_kind`** — `"computed_species"`.
- **`to_payload()`** — emits `ComputedSpeciesUploadRequest` JSON.

### 8.11 `ComputedReactionUpload`

- **Purpose** — top-level upload object for `POST /uploads/computed-reaction`.
- **Required** — `reaction: ChemReaction`.
- **Optional** — `calculations: list[Calculation]` (TS-side bucket),
  `species_calculations: dict[Species, list[Calculation]]` (Phase 3A —
  reactant/product bucket), `species_thermo: dict[Species, Thermo]`
  (Phase 3B — per-species thermo), `primary_ts_calculation`, `note`.
- **Local validation** — every calculation referenced via the reaction's
  TS / kinetics / source-calculation chain resolves to *some* bucket in
  the upload (TS or species); TS-side `depends_on` stays inside the TS
  bucket; species-side `depends_on` stays inside the same species's
  bucket; `primary_ts_calculation` must be in the TS bucket;
  `species_calculations` keys must be `Species` objects appearing in
  the reaction; one opt per species (multi-conformer deferred);
  each species's non-opt calcs need at least one opt to anchor the
  conformer geometry.
- **`upload_kind`** — `"computed_reaction"`.
- **`to_payload()`** — emits `ComputedReactionUploadRequest` JSON.
  Species with attached calculations produce one conformer (anchored
  by the opt's `output_geometry`, falling back to `input_geometry`)
  plus a non-opt `calculations` list referencing the conformer geometry
  via `geometry_key`. Species without attached calculations remain
  identity-only (empty `conformers` and `calculations` lists).

#### Kinetics `source_calculations` — three accepted shapes

`Kinetics.modified_arrhenius(source_calculations=...)` accepts:

- `dict[str, Calculation]` — one calc per role.
- `dict[str, list[Calculation]]` — multiple calcs per role
  (e.g. duplicate `reactant_energy` for a bimolecular reaction).
- `list[tuple[str, Calculation]]` — explicit ordering and duplicate
  roles.

The assembler resolves each `Calculation` to its bundle-local key at
payload time. References may target either the TS bucket or any
species bucket.

---

## 9. Validation responsibility

Two-level model, with a clear cut between them.

### 9.1 What the builder layer validates locally

Things that are obvious failures *before* the request goes out. The goal
is to keep the round-trip cost down for typos and missing fields, not to
re-implement the backend.

- missing required fields on any builder object
- invalid `charge` / `multiplicity` shape (non-int, multiplicity < 1)
- empty `reactants` / `products`
- negative or zero temperature bounds
- `Tmin >= Tmax`
- unit string outside the SDK's fixed unit vocabulary
- calculation `depends_on` points to a `Calculation` that is not in the
  parent upload's `calculations` list
- source-calculation role points to a `Calculation` not in the upload
- `Calculation.freq(...)` with an empty `frequencies_cm1`
- `Calculation.sp(...)` with no `electronic_energy_hartree`
- `ChemReaction` with the same `Species` instance on both sides, unless
  explicitly allowed
- `to_payload()` called on an upload that is not internally consistent

### 9.2 What the builder layer does **not** validate

Anything where the server is authoritative. The client must not develop a
parallel chemistry brain.

- canonical chemical identity (SMILES → canonical form / InChIKey)
- RDKit isomorphism between reactants and products
- database deduplication of species, geometry, calculation, or LoT
- permission checks (the server gates curator/admin actions)
- moderation status / review workflows
- full backend Pydantic schema completeness — the server will reject 422
  with a structured error and the builder must surface that cleanly
- energy or frequency sanity (the server applies wavefunction
  diagnostics; the builder does not second-guess them)

### 9.3 Builder-layer error type

Local validation raises a single dedicated exception so callers can
distinguish "your inputs are inconsistent" from "the server rejected the
upload":

```python
class TCKDBBuilderValidationError(ValueError):
    """Local builder validation failure (before any HTTP request)."""
```

`client.upload(builder_object)` therefore has three failure modes:

- `TCKDBBuilderValidationError` — never left the process, fix the code
- `TCKDBValidationError` — server returned 422
- `TCKDBHTTPError` / `TCKDBConnectionError` — transport-level

---

## 10. Payload-generation responsibility

The builder layer has one job at payload time: take a tree of Python
objects, walk it once, and produce the dict that matches the backend's
upload schema.

A few principles:

- **Idempotent.** `upload.to_payload() == upload.to_payload()` for the
  same object — local-key minting must be deterministic (§11).
- **Pure.** `to_payload()` does no I/O. The HTTP call is a separate
  step, in `client.upload(...)`.
- **One walk.** Each builder object emits its fragment once; references
  to it become local keys. No copying of geometries or LoT blocks across
  the bundle.
- **Stable.** Adding optional fields to a builder must not reorder
  existing payload keys in a way that breaks idempotency replay against
  the server.

### 10.1 SDK unit vocabulary

The builder layer ships a small fixed set of unit tokens for fields where
the backend takes a typed enum. The vocabulary lives in
`tckdb_client.builders.validation` and is exposed for tests:

```python
A_UNITS = {"cm3_mol_s", "m3_mol_s", "s-1", …}
ENERGY_UNITS = {"kJ/mol", "kcal/mol", "hartree", …}
TEMPERATURE_UNITS = {"K"}
```

Unknown tokens raise `TCKDBBuilderValidationError` locally. The server is
still the source of truth on which enum value those tokens map to; the
builder simply prevents typos like `"cm3 mol s"` (space-separated).

---

## 11. Payload local keys

The bundle upload schemas wire calculations, conformers, species, and TS
together using **bundle-local string keys** (`key: str`). The builder layer
manages those keys for the user.

### 11.1 Principle

> Users may optionally provide labels.
> The builder uses labels to create readable local keys.
> If labels are absent, the builder generates deterministic keys based
> on insertion order.
> Local keys are internal to the payload and not database IDs.

### 11.2 Mechanics

- Every builder object that needs a local key (`Species`, `Calculation`,
  `TransitionState`, `Kinetics`, `Thermo`) carries an optional `label: str`.
- At `to_payload()` time, a key minter walks the upload tree once and
  assigns:
  - `slugify(label)` when label is present and unique inside the upload,
    falling back to `slugify(label)_2`, `_3`, … on collision;
  - `species_1`, `calc_1`, `ts_1`, `kinetics_1`, … in insertion order
    when label is absent.
- Keys are stable across repeated `to_payload()` calls on the same object
  graph — the minter walks the same tree the same way.

### 11.3 Why deterministic

Idempotency-key flows (see `make_idempotency_key`, `replay_bundle`)
depend on the *payload bytes* being stable for a given logical input.
A non-deterministic minter (random UUIDs, `id()`-based) would make every
retry look like a new request to the server.

---

## 12. Upload behavior

`client.upload(upload_object)` is the single new method on the thin
client. It dispatches like this:

```python
def upload(self, upload_object, *, idempotency_key: str | None = None) -> Any:
    if not hasattr(upload_object, "upload_kind") or not hasattr(upload_object, "to_payload"):
        raise TypeError(
            "client.upload(<builder>) takes a builder object. "
            "For raw dicts, use client.upload(endpoint, payload_dict)."
        )
    payload = upload_object.to_payload()
    return self.post_json(
        UPLOAD_ENDPOINTS[upload_object.upload_kind],
        payload,
        idempotency_key=idempotency_key,
    )
```

Notes:

- The thin client's existing dual-arg `upload(endpoint, payload)` form
  stays. Resolution is by argument count and type. Passing a builder to
  the dual-arg form, or a dict to the single-arg form, raises a clear
  `TypeError` instead of guessing.
- Idempotency keys are still **explicit** — the builder layer does not
  invent them. The user passes a key as a kwarg exactly as today.
- The return value is the same parsed JSON the server emits today (no
  typed response models).

### 12.1 Where server-assigned IDs surface

After a successful upload, the parsed JSON response carries the
server-side IDs and refs for everything just persisted. They are **not**
written back into the builder objects (§6). A caller that needs the IDs
reads them off the response:

```python
result = client.upload(species_upload)
print(result["species_entry_id"], result["primary_calculation"]["id"])
```

---

## 13. Artifact handling

For MVP, artifacts are designed in but not fully implemented in Phase 1.
The intended API:

```python
opt.add_artifact(path="input.gjf", kind="input")
opt.add_artifact(path="output.log", kind="output_log")
opt.add_artifact(path="opt.chk", kind="checkpoint")
```

Each call adds an `ArtifactIn` entry to the calculation's `artifacts`
list. `add_artifact` validates locally:

- the file exists and is readable
- the `kind` is in the SDK's artifact-kind vocabulary

Upload mechanics — two-phase, mirroring the existing backend contract:

1. `client.upload(computed_species_upload)` posts the scientific payload
   and returns server-assigned `calculation_id` values.
2. The builder layer offers `client.upload_artifacts(result)` (Phase 3)
   that walks the result, finds each calculation's local artifacts list,
   and POSTs them to
   `/api/v1/calculations/{calculation_id}/artifacts`.

Sidecar / replay-based artifact dispatch (using the existing
`tckdb_client.replay` machinery) is an option to evaluate during Phase 3.
If `replay_bundle` can already carry artifact bytes on disk between
runs, artifact integration may fall out of that path without a dedicated
helper.

Artifact integration is **Phase 3** unless the existing client replay
support makes it trivial.

---

## 14. Computed species upload flow

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Species,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Calculation,
    ComputedSpeciesUpload,
)

client = TCKDBClient(base_url="https://tckdb.example.com", api_key="...")

lot = LevelOfTheory(method="wb97xd", basis="def2-tzvp")
gaussian = SoftwareRelease(software="Gaussian", version="16", revision="C.01")

ethanol = Species(smiles="CCO", charge=0, multiplicity=1)

opt = Calculation.opt(
    software_release=gaussian,
    level_of_theory=lot,
    input_geometry=Geometry.from_xyz(input_xyz),
    output_geometry=Geometry.from_xyz(output_xyz),
    final_energy_hartree=-154.123,
    converged=True,
)

freq = Calculation.freq(
    software_release=gaussian,
    level_of_theory=lot,
    input_geometry=Geometry.from_xyz(output_xyz),
    frequencies_cm1=[100.0, 200.0, 300.0],
    zpe_hartree=0.123,
    depends_on=opt,
)

upload = ComputedSpeciesUpload(
    species=ethanol,
    calculations=[opt, freq],
    primary_calculation=opt,
)

result = client.upload(upload)
```

What the builder does internally, top to bottom:

1. Walks the upload tree, assigns local keys (`species_1`, `calc_1`, `calc_2`).
2. Builds one `SpeciesEntryIdentityPayload` from `ethanol`.
3. Builds two `CalculationInBundle` entries:
   - `calc_1` with `type=opt`, `opt_result={...}`, primary.
   - `calc_2` with `type=freq`, `freq_result={...}`, `depends_on=[{parent_calculation_key: "calc_1", role: "freq_of"}]`.
4. Emits the `ComputedSpeciesUploadRequest` JSON payload.
5. Hands it to `client.post_json("/uploads/computed-species", payload, ...)`.

The shape is exactly what
`backend/app/schemas/workflows/computed_species_upload.py::ComputedSpeciesUploadRequest`
expects. The builder layer is doing no more than what an ARC-style adapter
would do, but framed in plain chemistry objects.

---

## 15. Computed reaction upload flow

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Species,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Calculation,
    TransitionState,
    ChemReaction,
    Kinetics,
    ComputedReactionUpload,
)

client = TCKDBClient(base_url="https://tckdb.example.com", api_key="...")

lot = LevelOfTheory(method="wb97xd", basis="def2-tzvp")
gaussian = SoftwareRelease(software="Gaussian", version="16", revision="C.01")

ch4 = Species(smiles="C", charge=0, multiplicity=1)
phenyl = Species(smiles="[c]1ccccc1", charge=0, multiplicity=2)
ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2)
benzene = Species(smiles="c1ccccc1", charge=0, multiplicity=1)

ts = TransitionState(
    charge=0,
    multiplicity=1,
    geometry=Geometry.from_xyz(ts_xyz),
)

ts_opt = Calculation.opt(
    software_release=gaussian,
    level_of_theory=lot,
    output_geometry=Geometry.from_xyz(ts_xyz),
    final_energy_hartree=-270.55,
    converged=True,
)

ts_freq = Calculation.freq(
    software_release=gaussian,
    level_of_theory=lot,
    input_geometry=Geometry.from_xyz(ts_xyz),
    frequencies_cm1=[-1200.0, 50.0, 80.0],
    zpe_hartree=0.201,
    depends_on=ts_opt,
)

kinetics = Kinetics.modified_arrhenius(
    A=2.1e7,
    A_units="cm3_mol_s",
    n=1.2,
    Ea=35.0,
    Ea_units="kJ/mol",
    Tmin=300,
    Tmax=2000,
    source_calculations={"ts_energy": ts_opt, "freq": ts_freq},
)

rxn = ChemReaction(
    reactants=[ch4, phenyl],
    products=[ch3, benzene],
    family="H_Abstraction",
    transition_state=ts,
    kinetics=[kinetics],
)

upload = ComputedReactionUpload(
    reaction=rxn,
    calculations=[ts_opt, ts_freq],
)

result = client.upload(upload)
```

What the builder does internally:

1. Walks reactants/products/TS, mints species/TS local keys.
2. Walks `calculations`, mints calc local keys; the kinetics
   `source_calculations` dict is rewritten to use those keys.
3. Emits a `ComputedReactionUploadRequest` matching the backend schema.

This payload is **generic** — it is not ARC-specific. ARC's existing
`TCKDBAdapter` can continue to emit raw dicts; the builder layer is
provided for new producers and ad-hoc scientific users who want the
shorter on-ramp.

---

## 16. Implementation phases

Recommend a four-phase rollout. Every phase is independently shippable;
no consumer code breaks at any phase boundary because the thin client
contract is unchanged throughout.

### Phase 0 — design only (this document)

- spec lives at `clients/python/docs/builder_api_mvp.md`
- linked from `clients/python/README.md`
- no code changes beyond docs

### Phase 1 — core species pipeline

- `tckdb_client/builders/` package skeleton
- `BuilderObject` / `Upload` protocol in `builders/base.py`
- `Species`, `Geometry`, `LevelOfTheory`, `SoftwareRelease`
- `Calculation.opt`, `Calculation.freq`, `Calculation.sp`
- `ComputedSpeciesUpload.to_payload()`
- `client.upload(upload_object)` dispatcher + dual-arg disambiguation
- local-validation error type
- local-key minter
- unit vocabulary in `builders/validation.py`
- tests (see §17)

### Phase 2 — reactions

- `ChemReaction`, `TransitionState`
- `Kinetics.modified_arrhenius` (+ source-calculation wiring)
- `ComputedReactionUpload.to_payload()`
- snapshot tests against current backend payload shape

### Phase 3 — depth

- `Thermo` builder (NASA9, HFn(T))
- artifact helpers, two-phase upload glue (or replay sidecar)
- statmech, transport builders if demand exists
- richer validation (e.g. negative-frequency count in TS freq)

### Phase 4 — adapter ergonomics

- worked examples for common external producers
- migration cookbook for ARC users who want to use builders in place of
  bespoke payload code
- optional convenience converters for cclib/Arkane outputs **without
  importing those libraries** (the conversion lives in the producer
  adapter; the builder stays chemistry-library-free)

---

## 17. Test plan

The test suite must enforce both correctness and the layering invariant.
All tests run under the existing `clients/python/tests/` directory.

| Test | What it asserts |
|------|-----------------|
| `test_builders_required_fields` | every builder rejects missing required fields with `TCKDBBuilderValidationError`. |
| `test_builders_unit_vocabulary` | `Kinetics.modified_arrhenius` rejects unknown unit tokens locally. |
| `test_geometry_from_xyz` | round-trips a small XYZ string into `Geometry.symbols` / `Geometry.coords`. |
| `test_local_keys_deterministic` | calling `to_payload()` twice on the same object graph produces identical bytes. |
| `test_local_keys_use_labels` | objects with `label="ts1"` produce a key derived from `"ts1"`; collisions get `_2` suffix. |
| `test_dependency_refs_resolve` | a `Calculation` whose `depends_on` is in the bundle becomes a `parent_calculation_key` edge; a `depends_on` outside the bundle raises locally. |
| `test_source_calculation_refs_resolve` | a `Kinetics` whose `source_calculations` map points outside the upload raises locally. |
| `test_computed_species_payload_snapshot` | `ComputedSpeciesUpload.to_payload()` for the §14 example matches a stored JSON snapshot. |
| `test_computed_reaction_payload_snapshot` | same for the §15 example. |
| `test_client_upload_dispatches_to_endpoint` | `client.upload(builder)` POSTs to the URL in `UPLOAD_ENDPOINTS[builder.upload_kind]` (asserted via `httpx.MockTransport`). |
| `test_client_upload_rejects_raw_dict` | `client.upload({"foo": 1})` raises `TypeError`, not a hidden POST. |
| `test_raw_upload_methods_unchanged` | existing `client.upload("computed_reaction", payload_dict)` still behaves byte-for-byte the same; the test fixtures pin the request URL, headers, and body. |
| `test_builders_import_without_backend` | a sandboxed import of `tckdb_client.builders` succeeds in an environment with `app/`, SQLAlchemy, FastAPI, Pydantic-Settings absent from `sys.modules`. |

The snapshot tests are deliberate. They are the contract surface between
the builder layer and the backend upload schemas — if a snapshot drifts,
the developer has to look at the diff and either accept it (server
schema changed, snapshot regenerated) or fix it (builder regressed).

---

## 18. Open design questions

Documented now so they don't get hand-waved during implementation.

1. **Identifier preference.** When a `Species` carries both `smiles` and
   `inchi`, which wins in the emitted identity fragment? Proposal: emit
   both; the server already deduplicates by canonical identity.

2. **Builder mutability.** Should `Species`, `Geometry`, etc. be frozen
   `dataclass`es or mutable objects? Mutability is nicer for incremental
   construction (`rxn.add_kinetics(...)`); immutability is nicer for
   reuse across uploads. Proposal: top-level *upload* objects are
   mutable up to `to_payload()`; building-block objects (`Species`,
   `Geometry`, `LevelOfTheory`, `SoftwareRelease`) are frozen
   dataclasses to make accidental sharing safe.

3. **Label-to-key collisions.** When two builders share a label, what's
   the deterministic disambiguation? Proposal: suffix `_2`, `_3`, … in
   insertion order; documented in §11. (Equivalent labels on equivalent
   logical entities should be deduplicated by *value*, not collide —
   but the builder layer does not own value-level dedup; that is
   the server's job.)

4. **`Calculation.opt` with no input geometry.** ARC-style restarts of an
   opt sometimes have no separate input geometry (the previous opt's
   final geometry is implied via `depends_on`). The backend bundle
   schema allows zero input geometries for `opt`. Proposal: `input_geometry`
   stays optional on `Calculation.opt`; we don't second-guess.

5. **Reaction-direction handling.** Should `ChemReaction` accept a
   `direction` kwarg, or always be left as "either" and let the server
   decide? Proposal: accept `direction in {"forward", "reverse",
   "either"}` as a hint; default `"either"`.

6. **Atomic-symbol normalization.** `Geometry.from_xyz("h 0 0 0\n")` —
   does the builder normalize `"h"` to `"H"`? Proposal: yes, simple
   capitalization is in-scope; anything beyond that is the server's
   problem.

7. **Free-text fields.** `notes`, `comment`, `label` — do we strip /
   length-cap them locally? Proposal: no strip, but emit a
   `TCKDBBuilderValidationError` above a generous local cap (e.g.
   16 kB) so a runaway log message doesn't end up posted.

8. **Builder-emitted idempotency keys.** Should the builder layer
   *help* compute idempotency keys (e.g. by hashing `to_payload()`
   output)? Tempting but tricky — local-key minting determinism is
   already a precondition, and the hash would change every time we
   add an optional field. Proposal: out of scope for MVP; users keep
   passing `idempotency_key=` explicitly.

9. **Versioning the builder layer.** Builder behavior changes (new
   factory, new validation rule) should bump the `tckdb-client`
   version (per existing memory). Open question: do we also stamp a
   `builder_schema_version` into payloads for forward-compat? Proposal:
   no — the server already enforces a minimum client version (§ shipped
   compat header), and overlapping schemes lead to drift.

10. **Conformer awareness.** The backend bundle schemas natively model
    conformer groups, observations, and assignment schemes. The MVP
    spec hides those behind the simpler `ComputedSpeciesUpload(species,
    calculations, primary_calculation)` shape — the builder emits a
    single-conformer bundle on the user's behalf. Multi-conformer
    uploads are a Phase 3 extension that introduces a `Conformer`
    builder; until then, users with multi-conformer datasets fall back
    to raw payloads.
