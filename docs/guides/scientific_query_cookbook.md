# TCKDB scientific query cookbook

A practical, recipe-style guide to querying TCKDB as a hosted
scientific information system. Every example uses `tckdb-client`
directly — no ARC-specific wrappers, no integer-ID assumptions, no
client-side ranking.

> New to hosted TCKDB querying? Start with
> [public_hosted_querying.md](public_hosted_querying.md) for the
> entry-point overview (anonymous reads, refs, abuse-control
> expectations) before working through the recipes here.

The corresponding runnable script is
[`clients/python/examples/query_cookbook.py`](../../clients/python/examples/query_cookbook.py).
Each recipe in this guide maps 1:1 to a function named
`recipe_<name>` in that script — pick one, lift it into your code,
and it should work.

```bash
python examples/query_cookbook.py --recipe list
python examples/query_cookbook.py --recipe lowest_sp_energy --smiles "O"
python examples/query_cookbook.py --recipe all --smiles "O" --json
```

## Operating principles

- **Refs are the normal hosted handles.** Every response carries a
  `*_ref` field next to anywhere an integer id once lived. Use the
  ref to chain follow-up reads.
- **Integer ids are compatibility / debug fields.** They are hidden
  by default. Pass `include=["internal_ids"]` and run against a
  deployment that sets `ALLOW_PUBLIC_INTERNAL_IDS=true` to see them
  again (see [internal-ids policy](../specs/internal_ids_visibility_policy.md)).
- **Hosted search is chemistry-first.** Callers ask about SMILES,
  reactants/products, level of theory — not table primary keys. The
  backend returns the resolved identity (ref) along with the
  scientific payload, so chaining is "free."
- **No client-side ranking.** Every method passes `collapse=`,
  `ranking=`, `min_review_status=`, etc. straight to the backend.
  The backend's documented sort order is the source of truth.

## Setup

```python
from tckdb_client import TCKDBClient

client = TCKDBClient(
    base_url="http://127.0.0.1:8010/api/v1",   # include /api/v1
    api_key=None,                               # optional for public scientific reads
    timeout=30.0,
)
```

`api_key` is optional for public scientific reads. Use it only when the
deployment requires authentication or when you need authenticated
quotas/features. If an `api_key` is set, the client forwards it on every
request (including reads), so authenticated deployments still see a
billable identity.

> Public reads being anonymous-friendly in the client is not an
> abuse-control mechanism. Hosted deployments should enforce abuse
> limits server-side through rate limits, pagination caps, query
> timeouts, and monitoring.

Use as a context manager when convenient: `with TCKDBClient(...) as client:` — that closes the underlying HTTP connection at scope exit.

---

## Recipe 1 — Species search by SMILES

**Q:** What species does TCKDB have that match this SMILES?

```python
species = client.search_species(
    smiles="O",
    include=["review"],
    collapse="all",
)

for record in species["records"]:
    print(record["species_ref"], record["canonical_smiles"])
    for entry in record["entries"]:
        print(
            "  ", entry["species_entry_ref"],
            "review:", entry["review"]["status"],
            "availability:", entry["availability"],
        )
```

What you get back:

- `records[*].species_ref` — public handle for the chemical species (one per identity).
- `records[*].entries[*].species_entry_ref` — handle for one physical *species entry* (a specific charge / multiplicity / electronic state realization). This is the handle every other species-side endpoint takes.
- `records[*].entries[*].availability` — boolean flags + counts so the caller can see at a glance whether thermo / statmech / conformers / calculations exist.

Common gotchas:

- Multiple `entries` per species is the normal case (e.g. ground-state vs excited-state variants of the same molecule).
- `inchi` currently has no enforced search path, so supplying it returns 422 `unsupported_filter`; use `smiles`, `inchi_key`, `formula` (derived through RDKit), or a public ref.

---

## Recipe 2 — Thermo search by species

**Q:** Give me the best thermo record for this species, scoped to the temperature window I care about.

