# tckdb-chemkin

Reads a CHEMKIN mechanism (`chem.inp`, with optional `therm.dat` and
`tran.dat`) and turns it into TCKDB thermo, transport and kinetics uploads.

```
pip install tckdb-chemkin
```

It installs `tckdb-client` and RDKit with it. From a checkout of
[TCKDB](https://github.com/TCKDB/TCKDB), `pip install -e clients/python/adapters/chemkin`.

## Command

```
tckdb-chemkin-import chem.inp --thermo therm.dat --transport tran.dat \
    --species-dict species_dictionary.txt --species-map map.csv \
    --mechanism-name GRI-Mech --mechanism-version 3.0 \
    [--dry-run | --upload] [--base-url URL] [--json]
```

Parsing, unit handling and payload building run offline. Only `--upload`
contacts a server: it needs the API root in `--base-url` or
`$TCKDB_BASE_URL` and an API key in `$TCKDB_API_KEY`. `--dry-run` builds the
payloads and their idempotency keys without sending anything.

## Species identity

CHEMKIN names carry no structure, so every species must be mapped to a SMILES,
charge and multiplicity. The sources, in order:

1. a CSV map (`--species-map`, `name,smiles[,charge,multiplicity]`);
2. an RMG `species_dictionary.txt` (`--species-dict`), read through RDKit;
3. `! SMILES=...` (and `MULT=...`) comments on the SPECIES declarations;
4. a built-in table for the bath gases AR, HE, NE and N2.

A name no source resolves is an error, and nothing is uploaded. The adapter
never guesses a structure.

## Python API

The stages are importable on their own: `parse_mechanism`,
`parse_thermo_file`, `parse_transport_file`, `normalize_mechanism`,
`IdentityResolver` and `build_all_payloads` from `tckdb_chemkin`, and
`upload_payloads` from `tckdb_chemkin.uploader`.

## Links

- Documentation: https://tckdb.github.io/TCKDB/
- Source: https://github.com/TCKDB/TCKDB/tree/main/clients/python/adapters/chemkin
- Changelog: https://github.com/TCKDB/TCKDB/blob/main/CHANGELOG.md
- License: MIT
