# Scientific reads, geometry, artifact download and runnable read examples

Moved from the package README.

## Scientific read/query methods

The client exposes thin wrappers over the backend's `/api/v1/scientific/*`
read surface. These methods are **generic** TCKDB reads — they serialize
parameters, call the backend, and return parsed JSON. They contain no
ARC- or RMG-specific selection or reuse policy, no client-side ranking,
and no notion of a "best" record. Trust posture, sort order, evidence
completeness, and provenance shape are all decided by the backend per
`docs/specs/browse_order.md`.

### Curated vs exploratory results: `profile=`

Every scientific read takes an optional `profile=`. `profile="curated"`
applies the `approved` review floor to the records a list, search, browse,
select or export returns, and shows the evidence behind a returned record
with its review badge where the response carries one (only `rejected` and
`deprecated` are withheld; a legacy single-record read states the record's
own status in the `X-TCKDB-Review-Status` header). It does not
mean a curator selected those records. `profile="exploratory"` (the
default when you pass nothing) returns every visible candidate. The
answering profile comes back in `response["request"]["profile"]` —
read that rather than assuming you got what you asked for.

```python
kinetics = client.search_kinetics(reactants=["C[CH2]"], profile="curated")
# One-sided queries mean containment: "kinetics for reactions consuming
# the ethyl radical", products unconstrained. Pass match="exact" to ask
# for one specific equation instead (both sides, counts included).
kinetics["request"]["profile"]                  # what actually answered
kinetics["request"]["profile_recommendation"]   # what TCKDB recommends
```

There is deliberately **no** `release=` parameter on any method. Scoping
a read to a published dataset release is done through the release's own
path — `GET /scientific/releases/{handle}/selections` and its artifacts,
reachable via `get_json()` — not through a query parameter. Supplying
`?release=` to any other scientific endpoint is answered with a 422.

**Recommended — chemistry-first search** (use these for hosted workflow
tools that know identifiers, not entry ids):

```python
# Thermo by SMILES — one call, entry id and entry ref returned in the response
thermo = client.search_thermo(
    smiles="C[CH2]",
    coverage_temperature_min_k=300,
    coverage_temperature_max_k=3000,
    include=["provenance", "review"],
)

# Kinetics by reactants/products
kinetics = client.search_kinetics(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    coverage_temperature_min_k=300,
    coverage_temperature_max_k=2000,
    include=["provenance", "review"],
)
```

Searches still return ordinary JSON dictionaries. Exported ``TypedDict``
aliases add editor/type-checker guidance without adding runtime validation:

```python
from tckdb_client import NetworkKineticsSearchResponse

page: NetworkKineticsSearchResponse = client.search_network_kinetics(
    source_species_entry_refs=["spe_reactant", "spe_reactant"],
    sink_species_entry_refs=["spe_product"],
    include=["coefficients"],
)
```

Each supported paginated search also has a lazy ``iter_*`` form. It keeps
filters and include tokens unchanged, follows returned pagination metadata,
and raises ``TCKDBPaginationError`` on malformed, changing, or non-advancing
pages. No read collapses its records to one, so ``pagination.total`` is the only
total. "Give me one" is the job of the ``select_*`` methods.

```python
for record in client.iter_network_kinetics(
    source_smiles=["[CH3]", "[CH3]"],
    sink_smiles=["CC"],
    include=["coefficients", "source_calculations"],
):
    print(record["network_kinetics"]["network_kinetics_ref"])
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
    reactants=["A"], products=["B"], method_http="GET",
)
```

**Ref-first follow-up reads** (preferred): each chemistry-first search
response includes ``*_ref`` public handles alongside the integer ids.
Use those refs when chaining into a detail endpoint — the path
parameter accepts either form.

