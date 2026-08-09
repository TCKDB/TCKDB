# Backend test ladder

The backend test suite has grown past the point where one command fits
every situation. This doc defines five intentional **tiers** so the
right scope is one short command away — and so "I just ran the tests"
means the same thing across the team.

The full repo suite is a **gate, not the edit loop**. Choose the
narrowest tier that proves the change you're making.

## Tiers at a glance

| Tier | Purpose                                       | Wall time (rough) | Tool                          |
|------|-----------------------------------------------|-------------------|-------------------------------|
| 0    | One test or one file — debugging              | seconds           | `test-fast.sh <path>`         |
| 1    | One affected module / feature                 | < 1 min if poss.  | `test-fast.sh <dir>` (`-k`)   |
| 2    | Scientific read + service confidence          | 1–3 min           | `test-scientific.sh`          |
| 3    | Full API surface regression gate              | several minutes   | `test-api.sh`                 |
| 4    | Full backend suite — pre-push / release       | longest, reliable | `test-full.sh`                |

Tier 0 and Tier 1 use the same script with different arguments — the
distinction is intent (single failure debug vs. validating a focused
change), not a different command.

## Scripts

All scripts live under [`backend/scripts/`](../scripts) and `cd` to the
backend directory before invoking pytest. They forward extra arguments
through, so `-k`, `-x`, `--maxfail=...`, and named-test selectors all
work as you'd expect:

| Script                                   | Default pytest call                                                                |
|------------------------------------------|------------------------------------------------------------------------------------|
| [`test-fast.sh`](../scripts/test-fast.sh)             | `pytest -v -x --tb=short "$@"`                                         |
| [`test-scientific.sh`](../scripts/test-scientific.sh) | `pytest -q --tb=short tests/api/scientific/ tests/services/scientific_read/ "$@"` |
| [`test-api.sh`](../scripts/test-api.sh)               | `pytest -q --tb=short tests/api/ "$@"`                                 |
| [`test-full.sh`](../scripts/test-full.sh)             | `pytest -q --tb=short tests/ "$@"`                                     |
| [`test-profile.sh`](../scripts/test-profile.sh)       | `pytest -v --durations=50 [<path>|tests/]`                             |

Tier 0/1 (`test-fast.sh`) keeps `-v` so each test name prints live while
you iterate. Tiers 2/3/4 use `-q --tb=short` to keep CI and pre-push
logs scannable — pass `-v` or `-vv` through `ARGS=` when debugging.

Make targets wrap each script and use the `tckdb_env` conda
environment:

```bash
make test-fast       ARGS="tests/api/test_api_health.py"
make test-scientific
make test-api        ARGS="-x"
make test-full
make test-profile    ARGS="tests/api/scientific/"
```

You can equivalently call the scripts directly. They do NOT hardcode
`conda run -n tckdb_env` so they compose with any environment manager
the caller already has active:

```bash
# Already inside the conda env (or another env with the deps installed):
bash backend/scripts/test-fast.sh tests/api/test_api_health.py

# Wrap explicitly when invoking from a fresh shell:
conda run -n tckdb_env bash backend/scripts/test-fast.sh tests/api/test_api_health.py
```

## When to run each tier

### Tier 0 — one test or one file

Use when you are actively debugging a single failure or iterating on a
single test.

```bash
make test-fast ARGS="tests/api/test_api_health.py::test_readyz_ready"
make test-fast ARGS="tests/api/test_request_id.py -k oversized"
```

`-x` stops at the first failure and `--tb=short` keeps the traceback
readable. Add `-vv` or `-s` as needed; arguments are forwarded.

### Tier 1 — affected module

Use after the focused fix passes, to confirm the surrounding module
or feature still passes.

```bash
make test-fast ARGS="tests/api/scientific/test_api_scientific_artifacts.py"
make test-fast ARGS="tests/services/test_calculation_parameter_extraction.py"
```

### Tier 2 — scientific surface

Run before committing changes to anything under
`app/api/routes/scientific/`, `app/services/scientific_read/`, or any
read-side scientific schema.

```bash
make test-scientific
```

### Tier 3 — full API surface

Run before committing changes to a route, middleware, error handler,
auth dependency, or anything that affects the HTTP surface broadly.
This is the cross-surface regression gate.

```bash
make test-api
```