```python
thermo = client.search_thermo(
    smiles="O",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)

rec = thermo["records"][0]
print(rec["species"]["species_entry_ref"], rec["thermo"]["thermo_ref"])
print("H298 =", rec["thermo"]["h298_kj_mol"], "kJ/mol")
print("S298 =", rec["thermo"]["s298_j_mol_k"], "J/mol/K")
```

The backend's locked sort order for thermo (`covers_requested_temperature_range DESC, extrapolation_distance_k ASC, review_rank ASC, evidence_completeness DESC, created_at DESC, id DESC`) decides who wins for `collapse="first"`. The client does not re-rank.

Useful fields:

- `records[*].thermo.model_kind` — `nasa` (polynomial coefficients in `records[*].thermo.nasa`), `points` (in `records[*].thermo.points`), or `scalar` (just `h298` / `s298`).
- `records[*].thermo.temperature_coverage` — `covers_requested_range`, `extrapolation_distance_k`. Diagnostic only; not for client-side scoring.
- `records[*].thermo.evidence_completeness` — score + per-predicate checklist. Same warning: not a "best" selector.

---

## Recipe 3 — Thermo provenance inspection

**Q:** What freq / SP calculations stand behind this thermo record?

```python
rec = thermo["records"][0]
prov = rec["thermo"]["provenance"]

print("statmech_ref         :", prov["statmech_ref"])
print("freq_calculation_ref :", prov["freq_calculation_ref"])
print("sp_calculation_ref   :", prov["sp_calculation_ref"])

lot = prov["level_of_theory"] or {}
sw  = prov["software"] or {}
print("level_of_theory_ref  :", lot.get("level_of_theory_ref"),
      f"({lot.get('method')}/{lot.get('basis')})")
print("software_release_ref :", sw.get("software_release_ref"),
      f"({sw.get('software')} {sw.get('version')})")
```

Important detail:

- For computed thermo derived from a statmech, the read service **falls back** to the statmech's source calculations when the thermo itself didn't declare any. So `freq_calculation_ref` and `sp_calculation_ref` are usually populated even when the thermo has no `ThermoSourceCalculation` rows of its own.
- Explicit thermo source calcs always take precedence over the statmech fallback. The fallback is invisible to the caller — the response shape is identical either way.

---

## Recipe 4 — Kinetics search by reactants/products

**Q:** Find kinetics for this elementary reaction.

```python
kinetics = client.search_kinetics(
    reactants=["[CH3]", "[CH3]"],
    products=["CC"],
    direction="either",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)

rec = kinetics["records"][0]
reaction = rec["reaction"]
params   = rec["kinetics"]["parameters"]
print("reaction_entry_ref:", reaction["reaction_entry_ref"])
print("matched_direction :", reaction["matched_direction"])
print("kinetics_ref      :", rec["kinetics"]["kinetics_ref"])
print(f"A = {params['A']} ({params['A_units']}) n = {params['n']}"
      f" Ea = {params['Ea_kj_mol']} kJ/mol")
```

Notes:

- `direction="either"` matches in either forward or reverse orientation; the response says which (`matched_direction`).
- `direction="exact"` is **not** supported in v0 — the backend returns 422.
- Non-TS-backed kinetics (experimental, estimated, network-derived, …) come through with `provenance.transition_state_entry_ref = null` and every `ts_*_calculation_ref = null`. That's not an error — it's how the schema signals "no transition-state chain to follow."

---

## Recipe 5 — Species-calculation search by species + level of theory

**Q:** What `sp` calculations exist for this species at any level of theory? (Or scope to one LoT.)

```python
# All calculations of a given type for this species.
calcs = client.search_species_calculations(
    smiles="O",
    calculation_type="sp",
    collapse="all",
    include=["provenance", "review"],
)

for rec in calcs["records"]:
    calc = rec["calculation"]
    lot  = rec["level_of_theory"]
    print(
        calc["calculation_ref"],
        calc["calculation_type"],
        calc["calculation_quality"],
        lot["method"], lot["basis"],
        lot["level_of_theory_ref"],
    )
```

