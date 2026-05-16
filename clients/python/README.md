# tckdb-client

Generic synchronous Python HTTP client for the [TCKDB](https://github.com/tckdb) API.

`tckdb-client` is the **transport layer** for any TCKDB consumer — scripts,
notebooks, post-processing jobs, future producer-specific adapters (ARC,
RMG, …). It accepts already-formed JSON payloads and sends them; it does
**not** know how to construct chemistry payloads, and it has **no**
chemistry dependencies.

> For hosted/public querying, see
> [`docs/guides/public_hosted_querying.md`](../../docs/guides/public_hosted_querying.md).

## Install

Install directly from the TCKDB monorepo without cloning:

```bash
pip install "git+https://github.com/calvinp0/tckdbv2.git#subdirectory=clients/python"
```

Editable install for local development (from the repo root):

```bash
pip install -e ./clients/python
# or with test extras:
pip install -e "./clients/python[test]"
```

Runtime dependency: `httpx`.

## Configure

Two values fully determine the target instance:

```bash
export TCKDB_BASE_URL="http://localhost:8010/api/v1"
export TCKDB_API_KEY="tck_replace_me"  # optional for public scientific reads
```

API keys are minted on the **target instance**. Pointing at a different
`base_url` does not migrate or sync data — see
[`docs/clients/generic-client-targeting.md`](../../docs/clients/generic-client-targeting.md).

### When is the API key required?

| Operation | API key required client-side? |
|-----------|-------------------------------|
| Scientific reads (`search_*`, `get_species_thermo`, `get_reaction_*`, `get_geometry`) | **No** — anonymous-friendly. Set a key only if the deployment requires authentication or you want authenticated quotas. |
| `health()` | No |
| `me()`, uploads, bundle dry-run/submit, `post_json`, `get_json` | **Yes** — the client raises `TCKDBAuthenticationError` before sending. |

When an API key *is* configured, every request — including scientific
reads — forwards it as the `X-API-Key` header. That lets authenticated
deployments bill reads against a user identity.

> The client is not an abuse-control boundary. Hosted deployments
> should enforce abuse limits server-side through rate limits,
> pagination caps, query timeouts, and monitoring.

## Quick start

```python
from tckdb_client import TCKDBClient

# Anonymous: scientific reads + health
with TCKDBClient(base_url) as client:
    print(client.health())
    print(client.search_species(smiles="O"))

# Authenticated: required for uploads / me / admin
with TCKDBClient(base_url, api_key=api_key) as client:
    print(client.me())
```

## Authenticated upload

`upload()` accepts:

- a known short name from `UPLOAD_ENDPOINTS`
  (`conformer`, `reaction`, `kinetics`, `thermo`, `statmech`,
  `transport`, `transition_state`, `network`, `network_pdep`,
  `computed_reaction`),
- an explicit path beginning with `/`
  (e.g. `/uploads/some-future-endpoint`),
- or an absolute URL for advanced use.

Unknown short names are rejected client-side — `upload("thermos", ...)`
fails fast rather than silently posting to `/uploads/thermos`.

```python
from tckdb_client import TCKDBClient

with TCKDBClient(base_url, api_key=api_key) as client:
    # preferred: known short name
    client.upload("thermo", payload, idempotency_key="mytool:job-123:thermo:eth")

    # forward-compatible: explicit path for future endpoints
    client.upload("/uploads/some-future-endpoint", payload)
```

## Idempotency keys

Retry-safe writes use the conventional `Idempotency-Key` header. Build a
generic key with the helper, or supply your own opaque string (16-200
chars, `[A-Za-z0-9._:-]`):

```python
from tckdb_client import make_idempotency_key

key = make_idempotency_key("mytool", "job-123", "thermo", "ethanol")
client.upload("thermo", payload, idempotency_key=key)
```

### Caveat: `make_idempotency_key` sanitizes parts

`make_idempotency_key(*parts)` replaces any character outside
`[A-Za-z0-9._:-]` with `-` so callers don't have to pre-sanitize labels
like `"n-butane (s)"`. **This is lossy.** Two distinct logical inputs
that differ only in disallowed characters collapse to the same key:

```python
make_idempotency_key("foo bar", ...)   # -> "foo-bar:..."
make_idempotency_key("foo-bar", ...)   # -> "foo-bar:..."  (collision!)
```

For most v0 producers (a single tool naming jobs from its own ID space)
that's fine. Producer adapters that need stronger uniqueness guarantees
should either pass pre-normalized parts or append a stable payload-hash
suffix:

```python
key = make_idempotency_key("arc", job_id, output_kind, stable_payload_hash[:12])
```

The server treats the key as opaque — it never parses structure — so
adding a hash suffix is purely a producer-side strengthening.

To detect whether the server **replayed** a stored response (rather than
re-executing the write), use the lower-level wrapper:

```python
response = client.request_json(
    "POST", "/uploads/thermo", json=payload, idempotency_key=key,
)
if response.idempotency_replayed:
    print("server replayed a prior response")
print(response.data)
```

## Contribution bundles

```python
preview = client.bundle_dry_run(bundle)
result  = client.bundle_submit(bundle, idempotency_key=key)
```

## Errors

Every HTTP failure raises a structured exception that carries the parsed
response body, status code, and headers:

| Status | Exception |
|--------|-----------|
| 401 | `TCKDBAuthenticationError` |
| 403 | `TCKDBForbiddenError` |
| 422 | `TCKDBValidationError` |
| 409 (`code=idempotency_conflict`) | `TCKDBIdempotencyConflictError` |
| 409 (other) | `TCKDBConflictError` |
| 4xx/5xx (other) | `TCKDBHTTPError` |
| network / timeout | `TCKDBConnectionError` |

```python
from tckdb_client import TCKDBValidationError

try:
    client.upload("thermo", bad_payload)
except TCKDBValidationError as exc:
    print(exc.status_code, exc.detail)
```

## Scientific read/query methods

The client exposes thin wrappers over the backend's `/api/v1/scientific/*`
read surface. These methods are **generic** TCKDB reads — they serialize
parameters, call the backend, and return parsed JSON. They contain no
ARC- or RMG-specific selection or reuse policy, no client-side ranking,
and no notion of a "best" record. Trust posture, sort order, evidence
completeness, and provenance shape are all decided by the backend per
[`docs/specs/read_api_mvp.md`](../../docs/specs/read_api_mvp.md).

**Recommended — chemistry-first search** (use these for hosted workflow
tools that know identifiers, not entry ids):

```python
# Thermo by SMILES — one call, entry id and entry ref returned in the response
thermo = client.search_thermo(
    smiles="C[CH2]",
    temperature_min=300,
    temperature_max=3000,
    collapse="first",
    include=["provenance", "review"],
)

# Kinetics by reactants/products
kinetics = client.search_kinetics(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)
```

**Discovery-only** (use only when you want the identity layer without
records — e.g. listing matched candidates in a UI):

```python
species = client.search_species(smiles="C[CH2]")

rxns = client.search_reactions(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
)
# Force GET if you prefer query-string encoding:
rxns_get = client.search_reactions(
    reactants=["A"], products=["B"], method="GET",
)
```

**Ref-first follow-up reads** (preferred): each chemistry-first search
response includes ``*_ref`` public handles alongside the integer ids.
Use those refs when chaining into a detail endpoint — the path
parameter accepts either form.

```python
# Thermo detail keyed off a ref returned by search_thermo.
thermo = client.search_thermo(smiles="CN", collapse="first")
species_entry_ref = thermo["records"][0]["species"]["species_entry_ref"]

detail = client.get_species_thermo(
    species_entry_id=species_entry_ref,  # public ref accepted here
    temperature_min=300,
    temperature_max=2000,
)

# Composite "/full" keyed off a ref returned by search_kinetics.
kinetics = client.search_kinetics(
    reactants=["[CH3]", "[CH3]"],
    products=["CC"],
    direction="either",
    collapse="first",
)
reaction_entry_ref = kinetics["records"][0]["reaction"]["reaction_entry_ref"]

full = client.get_reaction_full(
    reaction_entry_id=reaction_entry_ref,  # public ref accepted here
    include=["species", "kinetics", "transition_states", "calculations", "review"],
)

# Level-of-theory ref works as a filter wherever level_of_theory_id does.
lot_ref = detail["records"][0]["provenance"]["level_of_theory"]["level_of_theory_ref"]
same_lot = client.search_species_calculations(
    smiles="CN",
    calculation_type="sp",
    level_of_theory_ref=lot_ref,
    ranking="lowest_energy",
    collapse="first",
)
```

**Entry-id detail / follow-up** (still supported for inspection,
curation, or chaining off ids you already hold):

```python
# Kinetics for a known reaction entry, sorted per the locked D9 chain
kinetics = client.get_reaction_kinetics(
    reaction_entry_id=51,
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
)

# Thermo for a known species entry
thermo = client.get_species_thermo(
    species_entry_id=31,
    temperature_min=300,
    temperature_max=3000,
    model_kind="nasa",
)

# Composite "everything supporting this reaction" document
full = client.get_reaction_full(
    reaction_entry_id=51,
    include=["kinetics", "transition_states", "calculations", "review"],
    include_review="full",
)
```

### Geometry detail reads

``species-calculations/search`` returns ``geometry_ref`` handles, not
coordinate payloads, because most callers don't need the full atom
list. Use ``get_geometry(geometry_handle)`` to retrieve coordinates
when you do.

```python
calcs = client.search_species_calculations(
    smiles="O",
    calculation_type="sp",
    ranking="lowest_energy",
    collapse="first",
    include=["provenance", "review"],
)

# SP calculations consume an input geometry; opt produces an output.
geom_block = calcs["records"][0]["geometry"]
geometry_handle = (
    geom_block["primary_output_geometry_ref"]
    or geom_block["input_geometries"][0]["geometry_ref"]
)

geometry = client.get_geometry(geometry_handle)
# {
#   "geometry_ref": "geom_…",
#   "natoms": 3,
#   "format": "cartesian",
#   "coordinate_units": "angstrom",
#   "symbols": ["O", "H", "H"],
#   "coords": [[0, 0, 0], [0, 0.76, 0.58], [0, -0.76, 0.58]],
#   "provenance": {
#     "produced_by": [{"calculation_ref": "calc_…", "calculation_type": "opt", "role": "final"}],
#     "used_as_input_by": [{"calculation_ref": "calc_…", "calculation_type": "sp"}]
#   }
# }
```

Notes:
- SP calculations usually have ``input_geometries`` but no output
  geometry; ``primary_output_geometry_ref`` is ``null`` for SP by
  design (the upload layer only auto-attaches an output geometry for
  ``opt``).
- Opt calculations should normally have ``primary_output_geometry_ref``
  set when an optimized geometry was persisted.
- The endpoint accepts an integer ``geometry.id`` too, for
  compatibility; public docs and examples prefer the ref form.

**Notes**

- **Phase D (default):** scientific read responses expose only public
  refs (``*_ref``). Integer primary keys (``*_id``) and bare
  integer-id arrays (``input_geometry_ids``, ``supporting_calculation_ids``,
  …) are hidden by default. Their ref-bearing object-array siblings
  (``input_geometries``, ``supporting_calculations``, …) remain visible.
- **Opt-in to integer IDs:** request the ``internal_ids`` include
  token. The opt-in is only effective when the deployment sets
  ``ALLOW_PUBLIC_INTERNAL_IDS=true``; in hosted production the token
  is silently dropped and the response stays refs-only.

  ```python
  # Refs only (default):
  thermo = client.search_thermo(smiles="CN", collapse="first")

  # Compatibility / debugging — request internal ids if the server allows them:
  thermo = client.search_thermo(
      smiles="CN",
      collapse="first",
      include=["provenance", "review", "internal_ids"],
  )
  ```

- ``include=all`` does **not** expand to ``internal_ids``; you must
  pass ``include=["all", "internal_ids"]`` to combine them.
- Public refs are the preferred hosted handles; integer IDs are
  internal/debug compatibility fields.
- Path parameters like ``reaction_entry_id`` / ``species_entry_id``
  accept either an integer PK or a public ref of the matching prefix
  (``rxe_...`` / ``spe_...``). A wrong-prefix ref returns 422
  (``handle_type_mismatch``); an unknown ref returns 404. The path
  parameter shape did **not** change in Phase D — only the response.
- Supplying both ``*_id`` and ``*_ref`` for the same filter is allowed
  only when they resolve to the same row; otherwise the backend returns
  422 with a stable ``<resource>_handle_conflict`` code.
- ``reaction_entry_id`` is strictly ``reaction_entry.id`` (not
  ``chem_reaction.id``). ``species_entry_id`` is strictly
  ``species_entry.id`` (not ``species.id``).
- Client-supplied ``sort=`` is not supported in v0; the backend returns
  422 if a ``sort`` query parameter is sent. The methods deliberately
  omit a ``sort`` argument for that reason.
- ``direction="exact"`` is rejected by the backend with 422.
- ``include`` accepts a Python list and is serialized as repeated query
  parameters (``?include=a&include=b``).
- Returned values are parsed JSON ``dict`` envelopes matching the
  response models in ``backend/app/schemas/reads/scientific_*.py``. The
  client does not impose typed models.

## Examples

- [`examples/basic_usage.py`](examples/basic_usage.py)
- [`examples/upload_json_file.py`](examples/upload_json_file.py)
- [`examples/submit_bundle.py`](examples/submit_bundle.py)
- [`examples/scientific_reads.py`](examples/scientific_reads.py)

### Runnable scientific read example

[`examples/scientific_reads.py`](examples/scientific_reads.py) exercises every
chemistry-first scientific read method against a live TCKDB deployment.
Empty results are reported with a friendly message; client errors print
`status_code`, `code`, and `detail` rather than swallowing them.

```bash
# Default — run the species-side queries against a local instance
python examples/scientific_reads.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --smiles "C[CH2]"

# Add reactants/products to also run reaction discovery + kinetics +
# a follow-up to /scientific/reaction-entries/{id}/full
python examples/scientific_reads.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --reactant "[CH3]" --reactant "c1ccccc1" \
  --product "CH4" --product "[c]1ccccc1"

# Filter species-calculations by LoT, dump raw JSON
python examples/scientific_reads.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --smiles "CCO" \
  --level-of-theory-id 8 \
  --json
```

The script never selects a "best" record. It uses the documented
`collapse="first"` semantics (first record under the backend's
deterministic ordering) and prints the underlying evidence — review
status, temperature coverage, evidence completeness, provenance — so the
caller can apply their own reuse policy.

#### Selecting which scientific reads to run

By default the script exercises every section it knows about, which is
useful as a demo but noisy when you're debugging one specific API.
Three CLI knobs narrow the output:

- `--only <sections>` — comma-separated list of sections to run.
- `--skip <sections>` — comma-separated list to remove (applied after
  `--only`).
- `--no-followups` — disable every follow-up call (`thermo-detail`,
  `lot-followup`, `geometry`, `full`), keeping only the primary
  searches.

Two additional flags re-target the species-calculations search itself
(it previously hard-coded `sp` + `lowest_energy`):

- `--calculation-type <type>` — backend `calculation_type` filter
  (default `sp`; common values: `sp`, `opt`, `freq`, `scan`, `irc`,
  `neb`, `path_search`, `conf`).
- `--ranking <ranking>` — backend `ranking` knob (default
  `lowest_energy`). Unsupported combinations surface the backend's
  normal validation error.

Section names: `species`, `thermo`, `thermo-detail`, `calculations`,
`lot-followup`, `geometry`, `reactions`, `kinetics`, `full`, plus
`all`. Unknown names raise a clean CLI error.

```bash
# Species search only.
python examples/scientific_reads.py --smiles "O" --only species

# Species-calculations search + the geometry follow-up.
python examples/scientific_reads.py --smiles "O" --only calculations,geometry

# Primary searches only; skip every follow-up.
python examples/scientific_reads.py --smiles "O" --no-followups

# Pull opt records instead of the default sp.
python examples/scientific_reads.py --smiles "O" --only calculations --calculation-type opt
```

When a section's dependency is missing (e.g. `geometry` without
`calculations`, or `kinetics` without `--reactant`/`--product`), the
script prints a one-line skip notice and continues.