### Tier 4 — full backend suite

Run before `git push`, before opening a PR, and before tagging a
release. It includes parsers, importers, workflows, services, and
every API test together.

```bash
make test-full
```

## Debugging a single failing test

Re-run the one test from the failure output. Add verbosity and
disable capture once you need to read what's happening:

```bash
make test-fast ARGS="path/to/test_file.py::TestClass::test_case -vv -s"
```

If a test is flaky, run it three times in a row before concluding.

Test isolation here is **two-tier**, and knowing which tier a test is in
matters when diagnosing cross-file failures (see
[`tests/conftest.py`](../tests/conftest.py)):

- `db_session` / `db_conn` / `client` roll back per test. Rows written
  through these never outlive the test.
- The session-scoped `db_engine` fixture does **not**. Test files that
  use it with `with session.begin():` commit, so the shared test
  database genuinely accumulates committed rows for the whole pytest
  session. Those files must delete what they wrote, in a fixture
  `finally` — see "Isolation contract" below.

All of `tests/workflows/` is now in the first tier and is held there by a
tripwire in [`tests/workflows/conftest.py`](../tests/workflows/conftest.py)
that fails any test in that tree which commits. It used to be in the second:
running `test_network_pdep_upload.py` before `test_computed_reaction_upload.py`
left fifteen committed `type=irc` calculations behind and five tests that
locate "the" IRC calculation with an unqualified query then asserted against
the wrong row — deterministically, under `-p no:randomly`, with no seed
involved. The same residue accounted for every one of the 75 failures and 47
errors that `pytest tests/workflows/ tests/services/` produced; that
combination is green now.

Two consequences worth remembering. Committed rows from tier two are
visible to every later test file in the same run. And rollback does not
undo everything even in tier one: PostgreSQL sequences are
non-transactional, so a `setval` (as `restore_archive` performs when
repairing primary-key sequences) survives a rollback and leaks into the
rest of the session — see
[`tests/services/archive/conftest.py`](../tests/services/archive/conftest.py)
for the containment fixture and the failure it prevents.

Because each CI gate job gets a fresh database, this class of bug is
invisible in CI and only reproduces when several files share one local
database. Intermittent or combination-only failures are usually
fixture-ordering or shared-state bugs of exactly this kind.

### Recovering from a local test-database port conflict

If pytest reports `psycopg.OperationalError` while connecting to
`127.0.0.1:5432`, another PostgreSQL instance may already own the default
port. Select an available backend test port and update the ignored local
environment file with:

```bash
conda run -n tckdb_env python backend/scripts/dev/ensure_test_db_port.py --apply
```

Then restart the backend database service so Compose uses the selected
port. This recovery procedure is backend tooling and applies equally to
local development environments and automation.

## Profiling slow tests

`test-profile.sh` runs the requested subset with `--durations=50` so
the 50 slowest tests print at the end. Narrow the target when the
full suite is too slow to iterate on:

```bash
make test-profile ARGS="tests/api/scientific/"
make test-profile ARGS="tests/api/ -k upload"
make test-profile                              # whole tests/ tree
```

There is **no slow-test budget today**. When that lands, add a
`@pytest.mark.slow` marker (declared in `pytest.ini`) to anything
above the threshold and have CI deselect or quarantine it. Until
then, treat `make test-profile` output as informational.

## Concurrent runs and shared `DB_TEST_NAME`

Do not run multiple pytest processes against the same explicit
`DB_TEST_NAME` on the same Postgres host. The session fixture in
[`tests/conftest.py`](../tests/conftest.py) recreates the named test
database during setup and terminates active connections on it — a
second process pointed at the same name will see its DB dropped out
from under it mid-run, with confusing `OperationalError`s as the
visible symptom.

The session fixture derives a test-DB name with the following
precedence (see `_resolve_test_db_name` in
[`tests/conftest.py`](../tests/conftest.py)):

1. **Explicit `DB_TEST_NAME`** — used verbatim. Backward-compatible
   with existing CI configs that pin a job-specific name. Explicit
   names are **single-tenant**: only one pytest process at a time may
   use them. For CI runners that share one Postgres host, set
   `DB_TEST_NAME` per job (e.g. include the runner / job id) so
   parallel jobs do not collide.
