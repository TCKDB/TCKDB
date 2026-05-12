# Scientific read demo data

A tiny dataset for exercising TCKDB's chemistry-first scientific
read/query endpoints on a freshly deployed instance.

> ⚠ This data is **illustrative only**. Numeric values (energies,
> NASA coefficients, Arrhenius parameters, temperatures) are not
> publication-grade. Do not cite or reuse them scientifically.

## What the demo data contains

Loaded by [`backend/scripts/seed_scientific_demo_data.py`](../../backend/scripts/seed_scientific_demo_data.py)
into the database configured by `app.api.config.settings.database_url`:

| Table | Demo rows | Notes |
|---|---|---|
| `level_of_theory` | 1 | `wb97xd/def2tzvp`, shared by all computed records |
| `species` | 6 | `[H]`, `[H][H]`, `C` (CH₄), `[CH3]`, `CC` (C₂H₆), `[CH2]C` (C₂H₅) |
| `species_entry` | 6 | one ground-state minimum entry per species |
| `thermo` | 2 | scalar h298/s298 for CH₄; scalar+NASA for C₂H₆ |
| `thermo_nasa` | 1 | attached to the C₂H₆ thermo record |
| `geometry` | 2 | one per opt calculation that has output geometry |
| `calculation` | 4 | CH₄ opt, CH₄ sp, C₂H₆ opt (with conformer), CH₃ opt |
| `conformer_group` + `conformer_observation` | 1+1 | C₂H₆ "anti" conformer attached to its opt |
| `chem_reaction` | 2 | CH₃ + H₂ → CH₄ + H, CH₃ + CH₃ → C₂H₆ |
| `reaction_entry` | 2 | one entry per chem_reaction |
| `kinetics` | 2 | TS-backed-style modified Arrhenius for reaction 1; experimental for reaction 2 (non-TS-backed — null TS-chain provenance) |

Every demo row that has a `note` column is tagged with
`note='TCKDB demo data'` for easy auditing/cleanup.

Every seeded row also gets a populated `public_ref` automatically (the
ORM `before_insert` listener installed at app startup populates it for
events, and identity rows derive theirs from canonical content). So
once the demo data is loaded, scientific read responses for these rows
expose the standard Phase B / Phase C public handles —
`species_ref` (`spc_...`), `species_entry_ref` (`spe_...`),
`reaction_ref` (`rxn_...`), `reaction_entry_ref` (`rxe_...`),
`kinetics_ref` (`kin_...`), `thermo_ref` (`thm_...`),
`calculation_ref` (`calc_...`), `level_of_theory_ref` (`lot_...`), and
so on. Use those refs for follow-up reads where the examples below
chain into a detail endpoint.

## How to load it

The script is **opt-in** — without `--yes` it dry-runs and writes nothing.

```bash
cd backend

# Dry run (writes nothing):
PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py

# Actually load demo rows:
PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py --yes

# Override the database URL:
PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py --yes \
    --database-url postgresql+psycopg://tckdb:tckdb@127.0.0.1:5432/tckdb_demo
```

The script targets `app.api.config.settings.database_url` by default.
**Do not run it against a production database.** It is not idempotent —
re-running with `--yes` creates duplicate demo rows.

## How to query it

After loading, exercise the demo via the runnable example:

```bash
# Species + thermo + species-calculations queries for CH4
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C"

# Plus reaction discovery + kinetics for CH3 + H2 → CH4 + H
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" \
    --reactant "[CH3]" --reactant "[H][H]" \
    --product "C" --product "[H]"

# Lowest SP energy for CH4 with raw JSON output
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" \
    --json
```

You should see non-empty `records` for `search_species`,
`search_thermo`, `search_species_calculations`, `search_reactions`, and
`search_kinetics`. Phase D: each record carries the public `*_ref`
fields only; the runnable example prints refs as primary handles in
its human-readable output. Integer `*_id` fields are hidden by default
and can be restored by passing `--include-internal-ids` to the
example script (only effective when the deployment sets
`ALLOW_PUBLIC_INTERNAL_IDS=true`). The kinetics call against the
CH₃ + H₂ → CH₄ + H reaction returns a TS-backed-style record; querying
the CH₃ + CH₃ → C₂H₆ reaction returns a non-TS-backed record
(`scientific_origin = experimental`, all `transition_state_*` provenance
ref siblings null).