Scope to one LoT (refs are the preferred filter):

```python
calcs_at_lot = client.search_species_calculations(
    smiles="O",
    calculation_type="sp",
    level_of_theory_ref="lot_…",
    collapse="all",
)
```

Both `level_of_theory_id` (compatibility) and `level_of_theory_ref` are accepted as filters. Supplying both and disagreeing returns 422 `level_of_theory_handle_conflict`.

---

## Recipe 6 — Lowest single-point energy retrieval

**Q:** For this species, what's the lowest SP energy across all levels of theory in TCKDB?

```python
lowest = client.search_species_calculations(
    smiles="O",
    calculation_type="sp",
    ranking="lowest_energy",
    collapse="first",
    include=["provenance", "review"],
)

rec = lowest["records"][0]
energy = rec["energy"]
lot    = rec["level_of_theory"]
print(rec["calculation"]["calculation_ref"],
      energy["energy_hartree"], energy["energy_kind"],
      lot["method"], lot["basis"])
```

Rules:

- `ranking="lowest_energy"` requires `calculation_type` ∈ {`sp`, `opt`} — otherwise the backend returns 422 `unsupported_ranking_for_calculation_type`. The client doesn't validate; the error surfaces with a stable code.
- For `sp`, the energy comes from `CalculationSPResult.electronic_energy_hartree`. For `opt`, from `CalculationOptResult.final_energy_hartree`.
- Records whose energy column is null sort to the end (`NULLS LAST`).

---

## Recipe 7 — Optimized geometry retrieval

**Q:** For this species, give me an `opt` calculation and the ref of its optimized geometry.

```python
opt = client.search_species_calculations(
    smiles="O",
    calculation_type="opt",
    collapse="first",
    include=["provenance", "review"],
)

rec = opt["records"][0]
geom = rec["geometry"]
print("calculation_ref            :", rec["calculation"]["calculation_ref"])
print("primary_output_geometry_ref:", geom["primary_output_geometry_ref"])
print("primary_output_geometry_role:", geom["primary_output_geometry_role"])
print("input_geometries           :",
      [g["geometry_ref"] for g in geom["input_geometries"]])
print("output_geometries          :",
      [g["geometry_ref"] for g in geom["output_geometries"]])
```

Geometry expectations by calculation type:

| Type | `input_geometries` | `output_geometries` | `primary_output_geometry_ref` |
|---|---|---|---|
| `opt` | populated | auto-attached `final` role (if upload omitted) | populated |
| `sp` | populated | empty by design | `null` by design |
| `freq` | populated | producer-declared only | producer-declared only |
| `scan` / `irc` / `path_search` | producer-declared | producer-declared (per-point) | producer-declared |

Don't assume an SP has an output geometry — that's the schema speaking.

---

## Recipe 8 — Geometry coordinate download by `geometry_ref`

**Q:** I have a `geom_…` ref. Give me the atoms.

```python
geometry = client.get_geometry("geom_…", include=["provenance"])

print("geometry_ref      :", geometry["geometry_ref"])
print("natoms            :", geometry["natoms"])
print("format / units    :", geometry["format"], "/", geometry["coordinate_units"])
print("symbols           :", geometry["symbols"])
print("coords (Ångström) :")
for sym, (x, y, z) in zip(geometry["symbols"], geometry["coords"]):
    print(f"  {sym:<2} {x:>10.3f} {y:>10.3f} {z:>10.3f}")

prov = geometry["provenance"]
print("produced_by       :",
      [(p["calculation_ref"], p["role"]) for p in prov["produced_by"]])
print("used_as_input_by  :",
      [p["calculation_ref"] for p in prov["used_as_input_by"]])
```

Notes:

- The path parameter accepts either the integer `geometry.id` (compatibility) or a `geom_…` public ref. A wrong-prefix ref returns 422; an unknown ref returns 404.
- The `provenance` block lists every calculation that produced this geometry (with `role`) and every calculation that consumed it (no role — input links have no role column in v0). Useful for tracing why a geometry exists.

---

## Recipe 9 — Ref-based follow-up reads (chaining)

**Q:** How do hosted workflows compose multiple reads safely?

The shape of every hosted workflow is:

```
chemistry  →  search_*(…)  →  ref  →  get_*(ref)
```

Concrete example: SMILES → thermo search → species-entry ref → entry-detail thermo read.

```python
summary = client.search_thermo(
    smiles="O",
    temperature_min=300, temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)

species_entry_ref = summary["records"][0]["species"]["species_entry_ref"]

detail = client.get_species_thermo(
    species_entry_id=species_entry_ref,   # ref accepted on the path
    temperature_min=300, temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)
```

Other ref-based handles you'll commonly see in responses:

| Where it appears | Ref name | Used by |
|---|---|---|
| `search_thermo()` / `search_species_calculations()` | `species_entry_ref` | `get_species_thermo(species_entry_id=<ref>)` |
| `search_kinetics()` / `search_reactions()` | `reaction_entry_ref` | `get_reaction_kinetics(reaction_entry_id=<ref>)` and `get_reaction_full(reaction_entry_id=<ref>)` |
| `search_species_calculations()` | `level_of_theory_ref` | filter on subsequent `search_species_calculations(level_of_theory_ref=<ref>)` |
| `search_species_calculations()` (opt records) | `primary_output_geometry_ref` | `get_geometry(<ref>)` |
| `search_species_calculations()` (sp records) | `input_geometries[*].geometry_ref` | `get_geometry(<ref>)` |

The detail endpoints (`get_species_thermo`, `get_reaction_kinetics`, `get_reaction_full`) all accept either the integer PK or a public ref as the path parameter, and supplying both `*_id` and `*_ref` in query filters validates consistency (mismatch → 422 `<resource>_handle_conflict`).

---

## Recipe 10 — Optional: requesting integer IDs (local / debug)

**Q:** I'm running TCKDB locally and want to see integer IDs for debugging.

```python
response = client.search_species(
    smiles="O",
    include=["review", "internal_ids"],
)
print("request echo include:", response["request"]["include"])

rec = response["records"][0]
print("species_ref:", rec["species_ref"])
print("species_id :", rec.get("species_id", "(hidden)"))
```

How the token resolves:

- **`ALLOW_PUBLIC_INTERNAL_IDS=False` (hosted production default):** `internal_ids` is silently dropped from the resolved include set. It does **not** appear in `response["request"]["include"]`, and the response keeps the refs-only shape. No 4xx.
- **`ALLOW_PUBLIC_INTERNAL_IDS=True` (local / dev / explicitly enabled):** `internal_ids` appears in the echo and integer `*_id` fields are restored everywhere they exist — including in nested provenance summaries, on geometry detail responses, and inside the `/full` audit array.

Two related rules:

- `include=all` does **not** expand to `internal_ids`. If you genuinely want everything, pass `include=["all", "internal_ids"]`.
- Caller-supplied integer-id filters (`level_of_theory_id=8`, `species_entry_id=31`, …) are always echoed back in `request.filter` regardless of the visibility setting — request echoes mirror caller input. The visibility rule applies only to ids the backend resolved internally (e.g. an integer behind a ref) which are never leaked into the echo.

---

## Cross-references

- [Workflow-tool integration guide](workflow_tool_scientific_reads.md) — the long-form prose tour of the read API.
- [Public identifier policy](../specs/public_identifier_policy.md) — the design behind the `*_ref` scheme.
- [Internal-ID visibility policy](../specs/internal_ids_visibility_policy.md) — Phase D contract for the `internal_ids` opt-in.
- [Scientific read demo data](scientific_read_demo_data.md) — the local seed dataset you can hit with every recipe in this cookbook.
