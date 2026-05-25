# Demo Data

The demo dataset gives you non-empty scientific read responses on a
fresh local deployment.

It includes methane, ethane, radicals, geometries, thermo,
calculations, conformer data, two reactions, and kinetics. The values
are illustrative only and are not suitable for publication or reuse as
scientific reference data.

## Load It

Start from the repository root after `make up` has completed:

```bash
cd backend

# Dry run first; writes nothing.
PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py

# Actually write the demo rows.
PYTHONPATH=. conda run -n tckdb_env python scripts/seed_scientific_demo_data.py --yes
```

Keep the API running with `make api` from the repository root.

## What Success Looks Like

After loading the demo data, methane queries using `smiles=C` should
return non-empty `records`.

```bash
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C"
```

For all details, including cleanup notes and a full list of seeded
tables, see [Scientific read demo data](../guides/scientific_read_demo_data.md).