```python
# Thermo detail keyed off a ref returned by search_thermo.
thermo = client.search_thermo(smiles="CN")
species_entry_ref = thermo["records"][0]["species"]["species_entry_ref"]

detail = client.get_species_thermo(
    species_entry_id=species_entry_ref,  # public ref accepted here
    coverage_temperature_min_k=300,
    coverage_temperature_max_k=2000,
)

# Composite "/full" keyed off a ref returned by search_kinetics.
kinetics = client.search_kinetics(
    reactants=["[CH3]", "[CH3]"],
    products=["CC"],
    direction="either",
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
)
```

**Entry-id detail / follow-up** (still supported for inspection,
curation, or chaining off ids you already hold):

```python
# Kinetics for a known reaction entry, listed in display order (approved first, then newest deposited; not a preference);
# coverage_temperature_min_k/max_k only fill each record's coverage field (they filter nothing)
kinetics = client.get_reaction_kinetics(
    reaction_entry_id=51,
    coverage_temperature_min_k=300,
    coverage_temperature_max_k=2000,
)

# Thermo for a known species entry
thermo = client.get_species_thermo(
    species_entry_id=31,
    coverage_temperature_min_k=300,
    coverage_temperature_max_k=3000,
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
  thermo = client.search_thermo(smiles="CN")

  # Compatibility / debugging — request internal ids if the server allows them:
  thermo = client.search_thermo(
      smiles="CN",
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
  response models in ``backend/app/schemas/reads/scientific_*.py``. Public
  ``TypedDict`` aliases describe that wire shape but do not wrap or coerce it.

## Download an artifact

Raw artifact bytes are served only to authenticated callers -- always, on
every deployment. Unredacted ESS logs can carry producer-side scratch
paths, usernames and cluster hostnames, so the gate has no opt-out
(ADR 0004). Set your key first:

```bash
export TCKDB_API_KEY=tck_...
```

`--api-key-env` names a *different variable* to read the key from; it
never takes the key itself.

From the command line:

```bash
tckdb download artifact <sha256>                 # ./output_log_art_7k2p9x.log
tckdb download artifact <sha256> -o job.log      # ./job.log
tckdb download artifact <sha256> -o ~/downloads  # ~/downloads/<derived name>
tckdb download artifact <sha256> -o - | less     # straight to stdout
```

An existing file is never overwritten without `--force`. The bytes are
hashed and checked against the digest you asked for **before** anything
is written, so a mismatch -- a proxy or cache serving the wrong object --
fails loudly rather than landing on disk under a name that asserts a
digest it does not have.

The default filename is `<kind>_<artifact_ref><ext>` -- what the file is,
which record it belongs to, and its format. Deriving it costs one extra
metadata lookup, skipped entirely when `--output` is given, and falls
back to the digest if the archive knows neither.

The recorded filename is deliberately not used as the lead. On this
corpus it identifies nothing and reads as the opposite of the truth: 317
artifacts of kind `output_log` are all called `input.log`, because
`g16 input.gjf` writes `input.log` -- Gaussian names the output after the
job, not its role. The extension still comes from the recorded name,
since that is the part a reader needs.

The filename is stored data, so it is not trusted: only the basename
survives, and only characters that cannot mean anything to a path.

From Python:

```python
raw = client.download_artifact("<sha256>")
```

Exit codes: `0` written, `4` no artifact with that digest is readable by
your credential (either it is not in the archive, or it is neither
approved nor one of your own deposits), `1` anything else.

## Examples

- [`examples/basic_usage.py`](../examples/basic_usage.py))
- [`examples/upload_json_file.py`](../examples/upload_json_file.py))
- [`examples/submit_bundle.py`](../examples/submit_bundle.py))
- [`examples/scientific_reads.py`](../examples/scientific_reads.py))

### Runnable scientific read example

[`examples/scientific_reads.py`](../examples/scientific_reads.py)) exercises every
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

The script never selects a "best" record. It lists the records in the backend's
deterministic display order, reports the first of them (first in display order,
not a recommendation) and prints the underlying evidence — review
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