The example script then chains a few follow-up reads using the public
refs returned above (Phase C):

- `get_species_thermo(species_entry_id=<species_entry_ref>)` —
  same thermo records, fetched via the ref path handle.
- `search_species_calculations(level_of_theory_ref=<level_of_theory_ref>)`
  — re-runs the calculations search filtered by the LoT ref discovered
  on the first hit.
- `get_reaction_full(reaction_entry_id=<reaction_entry_ref>)` —
  composite provenance document for the reaction entry, again via the
  ref path handle.

A LoT ref filter (or supplying `--level-of-theory-ref lot_…` on the
command line) is the preferred way to scope calculation searches to a
specific level of theory; the integer `--level-of-theory-id` form
still works during the compatibility window.

### Geometry detail follow-up

After `search_species_calculations` picks an SP or opt calc, the
example script also chains into
`get_geometry(geometry_handle)` — it prefers
`primary_output_geometry_ref` (set for opt) and falls back to the
first `input_geometries[*].geometry_ref` (set for sp/freq/etc.).

`species-calculations/search` returns geometry refs only; the detail
endpoint returns the full coordinate payload (symbols, Cartesian
coords in Ångström) plus a compact provenance summary. SP rows have
`output_geometries=[]` and `primary_output_geometry_ref=null` by
design; opt rows should have a populated
`primary_output_geometry_ref` when the optimized geometry was
persisted.

### Running only the demo section you care about

The example script is a multi-call demo: by default it runs
`search_species`, `search_thermo`, the thermo detail follow-up,
`search_species_calculations`, the LoT follow-up, the geometry
follow-up, and (if reactants/products are supplied) the reaction +
kinetics + `/full` chain. Narrow the output with:

```bash
# Just the species discovery call.
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" --only species

# Species-calculations + geometry follow-up.
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" --only calculations,geometry

# Disable every follow-up call.
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" --no-followups

# Re-target the species-calculations search at opt records.
python clients/python/tckdb-client/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8000/api/v1 \
    --smiles "C" --only calculations --calculation-type opt
```

Section names: `species`, `thermo`, `thermo-detail`, `calculations`,
`lot-followup`, `geometry`, `reactions`, `kinetics`, `full`, plus
`all`. When a section's data dependency is missing (e.g. `geometry`
without `calculations`), the script prints a one-line skip notice
and continues.

## How to remove it

The script does not provide an unload command. Two options:

1. **Drop the database** (development only):
   ```bash
   PGPASSWORD=tckdb dropdb -h 127.0.0.1 -U tckdb tckdb_demo
   PGPASSWORD=tckdb createdb -h 127.0.0.1 -U tckdb tckdb_demo
   DB_NAME=tckdb_demo conda run -n tckdb_env alembic upgrade head
   ```

2. **Targeted SQL** (since every demo row carries
   `note='TCKDB demo data'` where the column exists):
   ```sql
   DELETE FROM thermo                WHERE note = 'TCKDB demo data';
   DELETE FROM kinetics              WHERE note = 'TCKDB demo data';
   DELETE FROM conformer_observation WHERE note = 'TCKDB demo data';
   DELETE FROM conformer_group       WHERE note = 'TCKDB demo data';
   -- species/species_entry/calculation rows have no `note` column;
   -- look up the demo SMILES (CH4/[CH3]/[H][H]/CC/[CH2]C/[H]) and
   -- delete the matching rows manually if you need a precise cleanup.
   ```

   Cleanup is intentionally manual — TCKDB's append-only ingestion
   model means the seed script doesn't carry a "delete what I created"
   handle, and a careless mass-delete could touch real records that
   happened to share the same SMILES.

## Warnings

- **Demo data is not curated science.** Energies, NASA coefficients,
  Arrhenius parameters, temperatures are illustrative only.
- **The script is not idempotent.** Re-running creates duplicate rows;
  this is intentional to keep the script small and free of opinionated
  conflict resolution.
- **No production database.** The script writes via SQLAlchemy with no
  guardrails beyond `--yes`. Targeted at local dev databases or a
  dedicated demo deployment.
- **Demo data can affect query rankings.** `lowest_energy` ranking on
  the demo CH₄ SP record will return the demo value (-40.5183 Ha)
  before any real record at the same level of theory. If a hosted
  instance accepts both real and demo data, plan to migrate the demo
  rows out before going public.
