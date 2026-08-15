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
| 3    | Everything the other two gates exclude        | several minutes   | `test-rest.sh`                |
| 4    | Full backend suite — pre-push / release       | ~6 min (`-n 8`)   | `test-full.sh`                |

Tier 0 and Tier 1 use the same script with different arguments — the
distinction is intent (single failure debug vs. validating a focused
change), not a different command.

Together the two Tier 3 gates and `test-scientific.sh` **cover
`backend/tests/` completely**, and `test-rest.sh` is defined as the
complement of the other two rather than as a list of directories, so a
directory added to `tests/` joins a gate the day it is created.

Covering is the guarantee; being disjoint is not. Run bare,
`test-api.sh` selects all of `tests/api/` including
`tests/api/scientific/`, which `test-scientific.sh` also selects — CI
passes `--ignore=tests/api/scientific/` so each test runs once per PR,
but locally the two overlap. Nothing depends on the overlap being
absent; a test running twice costs seconds, a test running zero times
cost this repo several days.

The covering half is enforced, not asserted — see
[`tests/scripts/test_gate_coverage.py`](../tests/scripts/test_gate_coverage.py),
which reads the scripts and `backend-ci.yml`, resolves what each job
selects and ignores, and fails if any test file is run by no required
CI job.

It is enforced because it silently stopped being true. Until August 2026
the two required PR jobs ran `tests/api/` and `tests/api/scientific/` +
`tests/services/scientific_read/` and nothing else, so 3,806 tests — more
than half the suite by count — gated no pull request. They ran nightly,
which meant a defect merged green and surfaced the next morning attached
to no PR; `tests/db/test_identifier_lengths.py` sat red on `main` for
days that way. Nothing in the scripts said so, which is the part worth
remembering: `test-api.sh` read as "the API gate" while being "some of
the API".

## Scripts

All scripts live under [`backend/scripts/`](../scripts) and `cd` to the
backend directory before invoking pytest. They forward extra arguments
through, so `-k`, `-x`, `--maxfail=...`, and named-test selectors all
work as you'd expect:

| Script                                   | Default pytest call                                                                | Workers |
|------------------------------------------|------------------------------------------------------------------------------------|---------|
| [`test-fast.sh`](../scripts/test-fast.sh)             | `pytest -v -x --tb=short "$@"`                                         | 0 |
| [`test-scientific.sh`](../scripts/test-scientific.sh) | `pytest -q --tb=short tests/api/scientific/ tests/services/scientific_read/ "$@"` | 8 |
| [`test-api.sh`](../scripts/test-api.sh)               | `pytest -q --tb=short tests/api/ "$@"`                                 | 8 |
| [`test-rest.sh`](../scripts/test-rest.sh)             | `pytest -q --tb=short tests/ --ignore=tests/api --ignore=tests/services/scientific_read "$@"` | 8 |
| [`test-full.sh`](../scripts/test-full.sh)             | `pytest -q --tb=short tests/ "$@"`                                     | 8 |
| [`test-profile.sh`](../scripts/test-profile.sh)       | `pytest -v --durations=50 [<path>|tests/]`                             | 0 |