## Tests

```bash
pytest
python -m py_compile examples/basic_usage.py examples/upload_json_file.py examples/submit_bundle.py
```

The test suite uses `httpx.MockTransport` and never contacts a live TCKDB
instance.

## Experimental builder layer

The package includes an experimental builder layer under
`tckdb_client.builders`. It constructs upload payloads from scientific
Python objects and sends them through the existing thin client.

Phase 1 supports computed species upload; Phase 2 adds computed reaction
upload (transition state + Arrhenius / modified-Arrhenius kinetics).

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

with TCKDBClient(base_url, api_key=api_key) as client:
    result = client.upload(upload)
```

### Computed reaction (Phase 2 + 3A)

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

#### Attaching reactant / product calculations (Phase 3A, experimental)

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
The Phase-3A builder supports **one opt per species**; multi-conformer
uploads still need the raw payload form.

#### Attaching thermo to reactant / product species (Phase 3B, experimental)

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
rather than guessing an endpoint. Thermo, statmech, and artifact
helpers are deferred to Phase 3; see
[`docs/builder_api_mvp.md`](docs/builder_api_mvp.md) for the full
phased rollout and open design questions.

## Non-goals (v0)

- no chemistry adapters / no ARC, RMG, RDKit, ASE, cclib, Arkane imports
- no async client (deferred)
- no automatic retries
- no payload sidecar / on-disk replay management
- no OpenAPI-generated client code
- no direct database access
