# Shared pytest invocation policy for the test-* gate scripts.
#
# Sourced, not executed. Defines ``tckdb_pytest_run_args`` which populates the
# array ``TCKDB_PYTEST_ARGS`` with the two things every gate needs and no gate
# should have to remember: a pinned random-order seed, and parallel workers.
#
# ---------------------------------------------------------------------------
# Why the seed is pinned
# ---------------------------------------------------------------------------
# ``pytest-randomly`` is installed and active, and by default it seeds itself
# from the clock. Every gate invocation therefore ran the suite in a *different*
# order, which means a red gate could not be told apart from bad luck and a
# green one proved nothing about the next run. Two agents reported wildly
# different failure counts for the same commit (``3 failed`` vs ``69 failed``)
# purely because they drew different orders.
#
# A pinned seed does not make the suite order-independent — the per-test
# rollback fixtures and the committed-row tripwire in ``tests/conftest.py`` do
# that. It makes a *gate result* reproducible, so a regression is a regression.
#
# 424242 is the seed the order-dependence audit was conducted against; it is
# deliberately a known-hostile order rather than a lucky one.
#
# Pinning is reproducibility, not a workaround — and the difference is only
# credible if something still runs an unpinned order. The suite is verified
# order-independent at seeds 1, 2, 3, 4, 5, 7, 11, 90210 and 424242, across 8,
# 4, 2 and 0 workers (see backend/docs/testing.md), so the pin now costs
# nothing but the coverage it would remove if it were the only mode. Hence:
#
#   TCKDB_TEST_SEED=random bash backend/scripts/test-full.sh
#
# draws a fresh seed per invocation and echoes it, so a failure found that way
# is still reproducible with TCKDB_TEST_SEED=<that value>. That is the mode a
# scheduled full-suite run should use: a nightly that draws the same order
# every night re-tests one ordering 365 times a year.
#
# Run a different order on purpose:
#   TCKDB_TEST_SEED=1 bash backend/scripts/test-full.sh
#   TCKDB_TEST_SEED=random bash backend/scripts/test-full.sh
#   bash backend/scripts/test-full.sh --randomly-seed=last   # (caller wins)
#
# ---------------------------------------------------------------------------
# Why the worker count is 8 and not 20
# ---------------------------------------------------------------------------
# Each xdist worker creates its own PostgreSQL database and runs
# ``alembic upgrade head`` into it (~6 s), so every extra worker costs a
# database, a migration and a pool of connections against one shared Postgres.
# Full suite (6,618 tests), 20-core host, seed 424242:
#
#   -n 4 ....... 586 s
#   -n 8 ....... 369 s   <- default
#   -n 16 ...... 302 s
#
# 4 -> 8 is a 1.6x gain; 8 -> 16 is only a further 1.22x. Returns have flattened
# by 8, and the second half of that curve is bought with eight more databases,
# eight more migrations, and a box with no headroom left for the developer who
# is running the tests. 8 is the point where the suite is fast enough that the
# gate stops being something to avoid.
#
# Override for a machine with a different shape:
#   TCKDB_TEST_WORKERS=16 bash backend/scripts/test-full.sh  # dedicated box
#   TCKDB_TEST_WORKERS=4  bash backend/scripts/test-full.sh
#   TCKDB_TEST_WORKERS=0  bash backend/scripts/test-full.sh  # serial
#
# ---------------------------------------------------------------------------
# Caller precedence
# ---------------------------------------------------------------------------
# The gate scripts append "$@" *after* these args, so an explicit
# ``--randomly-seed=...`` or ``-n ...`` on the command line wins. Passing
# ``-p no:randomly`` disables the plugin, which would make ``--randomly-seed``
# an unrecognised argument, so it is detected and the seed is dropped.

tckdb_pytest_run_args() {
    TCKDB_PYTEST_ARGS=()

    local caller_args=" $* "
    local seed="${TCKDB_TEST_SEED:-424242}"
    # ``TCKDB_DEFAULT_WORKERS`` is the *script's* default (the inner-loop
    # runners set 0: one file does not need eight databases). ``TCKDB_TEST_WORKERS``
    # is the *operator's* override and outranks it.
    local workers="${TCKDB_TEST_WORKERS:-${TCKDB_DEFAULT_WORKERS:-8}}"

    # ``random`` draws a seed here rather than letting pytest-randomly draw its
    # own from the clock. Both give an unpinned order; only this one is
    # reproducible afterwards. pytest-randomly announces the seed it picked in
    # ``pytest_report_header``, which ``-q`` -- what every gate script passes --
    # suppresses, so an unpinned failure would arrive with no way to replay the
    # order that produced it. Drawing it here and echoing it puts the seed in
    # the job log unconditionally, whatever the verbosity.
    if [[ "$caller_args" != *" -p no:randomly "* \
          && "$caller_args" != *"--randomly-seed"* ]]; then
        if [[ "$seed" == "random" ]]; then
            seed=$(( (RANDOM << 15 | RANDOM) + 1 ))
            echo "tckdb: unpinned order, drew --randomly-seed=${seed}" \
                 "(replay with TCKDB_TEST_SEED=${seed})" >&2
        fi
        TCKDB_PYTEST_ARGS+=("--randomly-seed=${seed}")
    fi

    # ``-n0`` is xdist's own "run in this process" mode; asking for it via the
    # flag rather than omitting it keeps the plugin's reporting consistent.
    if [[ "$caller_args" != *" -n "* && "$caller_args" != *"-n"[0-9]* && "$caller_args" != *"--numprocesses"* ]]; then
        TCKDB_PYTEST_ARGS+=("-n" "${workers}")
    fi
}