Every script additionally passes `--randomly-seed=424242` and `-n <workers>`,
via [`scripts/lib/pytest_run_args.sh`](../scripts/lib/pytest_run_args.sh).
See [Pinned order and parallel workers](#pinned-order-and-parallel-workers)
for why, and how to override either.

Tier 0/1 (`test-fast.sh`) keeps `-v` so each test name prints live while
you iterate. Tiers 2/3/4 use `-q --tb=short` to keep CI and pre-push
logs scannable — pass `-v` or `-vv` through `ARGS=` when debugging.

Make targets wrap each script and use the `tckdb_env` conda
environment:

```bash
make test-fast       ARGS="tests/api/test_api_health.py"
make test-scientific
make test-api        ARGS="-x"
make test-rest
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

Green here means the HTTP surface is intact. It says nothing about the
services, workflows, db constraints, schemas, parsers or importers
underneath it — that is the next section, and the distinction is why
this one is not called "the backend gate".

### Tier 3 — everything else

Run before committing changes to `app/services/` (outside
`scientific_read`), `app/workflows/`, `app/db/`, `app/schemas/`,
`app/importers/`, `app/parsers/`, `app/workers/` or `app/cli/` — which
is to say most of the backend.

```bash
make test-rest
```

It is the complement of the two gates above, not a list, so it needs no
maintenance when `tests/` grows. In practice these are the *cheap*
tests: 3,813 of them in 3m50s at four workers, against 1,998 scientific
tests in 4m21s on the same host.

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

Test isolation here has **two tiers**, and knowing which one a test is in
matters when diagnosing cross-file failures (see
[`tests/conftest.py`](../tests/conftest.py)):

- `db_session` / `db_conn` / `client` roll back per test. Rows written
  through these never outlive the test.
- The session-scoped `db_engine` fixture does **not**. A test that uses
  it with `with session.begin():` commits, so it must delete what it
  wrote, in a fixture `finally` — see "Isolation contract" below. The
  files still in that position are the `*_isolation.py` family, the
  upload-worker tests and the bundle-export CLI test; every one of them
  is there because it needs two genuinely concurrent transactions or a
  subprocess on its own connection, and every one of them cleans up.

The whole suite is now in the first tier, and is held there by the
`_refuse_committed_rows` tripwire in [`tests/conftest.py`](../tests/conftest.py),
which fails any test that commits without cleaning up. `tests/workflows/` was
the first tree moved (PR #111): running `test_network_pdep_upload.py` before
`test_computed_reaction_upload.py` left fifteen committed `type=irc`
calculations behind and five tests that locate "the" IRC calculation with an
unqualified query then asserted against the wrong row — deterministically,
under `-p no:randomly`, with no seed involved. That residue accounted for every
one of the 75 failures and 47 errors that
`pytest tests/workflows/ tests/services/` produced. ~50 more tests across 16
files in `tests/services/`, `tests/invariants/` and `tests/workers/` had the
same habit and were the bulk of the 69 failures the full suite produced at
`--randomly-seed=424242`; they are on `db_conn` now.

One consequence worth remembering: rollback does not undo everything.
PostgreSQL sequences are non-transactional, so a `setval` (as
`restore_archive` performs when repairing primary-key sequences) survives a
rollback and leaks into the
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

## Concurrent runs and `DB_TEST_NAME`

**Two pytest runs on one host no longer interfere, whatever you pass
as `DB_TEST_NAME`.** That was not true until 2026-08-11, and the way
it failed is worth knowing, because the symptom named nothing:

> Three agents worked in parallel on one box. One reported **2305
> errors** on a tree that, re-run alone, gave a clean pass at the same
> commit. Every error read `terminating connection due to
> administrator command` or `relation does not exist`. Nothing in the
> output said "another run is using your database".

Names were derived from `PYTEST_XDIST_WORKER` alone, so they were
unique *within* a run and identical *across* runs. Eight databases,
shared by everybody, each run dropping and recreating the others'
schemas mid-flight. CI escaped only because it sets `DB_TEST_NAME` per
`github.run_id`; no gate script did.

### The name

The session fixture (`_resolve_test_db_name` in
[`tests/conftest.py`](../tests/conftest.py)) builds:

```
<base>_<run token>[_<worker>]
```

- **base** — `DB_TEST_NAME` when set, otherwise `tckdb_test`. An
  explicit `DB_TEST_NAME` is a **label**, not the final name.
- **run token** — eight hex characters minted once per pytest run and
  exported as `TCKDB_TEST_RUN_TOKEN`. The xdist controller imports the
  root conftest before execnet spawns the workers, so all eight
  databases of one run share one token; if a future pytest stops
  importing it there, each worker mints its own, which is still
  correct (the token only ever has to differ *between* runs).
- **worker** — the sanitized `PYTEST_XDIST_WORKER` id. Asking for a
  specific database name *and* N workers is asking for N databases:
  when the explicit name won unconditionally, `-n` pointed every
  worker at one database and they raced to drop, recreate and then
  concurrently write it, reintroducing exactly the cross-test
  visibility the per-test rollback exists to prevent.

Over-long names have the **base** trimmed, never the suffix. Postgres
truncates identifiers silently, so trimming the tail would eat the run
token first — turning the cross-run guarantee off for precisely the
operators who pin long, descriptive job names — and would collapse
`…_gw10` and `…_gw11` onto one database.

The run token is why the explicit name can no longer be used verbatim.
Making only the *default* unique would have missed the collision shape
that actually happens: two runs copy-pasting the same
`DB_TEST_NAME=...` out of the same document. Set it for readability
(`DB_TEST_NAME=tckdb_test_analytics` makes your databases obvious in
`\l`); it is no longer load-bearing for isolation.

The resolved name is exported back into `os.environ["DB_TEST_NAME"]`
so subprocess-based tests (e.g. the contribution-bundle CLI smoke
test) inherit the same database. Re-resolving an already-resolved name
is idempotent — a second token is never stacked on.

The xdist controller process never creates a database: it collects but
does not execute tests, and `PYTEST_XDIST_WORKER` is unset there.

### The refusal

Random tokens make a collision vanishingly unlikely, and "unlikely" is
not the same as "reported". Before it drops anything,
`_recreate_test_database` refuses when either holds:

- a **live backend** is attached to the exact name. Within one run each
  worker owns a distinct name and creates it once, so a connection
  already there belongs to somebody else;
- the database carries an **ownership marker from a different run** on
  this host whose creator pid is still alive — a run that has created
  its database but not yet connected, which the backend check misses.

The refusal raises `ForeignTestDatabaseError` naming both run tokens
and saying what to do. An orphan from a *dead* run is reclaimed, not
refused: leaks are expected, and a permanent blocker would be worse
than the leak.

An unmarked database is not refused — it was not created by this
harness, its name has already been validated against the
isolated-test-database pattern, and dropping it is the documented
behaviour of an explicit `DB_TEST_NAME`.

### The connections

Run-unique names removed the collision over a *name* and left the
collision over *connections* with nothing to name it.

One 8-worker run peaks at about **35 client backends** (~4 per worker,
plus the admin engine and the `alembic upgrade head` subprocess).
PostgreSQL's default `max_connections` is 100, so three concurrent runs
sit at the limit and four are past it. What that looks like from inside
a run is tens to hundreds of failures reading `connection is bad`,
`server closed the connection unexpectedly` and `sorry, too many
clients already`, spread across whichever tests happened to be running:

> One agent measured 59 connections already in use before its run
> started and reported **21 failures / 243 errors**. Another saw
> **87 / 1 / 23 / 0** failures across four runs of the same commit,
> every one of which passed serially. Both initially read it as a
> defect in `main`; one nearly published a false baseline from it.

Two mechanisms now make that legible, and one makes it rarer.

- **Refusal at session start.** Before anything is created, the
  controller reads `max_connections`, the live *client backend* count
  (not `count(*)` — the checkpointer, walwriter and autovacuum launcher
  appear in `pg_stat_activity` and occupy no slot) and every
  `tckdb_test%` database's ownership marker. If the free headroom is
  below `workers x 5 + 6`, the session stops with a
  `ConcurrentTestRunError` naming the other runs, their pids and their
  connection counts. It derives from `pytest.UsageError`, so it prints
  as one `ERROR:` line with exit code 4 rather than as an
  `INTERNALERROR>` traceback.
- **A terminal-summary banner.** The refusal cannot catch the other run
  ramping up *after* this one started, which is the more common shape.
  If any foreign run was seen at either end of the session, the summary
  names it and says that connection-shaped failures are that, not a
  regression.
- **A bigger ceiling.** `docker-compose.yml` starts Postgres with
  `max_connections=${DB_MAX_CONNECTIONS:-200}`. A limit is not an
  allocation — the cost is per live connection — so this is safe on the
  Raspberry Pi deployment that shares the file. **The container must be
  recreated, not restarted,** for a change to apply:
  `docker compose up -d --force-recreate db`.

Knobs, both read by [`tests/conftest.py`](../tests/conftest.py):

| Variable | Effect |
|---|---|
| `TCKDB_TEST_CONCURRENCY_CHECK=0` | Disable the check entirely. |
| `TCKDB_TEST_MIN_HEADROOM=<n>` | Override the computed requirement. Setting it high is how the refusal is exercised on a quiet server. |

Covered by
[`tests/test_concurrent_run_detection.py`](../tests/test_concurrent_run_detection.py),
which asserts the attribution arithmetic, that the refusal fires and
that it reaches the terminal as one readable message.

### Scratch databases created by individual tests

Migration tests create their own throwaway databases to drive Alembic
against. **Every one of them must get its name from
`conftest.scratch_database_name(label)`**, which returns
`tckdb_test_<label>_<uuid>` — inside the `tckdb_test%` pattern both
reclaimers below use, and trimmed so the unique part survives the
63-byte identifier limit.

Names built by hand (`tckdb_et_scope_migration_*`,
`tckdb_stage2_legacy_*`, `tckdb_exec_env_migration_*`) matched neither
reclaimer, so a run killed partway leaked them permanently; one was
found and dropped by hand on 2026-08-10.
[`tests/test_scratch_database_names.py`](../tests/test_scratch_database_names.py)
fails any test file that issues `CREATE DATABASE` without going
through the helper, so the next one cannot drift out silently, and
asserts the sweep and the standalone reclaim script still agree on
what "reclaimable" means.

### Pinned order and parallel workers

Both are applied by [`scripts/lib/pytest_run_args.sh`](../scripts/lib/pytest_run_args.sh),
which every `test-*.sh` script sources.

**The random-order seed is pinned to `424242`.** `pytest-randomly` is installed
and active, and left alone it seeds itself from the clock — so every gate
invocation ran the suite in a different order, a red gate could not be told
apart from bad luck, and a green one proved nothing about the next run. Two
different agents reported `3 failed` and `69 failed` for the *same commit*
purely because they drew different orders. A pinned seed does not make the
suite order-independent; the rollback fixtures and the committed-row tripwire
below do that. It makes a gate *result* reproducible.

That claim is only worth anything if somebody checks it, so it is checked. The
full suite is verified green at seeds **1, 2, 3, 4, 5, 7, 11, 90210 and
424242**, under **8, 4, 2 and 0** workers — 0 (serial) being the strict case,
since it puts all ~6,770 tests in one process against one database and so
exercises every cross-file adjacency the sharded runs hide.

**That audit is a measurement of a commit, not a property of the suite.** One
real order dependence appeared four days after it was taken, and it is worth
knowing its shape because the shape recurs:
`tests/db/test_imaginary_mode_tau_basis_constraint.py` downgraded the shared
database and restored it to the revision it was written against rather than to
`head`. That revision stopped being `head` the day `b7e4d1a9c026` landed on top
of it, so from then on the file left every later test in its process running
against a schema one revision old — surfacing as three failures in
`tests/db/test_statmech_torsion_index_uniqueness.py` asserting that a unique
constraint had the wrong name. The eight-way gates stayed green throughout: with
eight workers and eight databases, the offender and its victim only share a
process about one run in eight, which is exactly the blind spot the paragraph
above names. `tests/db/conftest.py::_must_leave_the_database_at_head` now fails
the offender at its own teardown, so the class no longer depends on a serial run
to be seen. Re-audit after adding a revision; do not treat the seed list as a
standing guarantee.

**Set `TCKDB_TEST_SEED=random` for an unpinned order.** The script draws a
fresh seed per invocation and echoes it:

```
tckdb: unpinned order, drew --randomly-seed=636985447 (replay with TCKDB_TEST_SEED=636985447)
```

It draws the seed itself rather than letting `pytest-randomly` pick one from
the clock because the plugin announces its choice in `pytest_report_header`,
which the `-q` every gate script passes suppresses — so a failure in an
unpinned order would arrive with no way to replay the order that produced it.
`backend-nightly.yml` runs this way; the PR gates stay pinned.

**Workers default to 8**, not to core count. Each xdist worker creates its own
database and runs `alembic upgrade head` into it (~6 s), so every extra worker
costs a database, a migration and a connection pool against one shared
Postgres. Measured on a 20-core host, full suite (6,618 tests) at seed 424242:

| Workers | Wall clock |
|---------|------------|
| 4       | 586 s      |
| 8       | 369 s      |
| 16      | 302 s      |

4 → 8 is a 1.6× gain; 8 → 16 buys only a further 1.22× for eight more
databases and no headroom left on the machine. 8 is where the returns flatten.

Tier 0/1 (`test-fast.sh`) and `test-profile.sh` default to 0 workers: one file
does not need eight databases, and durations measured under eight-way
contention describe the contention rather than the tests.

```bash
TCKDB_TEST_SEED=7 bash backend/scripts/test-full.sh        # a different order
TCKDB_TEST_SEED=random bash backend/scripts/test-full.sh   # unpinned order
bash backend/scripts/test-full.sh --randomly-seed=last     # caller wins
TCKDB_TEST_WORKERS=16 bash backend/scripts/test-full.sh    # more workers
TCKDB_TEST_WORKERS=0  bash backend/scripts/test-full.sh    # serial
```

An explicit `--randomly-seed=…`, `-n …`, or `-p no:randomly` on the command
line always beats the default, because the caller's arguments are appended
after the script's.

### Test-database cleanup and the startup sweep

Because every name embeds a per-run token, **no name is ever reused** —
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
   the creating host, pid and run token; the sweep drops a database
   **only** when all of the following hold:

   - it carries this harness's marker (databases created by anything
     else — including orphans that predate the marker — are never
     touched);
   - the marker's host is this host and its pid is no longer running
     (this is what stops a concurrently-starting pytest run from
     having its freshly created database swept away before it
     connects);
   - the marker's run token is **not this run's**. Under `-n` the
     eight workers of one run share a token, so a worker whose creator
     pid has been recycled must not have a sibling's live database
     dropped underneath it;
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
  bug — and the tripwire below should already have named the culprit.
  If it did not, the pollution is not committed rows: look at
  sequence counters (non-transactional, so `setval` survives a
  rollback), process-global state, and session-scoped fixtures.
- The order is pinned, so a failure reproduces. Confirm order
  *independence* by varying it on purpose:

  ```bash
  TCKDB_TEST_SEED=1 bash backend/scripts/test-full.sh      # a different order
  TCKDB_TEST_SEED=random bash backend/scripts/test-full.sh # any order at all
  bash backend/scripts/test-full.sh -p no:randomly         # declaration order
  ```

  Vary the *worker count* too, and finish with `TCKDB_TEST_WORKERS=0`.
  Workers shard the suite, so an eight-way run only ever puts a leaking
  test and its victim in the same process one time in eight; serial puts
  every test in one process against one database and is the case that
  actually decides order independence.

  For declaration order *reversed*, write a throwaway plugin outside the repo
  and load it by name — deliberately not a supported flag, because the suite
  should never have a way to pin an order:

  ```bash
  mkdir -p /tmp/po && printf 'def pytest_collection_modifyitems(items):\n    items.reverse()\n' \
      > /tmp/po/revorder.py
  cd backend
  PYTHONPATH=/tmp/po pytest -q -n 8 -p no:randomly -p revorder tests/
  ```

  Do **not** try to do this by collecting node ids and reversing the list:
  several parametrized ids in this suite embed newlines (ESS log fragments used
  as parameters), so a line-based `--collect-only | tac` silently mangles them
  into unrecognised arguments.
- The pytest fixture creates a fresh `tckdb_test` database via
  `alembic upgrade head` once per session (see
  [`tests/conftest.py`](../tests/conftest.py)) and rolls each test
  back inside its own transaction. Tests that commit raw bytes to
  external storage (MinIO) skip themselves when MinIO is not
  reachable — that's expected on a workstation without the dev
  container running.

### The ambient session factory

`app.api.deps` builds `engine` / `SessionLocal` at **import time** from
`settings.database_url` — the ambient `DB_NAME`, locally `tckdb_dev`.
That is right in a deployment, where the ambient database *is* the
database, and wrong under pytest, where nothing creates, migrates,
inspects or rolls back it.

It is not a theoretical hazard. It was root cause 2 of the
seed-independence work: five `/status` tests probed through
`health.SessionLocal`, passed in a dev shell and passed on the PR gate
(which runs `alembic upgrade head` against `DB_NAME` in an earlier
step), and failed only on the nightly, which does not. They were green
for a reason unrelated to what they asserted. Worse than a false
green: several of these call sites **commit**, and a commit into
`tckdb_dev` is invisible to the committed-row tripwire and to every
assertion in the suite.

Several sites cannot be handed a request-scoped session, so "thread a
session through" is not available to them:

| Site | Why it needs an out-of-request session |
|---|---|
| `services/upload_submission.record_failed_upload` | writes the failed-upload audit in a transaction *independent* of the request's, which has already rolled back |
| `services/artifact_integrity.record_artifact_integrity_event` | same shape — an append-only observation that must survive the caller's failure |
| `workers/upload_worker` | runs outside any request |
| `api/idempotency` | decorator; needs its own transaction for the idempotency record |
| `api/startup_checks.check_server_encoding` | runs at boot; there is no request |
| `routes/health` — `/health`, `/readyz`, `/status` | deliberately probe the process's **own** engine. Routing them through an overridable request dependency would let a test declare a deployment healthy while the deployment's engine is broken — the opposite of what the endpoint is for |

So the binding is made truthful rather than removed. `tests/conftest.py`
does two things, via `deps.bind_ambient_session_factory`:

1. **At import**, before any fixture, it points the factory at an
   engine that *refuses to connect*. A path that needs an
   out-of-request session and never requested `db_engine` now says so,
   instead of quietly writing to `tckdb_dev`.
2. **`db_engine`** rebinds it to the real per-worker engine for the
   session, and restores the refusing engine at teardown.

`sessionmaker.configure` mutates the factory in place, so the modules
that hold a `from app.api.deps import SessionLocal` reference — health,
idempotency, the upload worker, the archive CLI — all follow. Rebinding
only the module attribute would not have reached them, which is exactly
how the `/status` probes stayed broken.

Consequence for CI: **the suite no longer needs `DB_NAME` to exist, let
alone be migrated.** `tests/api/` passes with `DB_NAME` pointing at a
database that has never been created. Coverage lives in
[`tests/test_ambient_session_binding.py`](../tests/test_ambient_session_binding.py).

If you are writing a service that needs its own transaction, prefer an
injectable `session_factory=` parameter (as both services above have)
over reaching for `SessionLocal` directly — it lets a test pass the
per-test connection without touching global state.

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

#### The tripwire

The contract above is enforced, not merely documented.
`_refuse_committed_rows` in [`tests/conftest.py`](../tests/conftest.py) is an
autouse fixture that counts a curated set of tables before and after every
test that touches the database, and **fails the test that committed** — by
name, wherever it lives. Without it, a single committing test surfaced as an
inexplicable failure in whatever unrelated file `pytest-randomly` scheduled
next, hundreds of tests later.

It started in `tests/workflows/conftest.py` and now covers every tree, because
~40 other files had the same habit. Tests that legitimately commit satisfy it
by cleaning up: the counts match again by teardown. There is no exemption
marker, deliberately. `TCKDB_TEST_COMMIT_TRIPWIRE=0` disables it for bisecting
an unrelated failure, never as a way to land a committing test.

It makes **two** comparisons, not one. The first is the pair above: baseline
against final count, which catches a test body that commits. The second is
this test's baseline against the *previous* test's final count, which catches
rows that appeared when no test body was running at all — the setup of a
session- or module-scoped fixture (pytest instantiates those before any
function-scoped autouse fixture, so the first check is blind to them), a
subprocess, a background thread. That leak used to be undetectable by
construction and surfaced only as an unqualified query in some later file
returning a row nothing there created; now it names the two tests it appeared
between.

It watches a curated ~35-table union rather than all ~110 public tables
because counting everything costs ~90 ms per probe (~12 minutes over the
suite) against ~2 ms for the union. `app_user` and `api_key` are deliberately
excluded: the session-scoped `_api_test_user` fixture commits exactly one user
and key on first use, and watching those tables would blame whichever test
happened to run first.

#### Append-only tables

An append-only table cannot be cleaned up through the ordinary path.
`record_review_event` and `scientific_record_supersession` carry a
`BEFORE UPDATE OR DELETE` trigger (`tckdb_reject_mutation`, revision
`c6f2a9d4e7b1`), and the scientific tables additionally refuse `TRUNCATE`.

The narrow escape hatch is `SET LOCAL session_replication_role = replica`,
which suppresses user triggers **for one transaction only**. Three teardowns
use it — `tests/services/test_record_review_write_isolation.py`,
`tests/workers/test_upload_worker.py`,
`tests/services/archive/test_archive.py` — each confined to row ids the test
itself created (a reserved id band, or an `id >` high-water mark taken before
the test ran). It appears only in test teardown; no production code path and
no test *body* may use it, because the guard it suspends is the thing those
tests exist to protect.

The per-run disposable database remains the backstop — `_recreate_test_database`
at session start, `_drop_test_database` at session end, plus the startup sweep
above — but it is no longer the *primary* answer, and "the database gets
dropped eventually" is not an acceptable substitute for a teardown.

The other thing that outlives a test is **the schema itself**. The migration
round-trip tests in `tests/db/` run `alembic downgrade` against the per-run
database the whole process shares, so they must upgrade back to `head` — not
to the revision they were written about — in a `finally`. Stopping at a
historical revision leaves every migration after it un-applied for the rest of
the session, and the failure surfaces hundreds of tests later as
`column ... does not exist` in files that have nothing to do with migrations.
That single omission was what made `make test-full` unusable; see
`tests/db/test_dataset_release_migration.py::_restore_head`.

Running Alembic in-process also reconfigures **logging**: `alembic/env.py`
calls `logging.config.fileConfig`, which by default disables existing loggers
and replaces the root handlers — including the one `caplog` reads. Every
`caplog` assertion in the process comes back empty afterwards, in trees with
no connection to migrations. `tests/db/conftest.py` neutralises `fileConfig`
for that tree; a future in-process Alembic caller anywhere else needs the same
fixture moved up.

## Test policy

- Full suite is a **gate**, not the edit loop. Don't run Tier 4 on
  every save.
- Land Tier 1 green at minimum before pushing. Land Tier 3 green
  before opening a PR. Tier 4 is required before merging.
- The backend CI gate runs three independent jobs in parallel: the API job
  runs Tier 3 with `tests/api/scientific/` ignored; the scientific job runs
  the scientific API directory plus `tests/services/scientific_read/`; and
  the complement job runs everything neither of those selects. Together they
  cover every test file in `backend/tests/`, which is checked rather than
  claimed
  ([`tests/scripts/test_gate_coverage.py`](../tests/scripts/test_gate_coverage.py)).

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

- shell/doc hygiene checks (`git diff --check`, `bash -n` over **every**
  tracked `*.sh` — derived from `git ls-files`, not enumerated, because
  the enumerated list had never included `dev_login.sh` or
  `tckdb_auth.sh` — and `make help`)
- `alembic upgrade head`, `alembic heads`, `alembic current` and
  `alembic check`, against a scratch database created by the step
- the OpenAPI golden snapshot test at
  [`tests/api/test_openapi_snapshot.py`](../tests/api/test_openapi_snapshot.py)
- the API gate via [`../scripts/test-api.sh`](../scripts/test-api.sh)
- the scientific read/service gate via
  [`../scripts/test-scientific.sh`](../scripts/test-scientific.sh)
- the complement gate via [`../scripts/test-rest.sh`](../scripts/test-rest.sh)

The API gate ignores both `tests/api/scientific/` (covered by the scientific
job) and `tests/api/test_openapi_snapshot.py` (run once by its dedicated
golden-snapshot step). The final `Backend CI` job is a stable aggregate status
check: it runs even after an upstream failure or cancellation and fails unless
the matrix result is `success`, while leaving every detailed gate check visible.

### The ambient database is never migrated

`DB_NAME` — the database `settings.database_url` points at, as opposed to the
per-worker `DB_TEST_NAME` databases the fixtures create and migrate — exists
and is deliberately left empty in **both** `backend-ci.yml` and
`backend-nightly.yml`. Nothing should migrate it, and no test should need it
migrated: a test that reaches it is reaching past every fixture.

The alembic steps run against their own scratch database for exactly this
reason. They are migration checks (one head, applies cleanly, no model/schema
drift) and none of that is a property of the ambient database. Pointing them
at `DB_NAME` made "CI happens to migrate the database the tests inherit" an
undeclared part of the environment — and since the nightly had no such step,
five `/status` tests passed on every PR and failed every night for eleven
nights, attached to no PR and explained by nothing in the file. Both workflows
now present the same empty database, so that class of coupling fails the pull
request that introduces it.

Each CI job owns an isolated Postgres service and MinIO service. Its
`DB_TEST_NAME` and `S3_BUCKET` include both the GitHub run id/attempt and the
job role, so concurrent jobs and workflow runs do not share test resources.
The run token now makes the `DB_TEST_NAME` half of that redundant for
isolation — it stays because it makes a job's databases identifiable.

The gates run under xdist, at `TCKDB_TEST_WORKERS=4` rather than the local
default of 8, because a GitHub standard runner has 4 vCPUs and one Postgres
container. `DB_TEST_NAME` is still set per job; `_resolve_test_db_name` appends
the run token and the worker id to it, so each worker gets its own database
instead of four workers racing on one recreated one.

Nothing in the suite depends on `DB_NAME` any more — see *The ambient
session factory* above. A workflow step that runs `alembic upgrade head`
against `DB_NAME` before the gate is no longer load-bearing for the
tests, and the PR gate / nightly divergence it created (one migrated it,
the other did not) can no longer hide a failure in one environment.

Since the three gates cover `backend/tests/` between them, a PR now runs every test
[`backend-nightly.yml`](../../.github/workflows/backend-nightly.yml) runs.
What the nightly still adds is the **order**: the gates pin a seed so a red
gate means a regression rather than an unlucky draw, and the nightly draws
`TCKDB_TEST_SEED=random` and echoes it, so an order-dependent defect surfaces
somewhere. `make test-full` locally remains the cheapest way to get both at
once before pushing.

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

## Parallelization

[`pytest-xdist`](https://pypi.org/project/pytest-xdist/) is installed (a `dev`
extra in `backend/pyproject.toml`) and **on by default** in the Tier 2/3/4
scripts. See [Pinned order and parallel workers](#pinned-order-and-parallel-workers)
for the flags and how to override them.

Order-independence was the prerequisite, not a separate nicety: xdist
distributes tests across workers in arbitrary groupings, so a suite that only
passed in particular orders could not survive it. What made it safe:

- **A database per worker, and per run.** `_resolve_test_db_name` appends the
  run token and the worker id even when `DB_TEST_NAME` is set explicitly,
  which every gate script and CI job does. Without the worker id, all workers
  dropped, recreated and then wrote one database concurrently; without the run
  token, two runs on one host did the same to each other.
- **No test commits to the shared database.** ~50 tests across 16 files did;
  they now persist through `db_conn`, and the tripwire above keeps it that way.
- **Advisory locks are per-database.** The only one in the app
  (`pg_advisory_xact_lock` in `app/services/conformer_resolution.py`) is
  transaction-scoped and lives in each worker's own database, so workers cannot
  contend.
- **Migration round-trip tests own their databases.** The `tests/db/` tests that
  run `alembic downgrade` create a `uuid4`-named database of their own rather
  than downgrading the session database.
- **Worker-local process state is harmless.** Each worker is a separate process,
  so the in-memory rate-limit store and the module-level factory counters in
  `tests/services/scientific_read/_factories.py` are already isolated.

Things still worth knowing:

- Worker count is a tuning knob, not a core count — the Postgres server is
  shared. 8 is the measured default; see the header of
  [`scripts/lib/pytest_run_args.sh`](../scripts/lib/pytest_run_args.sh).
- The MinIO bucket is **not** per worker. Artifact tests key objects by content
  hash or by row id from their own worker-local database, so they do not
  collide today, but a future test that writes a fixed object key would.
- `--dist load` (the default) may split one file across workers. Nothing in the
  suite depends on file grouping; if something appears to, that is a leak to
  fix rather than a reason to reach for `--dist loadfile`.
- **`tmp_path` usage multiplies by the worker count.** The CCCBDB importer tests
  write snapshot trees into `tmp_path`, and pytest retains the last three
  `basetemp` directories per worker. On a host where `/tmp` is a small tmpfs
  shared with other workloads this exhausts it, and the whole band of tests
  running at that moment fails with `OSError: [Errno 122] Disk quota exceeded` —
  which looks nothing like a disk problem in the summary. Point `TMPDIR` (or
  `--basetemp`) at real disk if `df -h /tmp` is tight.