2. **`PYTEST_XDIST_WORKER`** — when pytest-xdist is invoked, each
   worker exports its id (`gw0`, `gw1`, …) and the fixture maps it to
   `tckdb_test_<worker>` (e.g. `tckdb_test_gw0`, `tckdb_test_gw1`).
   Worker ids are sanitized to safe identifier characters, so each
   worker owns its own database and the drop-and-recreate sequence
   cannot race.
3. **Fallback** — `tckdb_test_<pid>` so two ad-hoc pytest processes
   on one host (e.g. two terminals running `make test-fast`) never
   share a database, even without xdist.

The resolved name is exported back into `os.environ["DB_TEST_NAME"]`
so subprocess-based tests (e.g. the contribution-bundle CLI smoke
test) inherit the same database. xdist is **not** wired up by
default; this is forward-looking infrastructure that activates the
moment a caller sets `PYTEST_XDIST_WORKER`.

### Test-database cleanup and the startup sweep

Because the fallback name embeds the pid, **no name is ever reused** —
so any run that fails to drop its database leaks it permanently. Two
mechanisms keep that from accumulating:

1. **Fixture teardown.** `db_engine` performs database creation *and*
   the `alembic upgrade` subprocess **inside** its `try`, so the
   `finally` that drops the database covers every in-process failure
   path — a failed migration, a failed `create_engine`, a collection
   error or `KeyboardInterrupt` thrown into the fixture — not just a
   clean session exit.
2. **Startup sweep.** Nothing in Python can run after `SIGKILL`, an
   OOM kill, or a power loss, so `_sweep_stale_test_databases` runs
   once at session start to reclaim that residue. Every database the
   fixture creates is stamped with a `COMMENT ON DATABASE` recording
   the creating host and pid; the sweep drops a database **only** when
   all of the following hold:

   - it carries this harness's marker (databases created by anything
     else — including orphans that predate the marker — are never
     touched);
   - the marker's host is this host and its pid is no longer running
     (this is what stops a concurrently-starting pytest run from
     having its freshly created database swept away before it
     connects);
   - `pg_stat_activity` reports no backend attached;
   - the name matches the isolated-test-database pattern and is not
     this session's own database.

   The `DROP` is issued **without** `pg_terminate_backend`, so if a
   connection appears in the race window Postgres refuses and the
   sweep moves on. The sweep is best-effort and never fails a run.
   Set `TCKDB_TEST_DB_SWEEP=0` to disable it.

Regression coverage for both lives in
[`tests/test_db_harness_lifecycle.py`](../tests/test_db_harness_lifecycle.py).

If you need to audit what is on the server:

```bash
psql -h 127.0.0.1 -U tckdb -d postgres -c "
  SELECT d.datname,
         pg_size_pretty(pg_database_size(d.datname)) AS size,
         shobj_description(d.oid, 'pg_database')     AS marker
  FROM pg_database d
  WHERE d.datname LIKE 'tckdb\_test%'
  ORDER BY d.oid;"
```

## Flaky / repro handling

- Reproduce in isolation first (Tier 0/1). If it passes alone but
  fails in the full suite, it's a test-isolation bug, not a unit
  bug — bisect by running an ordered subset of files.
- The pytest fixture creates a fresh `tckdb_test` database via
  `alembic upgrade head` once per session (see
  [`tests/conftest.py`](../tests/conftest.py)) and rolls each test
  back inside its own transaction. Tests that commit raw bytes to
  external storage (MinIO) skip themselves when MinIO is not
  reachable — that's expected on a workstation without the dev
  container running.

### Isolation contract: what a test may leave behind

The database is created once per pytest process and shared by every test
in it. **Nothing a test writes may outlive it**, because the next test
cannot know what ran before it. Three fixtures deliver that, and a test
should use one of them rather than opening its own connection:

| Fixture | What you get | Use it when |
|---|---|---|
| `db_conn` | A `Connection` inside a transaction rolled back at teardown, with a SAVEPOINT already open so `Session(db_conn)` nests instead of joining | Anything that persists rows — this is the default, and what all of `tests/workflows/` uses |
| `client` / `db_session` | The same, plus a `TestClient` whose `get_db`/`get_write_db` are bound to it | Anything going through a route |
| `db_engine` | The raw session-scoped `Engine` | Only when a test genuinely needs **two concurrent transactions** — a race, an advisory lock, a `READ COMMITTED` visibility check |

