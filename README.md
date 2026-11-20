# TCKDB

<p align="center">
  <img src="docs/assets/tckdb-wordmark-cropped.png" alt="TCKDB wordmark" width="760">
</p>

TCKDB (Theoretical Chemical Kinetics Database) stores computed thermochemistry
and kinetics together with the calculations behind each number: the geometry,
the level of theory, the program and version, and the workflow that ran it.
You run it yourself, on a laptop or on a server for your group, and talk to it
over an HTTP API or the web interface.

**Documentation:** <https://tckdb.github.io/TCKDB/>

## What it does

- **Chemical identity.** Species, reactions and transition states, each with a
  specific form (stereochemistry, electronic state, isotopes) kept apart from
  the graph that identifies it.
- **Calculations.** Optimisations, frequencies, single points, scans, IRCs and
  path searches (NEB, GSM), with their parameters, input and output geometries,
  and the raw output files.
- **Scientific results.** Statistical mechanics, thermochemistry (NASA
  polynomials, H, S, Cp), rate coefficients (Arrhenius, Chebyshev, PLOG,
  falloff), transport and pressure-dependent networks.
- **Provenance.** Level of theory, program release, workflow tool and
  literature for every record.
- **Review.** Records start unreviewed. Curators approve or reject them, attach
  findings, and publish citable dataset releases.
- **Selection.** For a species or reaction, TCKDB lists every candidate value
  with its evidence and lets you choose by explicit rules. It never ranks
  methods for you.
- **Checks.** Consistency checks (Gibbs energy, Kirchhoff, Hess's law and
  others) compare related records and report disagreements as advisory
  findings.
- **Export.** CHEMKIN mechanisms and contribution bundles.
- **Access.** Anonymous reads by stable public IDs (`spc_…`, `rxn_…`), uploads
  with an API key, a Python client and a web interface.

## Run it

You need Git and Docker with Compose. TCKDB itself comes as published container images.

```bash
git clone https://github.com/TCKDB/TCKDB.git
cd TCKDB
backend/scripts/ops/tckdb_make_env.sh     # writes the settings, generates every password
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost up -d --wait
```

`make selfhost` runs the last two steps. The script asks five questions: the
deployment mode, the public URL, the proxy header (if any), and the admin's
name and email. It prints the admin password once.

When the stack is up:

- Web interface: <http://127.0.0.1:8011>
- API: <http://127.0.0.1:8010/api/v1> (`/health` returns `{"status":"ok"}`)

Create an API key for uploads:

```bash
TCKDB_BASE_URL=http://127.0.0.1:8010/api/v1 backend/scripts/tckdb_auth.sh login-create-key
```

Check the deployment with `backend/scripts/check_selfhosted_deployment.sh`.

Both ports listen on 127.0.0.1 only. To reach TCKDB from other machines, put a
reverse proxy or a tunnel in front of port 8010; the
[hosting guide](https://tckdb.github.io/TCKDB/deployment/) covers nginx,
Caddy, Cloudflare Tunnel and Tailscale.

Raw output files go to an S3-compatible store. The Compose file runs
SeaweedFS; to use AWS S3, Google Cloud Storage, MinIO or another S3 service
instead, set `S3_ENDPOINT_URL` and the keys in `.env.selfhosted`.

## Query it

Reads need no account.

```bash
# Species by SMILES
curl -G http://127.0.0.1:8010/api/v1/scientific/species/search \
    --data-urlencode "smiles=C"

# Thermochemistry for ethanol
curl -G http://127.0.0.1:8010/api/v1/scientific/thermo/search \
    --data-urlencode "smiles=CCO"

# Rate coefficients for CH4 + OH -> CH3 + H2O
curl -G http://127.0.0.1:8010/api/v1/scientific/kinetics/search \
    --data-urlencode "reactants=C" --data-urlencode "reactants=[OH]" \
    --data-urlencode "products=[CH3]" --data-urlencode "products=O"
```

[`examples/clients/explore_tckdb.ipynb`](examples/clients/explore_tckdb.ipynb)
is a read-only tour of an instance: it pulls thermochemistry, evaluates the
NASA polynomials and compares them with NIST-JANAF. The
[query cookbook](https://tckdb.github.io/TCKDB/guides/scientific_query_cookbook/)
has more.

## Upload to it

Uploads are JSON payloads sent with an `X-API-Key` header. The Python client
builds and sends them:

```bash
pip install "git+https://github.com/TCKDB/TCKDB.git#subdirectory=schemas/python/tckdb-schemas"
pip install "git+https://github.com/TCKDB/TCKDB.git#subdirectory=clients/python"
```

Adapters turn program and workflow output into uploads: ARC
([tckdb-adapters](https://github.com/calvinp0/tckdb-adapters)), CHEMKIN
mechanisms and QCSchema results (in [`clients/python/adapters`](clients/python/adapters)).
Any tool that can POST JSON can upload; see
[Upload](https://tckdb.github.io/TCKDB/start/uploads/) and
[Depositing](https://tckdb.github.io/TCKDB/guides/depositing_a_thermo_record/).

## Develop it

To work on TCKDB itself, run the API from a conda environment instead of the
image:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
mamba env create -n tckdb_env -f backend/environment.yml
conda activate tckdb_env
pip install -e schemas/python/tckdb-schemas
pip install -e "backend[dev]"
make up      # Postgres with RDKit and SeaweedFS, then migrations
make api     # API on 127.0.0.1:8010
```

`make help` lists the other targets, including the test suites. See
[Install locally](https://tckdb.github.io/TCKDB/start/getting-started/).

## Status

TCKDB is at version 0.2. The schema and the API can still change before 1.0.

## Citing and license

The code is MIT licensed ([`LICENSE`](LICENSE)). Scientific data in TCKDB is
CC BY 4.0 ([`LICENSE-DATA`](LICENSE-DATA)): cite the dataset release you used,
by its tag and manifest checksum. To cite the software, use
[`CITATION.cff`](CITATION.cff).

Copyright (c) 2026 Calvin Pieters, Alon Grinberg Dana, and Technion – Israel
Institute of Technology.
