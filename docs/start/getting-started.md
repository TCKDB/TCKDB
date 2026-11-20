# Install locally

The shortest path from a fresh clone to a TCKDB API running from source, for
working on the code.

Just want to run TCKDB, for yourself or a group? [Self-host](../deployment/README.md)
does it from published images in three commands, with no Conda.

<figure class="tk-figure">
--8<-- "assets/dev-setup-flow.svg"
</figure>

## What you need

- Git.
- Docker Engine or Docker Desktop with Compose.
- Conda or Mamba. The environment takes RDKit from conda-forge.
- A Unix-like shell: Linux, macOS, or WSL on Windows.

## Set up

```bash
git clone https://github.com/TCKDB/TCKDB.git
cd TCKDB

cp .env.example .env
cp backend/.env.example backend/.env

mamba env create -n tckdb_env -f backend/environment.yml
conda activate tckdb_env
pip install -e schemas/python/tckdb-schemas
cd backend && pip install -e ".[dev]" && cd ..

make up
make api
```

`make up` starts Postgres and SeaweedFS in Docker and runs the migrations.
`make api` runs the API in the foreground on 127.0.0.1:8010.

In another terminal:

```bash
make doctor
curl http://127.0.0.1:8010/api/v1/health
```

Expected response:

```json
{"status":"ok"}
```

TCKDB is running. Until you load or upload records, queries return empty
`records` arrays.

## Next

Load the small dataset in [Demo data](demo-data.md), then try
[Querying](querying.md).

Details, administrator accounts and API keys are on
[Run from source](../deployment/local-v0.md).
