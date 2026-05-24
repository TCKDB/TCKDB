# tckdb-client

Generic synchronous Python HTTP client for the [TCKDB](https://github.com/tckdb) API.

`tckdb-client` is the **transport layer** for any TCKDB consumer — scripts,
notebooks, post-processing jobs, future producer-specific adapters (ARC,
RMG, …). It accepts already-formed JSON payloads and sends them; it does
**not** know how to construct chemistry payloads, and it has **no**
chemistry dependencies.

## Install

From the package directory:

```bash
pip install -e .
# or with test extras:
pip install -e ".[test]"
```

Runtime dependency: `httpx`.

## Configure

Two values fully determine the target instance:

```bash
export TCKDB_BASE_URL="http://localhost:8000/api/v1"
export TCKDB_API_KEY="tck_replace_me"
```

API keys are minted on the **target instance**. Pointing at a different
`base_url` does not migrate or sync data — see
[`docs/clients/generic-client-targeting.md`](../../../docs/clients/generic-client-targeting.md).

## Quick start

```python
from tckdb_client import TCKDBClient

with TCKDBClient(base_url, api_key=api_key) as client:
    print(client.health())
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
[`docs/specs/read_api_mvp.md`](../../../docs/specs/read_api_mvp.md).

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
  --base-url http://127.0.0.1:8000/api/v1 \
  --smiles "C[CH2]"

# Add reactants/products to also run reaction discovery + kinetics +
# a follow-up to /scientific/reaction-entries/{id}/full
python examples/scientific_reads.py \
  --base-url http://127.0.0.1:8000/api/v1 \
  --reactant "[CH3]" --reactant "c1ccccc1" \
  --product "CH4" --product "[c]1ccccc1"

# Filter species-calculations by LoT, dump raw JSON
python examples/scientific_reads.py \
  --base-url http://127.0.0.1:8000/api/v1 \
  --smiles "CCO" \
  --level-of-theory-id 8 \
  --json
```

The script never selects a "best" record. It uses the documented
`collapse="first"` semantics (first record under the backend's
deterministic ordering) and prints the underlying evidence — review
status, temperature coverage, evidence completeness, provenance — so the
caller can apply their own reuse policy.

## Tests

```bash
pytest
python -m py_compile examples/basic_usage.py examples/upload_json_file.py examples/submit_bundle.py
```

The test suite uses `httpx.MockTransport` and never contacts a live TCKDB
instance.

## Non-goals (v0)

- no chemistry adapters / no ARC, RMG, RDKit, ASE, cclib, Arkane imports
- no async client (deferred)
- no automatic retries
- no payload sidecar / on-disk replay management
- no OpenAPI-generated client code
- no direct database access