`Session(db_conn)` keeps working with the ordinary
`with Session(...) as s, s.begin(): ...` idiom: because `db_conn` is inside a
SAVEPOINT, SQLAlchemy's default `join_transaction_mode` resolves to
`create_savepoint`, so the session's commit stays inside the per-test
transaction and its rollback (including the implicit one when a test asserts
that an upload raises) undoes only its own work.

A test that takes `db_engine` **commits for real and must clean up after
itself**, in a fixture `finally` so it also cleans up when the test fails.
`tests/services/test_concurrent_deposit_isolation.py` and
`tests/services/test_record_review_write_isolation.py` show the shape.

The one thing a test cannot clean up is an append-only table.
`record_review_event` and `scientific_record_supersession` carry a
`BEFORE UPDATE OR DELETE` trigger (`tckdb_reject_mutation`, revision
`c6f2a9d4e7b1`), and the scientific tables additionally refuse `TRUNCATE`, so
the rows the record-review race tests commit cannot be removed by any
test-level code. Deleting them would require the harness to disable a
production integrity guard — which would leave that guarantee unenforced in
the very suite meant to enforce it. **The harness answer is the per-run
disposable database**: `_recreate_test_database` at session start and
`_drop_test_database` at session end, plus the startup sweep above. Tests in
that position must confine themselves to a reserved, non-colliding id band
(as `test_record_review_write_isolation.py` does) so the residue can never be
mistaken for another test's data, and no test may make an *absolute*
unqualified count over those tables — before/after deltas only.

## Test policy

- Full suite is a **gate**, not the edit loop. Don't run Tier 4 on
  every save.
- Land Tier 1 green at minimum before pushing. Land Tier 3 green
  before opening a PR. Tier 4 is required before merging.
- The initial backend CI gate runs two independent jobs in parallel: the API
  job runs Tier 3 with `tests/api/scientific/` ignored, while the scientific
  job runs the scientific API directory plus `tests/services/scientific_read/`
  through Tier 2. Together they cover every API test once and every
  scientific-read service test once. Local Tier 4 remains the pre-push and
  pre-merge insurance until full-suite CI runtime is known.

## CI gate

GitHub Actions runs the backend gate in
[`../../.github/workflows/backend-ci.yml`](../../.github/workflows/backend-ci.yml)
for pull requests and pushes that touch backend code, backend tests,
the backend package, the shared `tckdb-schemas` package, or the CI
workflow itself.

The workflow uses the same RDKit-enabled Postgres image as local
development:

```text
informaticsmatters/rdkit-cartridge-debian:Release_2025_03_3
```

Plain Postgres is not sufficient because the Alembic chain enables the
`rdkit` extension and the schema includes RDKit cartridge types and
indexes.

The CI job creates the `tckdb_env` conda environment from
[`../environment.yml`](../environment.yml), then installs the shared
schema package and backend package in editable mode:

```bash
python -m pip install -e schemas/python/tckdb-schemas
python -m pip install -e "backend[dev]"
```

The gate runs:

- shell/doc hygiene checks (`git diff --check`, `bash -n` for the test
  ladder scripts, and `make help`)
- `alembic upgrade head`, `alembic heads`, and `alembic current`
  against the RDKit Postgres service
- the OpenAPI golden snapshot test at
  [`tests/api/test_openapi_snapshot.py`](../tests/api/test_openapi_snapshot.py)
- the API gate via [`../scripts/test-api.sh`](../scripts/test-api.sh)
- the scientific read/service gate via
  [`../scripts/test-scientific.sh`](../scripts/test-scientific.sh)

The API gate ignores both `tests/api/scientific/` (covered by the scientific
job) and `tests/api/test_openapi_snapshot.py` (run once by its dedicated
golden-snapshot step). The final `Backend CI` job is a stable aggregate status
check: it runs even after an upstream failure or cancellation and fails unless
the matrix result is `success`, while leaving both detailed gate checks visible.

Each CI job owns an isolated Postgres service and MinIO service. Its
`DB_TEST_NAME` and `S3_BUCKET` include both the GitHub run id/attempt and the
job role, so concurrent jobs and workflow runs do not share test resources.
The workflow does not enable pytest-xdist: an explicit `DB_TEST_NAME` takes
precedence over `PYTEST_XDIST_WORKER`, so adding `-n` would still make workers
race on one recreated database. The scientific test factories also need
worker-aware isolation before xdist can safely be added.

