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
# Run a different order on purpose:
#   TCKDB_TEST_SEED=1 bash backend/scripts/test-full.sh
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

    if [[ "$caller_args" != *" -p no:randomly "* && "$caller_args" != *"--randomly-seed"* ]]; then
        TCKDB_PYTEST_ARGS+=("--randomly-seed=${seed}")
    fi

    # ``-n0`` is xdist's own "run in this process" mode; asking for it via the
    # flag rather than omitting it keeps the plugin's reporting consistent.
    if [[ "$caller_args" != *" -n "* && "$caller_args" != *"-n"[0-9]* && "$caller_args" != *"--numprocesses"* ]]; then
        TCKDB_PYTEST_ARGS+=("-n" "${workers}")
    fi
}
