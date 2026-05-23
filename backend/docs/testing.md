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

| Script                                   | Default pytest call                                                       |
|------------------------------------------|---------------------------------------------------------------------------|
| [`test-fast.sh`](../scripts/test-fast.sh)             | `pytest -q -x --tb=short "$@"`                                |
| [`test-scientific.sh`](../scripts/test-scientific.sh) | `pytest -q tests/api/scientific/ tests/services/scientific_read/ "$@"`  |
| [`test-api.sh`](../scripts/test-api.sh)               | `pytest -q tests/api/ "$@"`                                   |
| [`test-full.sh`](../scripts/test-full.sh)             | `pytest -q tests/ "$@"`                                       |
| [`test-profile.sh`](../scripts/test-profile.sh)       | `pytest -q --durations=50 [<path>|tests/]`                    |

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
Test isolation in this repo is per-test transaction rollback (see
[`tests/conftest.py`](../tests/conftest.py)); intermittent failures
are usually fixture-ordering or shared-state bugs surfacing.

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

## Test policy

- Full suite is a **gate**, not the edit loop. Don't run Tier 4 on
  every save.
- Land Tier 1 green at minimum before pushing. Land Tier 3 green
  before opening a PR. Tier 4 is required before merging.
- The CI configuration (TBD) should run Tier 4. Local Tier 4 is the
  pre-push insurance.

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
- The rate-limit middleware in-memory store is disabled per test
  (already the case via `_disable_rate_limit_by_default` autouse
  fixture) so two workers do not poison each other's bucket counters
  when they happen to land on the same IP.
- The MinIO/artifact-storage tests that skip on `not _minio_available()`
  do the right thing under parallel workers.

Until that work is done, the scripts run pytest single-threaded.