The full Tier 4 suite is intentionally not part of this v0 PR workflow.
Keep running `make test-full` locally before push/merge until a
separate full-suite or nightly CI gate is added.

## OpenAPI golden snapshot

[`tests/api/test_openapi_snapshot.py`](../tests/api/test_openapi_snapshot.py)
freezes the full normalized `/openapi.json` schema in a golden file at
[`tests/api/golden/openapi.json`](../tests/api/golden/openapi.json).
Any change to a path, request/response schema, parameter, enum, or
operation id surfaces as a diff against the golden file — accidental
contract drift fails the test loudly, intentional changes show up
field-level in the PR diff.

The existing path-presence checks in
[`tests/api/scientific/test_api_openapi.py`](../tests/api/scientific/test_api_openapi.py)
only verify that a handful of routes exist; they cannot catch field
renames, response-shape changes, or enum-value drift on routes that
*are* present. The snapshot closes that gap.

**Update workflow.** When you have intentionally changed a route or
schema, regenerate the golden. Either of these works:

```bash
make update-openapi-golden
```

```bash
conda run -n tckdb_env bash backend/scripts/update-openapi-golden.sh
```

Both set `UPDATE_OPENAPI_GOLDEN=1` and rerun the snapshot test, which
overwrites the golden file from the live `/openapi.json` instead of
asserting against it. The Make target additionally forwards extra
pytest args via `ARGS=...` (e.g. `make update-openapi-golden ARGS="-x"`).

The underlying command is still available if you prefer it:

```bash
UPDATE_OPENAPI_GOLDEN=1 conda run -n tckdb_env pytest \
    tests/api/test_openapi_snapshot.py
```

After regenerating, review the diff before committing:

```bash
git diff backend/tests/api/golden/openapi.json
```

The diff is the contract change — treat it as part of the review
surface, not as boilerplate to wave through.

**Normalization.** The helper sorts dict keys recursively and dumps
with `indent=2`, `sort_keys=True`, and a trailing newline. Arrays
are intentionally left in generation order — `required`, `allOf` /
`oneOf`, `enum`, and path parameter lists all have semantic order.

## Pytest markers (follow-up, not in this slice)

The repo currently uses only standard pytest markers (`skipif`,
`filterwarnings`). No custom markers are declared in `pytest.ini`.

If/when a marker rollout makes sense, register them in
`backend/pytest.ini` under `[pytest]` `markers = ...` and tag tests
incrementally. Candidate markers, in approximate order of likely
usefulness:

```
slow          # exceeds a stated wall-time budget
integration   # needs a running DB / MinIO / external service
external      # hits a real third-party API (CCCBDB, DOI, ISBN)
smoke         # opt-in liveness/sanity tests (already a directory)
```

Do NOT tag hundreds of files in one PR. Tag a single suite at a
time and validate the deselection behavior end-to-end.

## Parallelization (follow-up, not in this slice)

[`pytest-xdist`](https://pypi.org/project/pytest-xdist/) can cut the
wall time of the Tier 3/4 suites substantially once test isolation
under parallel workers is proven safe.

Things to verify before enabling xdist by default:

- The test DB is named per worker (`tckdb_test_gw0`, `tckdb_test_gw1`,
  ...) so workers do not race on the same `alembic upgrade head` /
  drop-and-recreate sequence in [`tests/conftest.py`](../tests/conftest.py).
  This requires omitting `DB_TEST_NAME` (or making it worker-specific),
  because explicit names intentionally take precedence over
  `PYTEST_XDIST_WORKER`.
- The rate-limit middleware in-memory store is disabled per test
  (already the case via `_disable_rate_limit_by_default` autouse
  fixture) so two workers do not poison each other's bucket counters
  when they happen to land on the same IP.
- The MinIO/artifact-storage tests that skip on `not _minio_available()`
  do the right thing under parallel workers, including worker-isolated object
  keys/buckets and subprocess inheritance.
- The process-global scientific-read factory counters use worker-distinct
  prefixes or another isolation mechanism.

Until that work is done, the scripts run pytest single-threaded.
