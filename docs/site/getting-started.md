# Getting Started

This page is the shortest path from a fresh clone to a running local
TCKDB API.

## Prerequisites

- Git.
- Docker Desktop or Docker Engine with Compose.
- Conda or Mamba. The recommended environment uses conda-forge RDKit.
- A Unix-like shell: Linux, macOS, or WSL on Windows.

## Local Setup

```bash
git clone <repo-url> tckdb
cd tckdb

cp .env.example .env
cp backend/.env.example backend/.env

mamba env create -n tckdb_env -f backend/environment.yml
conda activate tckdb_env
cd backend && pip install -e ".[dev]" && cd ..

make up
make api
```

In another terminal:

```bash
make doctor
curl http://127.0.0.1:8010/api/v1/health
```

Expected health response:

```json
{"status":"ok"}
```

At this point TCKDB is running. If you have not loaded or uploaded
scientific records yet, query endpoints may return empty `records`
arrays. That is normal for an empty database.

## Next Step

Load the small illustrative dataset in [Demo Data](demo-data.md), then
try [Querying](querying.md).

For a longer local deployment guide, see
[Local Development](../deployment/local-v0.md).
