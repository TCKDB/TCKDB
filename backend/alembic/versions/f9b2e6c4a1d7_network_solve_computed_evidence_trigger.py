"""Make ``computed`` a claim the database can refuse, not just the API.

``c4d8f1b2a9e6`` added ``network_solve.kind`` and made *half* the contract
structural: ``ck_network_solve_reported_requires_literature`` holds
``reported ⇒ literature_id IS NOT NULL`` in the record itself. The other half —
``computed ⇒ this database holds the master-equation inputs`` — stayed in a
single Pydantic validator (``validate_mechanistic_channel_evidence`` in
``app/schemas/workflows/network_pdep_upload.py``) on the single wired write
path. ADR 0010 named that asymmetry as the same fragility it was written to
correct, sharpened by ``NetworkSolveBase`` defaulting ``kind`` to ``computed``:
a second write path would not merely *imply* a master-equation solve by table
membership, it would affirmatively *assert* one. This revision closes it as far
as SQL can honestly close it.

Why a trigger and not a CHECK
-----------------------------
A ``network_solve`` row is inserted *before* the children that evidence it —
``network_solve_state_energy``, ``network_solve_channel_barrier`` and
``network_solve_energy_transfer`` all carry ``solve_id``, so they cannot exist
until the solve does (``app/workflows/network_pdep.py`` writes the solve, then
flushes, then the children). A ``CHECK`` or a ``NOT NULL`` is evaluated per
statement and would reject every legitimate insert. The guarantee is only
expressible at *commit*, which is what ``CONSTRAINT TRIGGER ... DEFERRABLE
INITIALLY DEFERRED`` is for.

What this trigger guarantees
----------------------------
At COMMIT, for a ``network_solve`` row with ``kind = 'computed'``: **a nonzero
amount of master-equation evidence of each applicable class.**

1. **At least one ``network_solve_state_energy``.** Solving a master equation
   requires an energy surface; a computed solve holding none asserts a
   derivation that is simply absent.
2. **At least one ``network_solve_energy_transfer``** — applicable when the
   solve's network declares at least one ``well`` state. Collisional energy
   transfer is what makes a network pressure-dependent at all, so an energized
   intermediate with no ⟨ΔE⟩down model is a solve with nothing to solve over.
3. **At least one ``network_solve_channel_barrier``** — applicable when the
   network declares at least one *saddle-point* path (a
   ``network_channel_microreaction`` row with a non-NULL
   ``transition_state_entry_id``). An all-barrierless network is correct
   science with legitimately zero barriers, so the rule is conditional; an
   unconditional one would refuse a correct result, which ADR 0008 disqualifies
   outright.

It fires on ``INSERT``/``UPDATE`` of ``network_solve`` (so relabelling a
``reported`` solve to ``computed`` is checked too), and on ``DELETE``/``UPDATE``
of the three evidence tables (so the evidence cannot be pulled out from under a
committed computed solve).

What this trigger deliberately does NOT guarantee
-------------------------------------------------
It enforces **existence**, not **coverage**. The three coverage rules stay with
``validate_mechanistic_channel_evidence`` and are *not* reimplemented here:

* one state energy for **every** network state,
* one ⟨ΔE⟩down per **(well, bath-gas collider)** pair, or a single
  ``network_wide`` declaration (ADR 0009),
* one barrier for **every** saddle-point channel path, and none for a
  barrierless one.

So the database now guarantees ``reported ⇒ literature`` and
``computed ⇒ nonzero ME evidence of each applicable class``. **Full coverage
remains a property of the wired upload path only.** Nobody should read the
trigger as the whole contract.

One further hole is named rather than papered over: these are ``FOR EACH ROW``
triggers, and ``TRUNCATE`` fires no row-level trigger, so
``TRUNCATE network_solve_state_energy`` would strand every computed solve
unevidenced. Statement-level ``TRUNCATE`` guards are how ``e3f4a5b6c7d8``
protects the append-only release tables and would be the fix; they are not
added here because ``TRUNCATE`` on these tables is an operator action outside
the write paths this backstop is for, and one narrow rule at a time is easier
to reason about than two.

That boundary is where ADR 0008's tiering rule puts it, and not by accident. A
computed solve with *zero* state energies **contradicts its own kind** — it is
exactly the over-claiming row ADR 0010 was written about, and a contradiction
is definitional, so it may block. A computed solve with four state energies out
of five is an *incompleteness*, and ADR 0008 is explicit that an incomplete
record is still a true record, to be graded by the trust and reproducibility
layers rather than refused.

Three failure-mode arguments seal it.

*Drift produces production 500s, and this repo has lived the precedent.* ADR
0009 **relaxed** a coverage rule after the fact: the per-(well × collider)
cross product gained a ``network_wide`` exemption because the old rule
manufactured false provenance. A full-coverage trigger written before 0009
would have refused every legitimate network-wide deposit *at COMMIT*, as an
opaque IntegrityError-turned-500 rather than a clean 422, until a lockstep
migration reached the deployed database. ADR 0009 anticipates further scope
members. Existence-only is monotone under every such relaxation: a payload
satisfying any future coverage rule still carries at least one row of each
applicable class, so this trigger can never drift stricter than the front door.

*Full coverage would duplicate one fact across tiers, which ADR 0008 forbids* —
the blocking tier owns a rule and the others cite it. A PL/pgSQL copy of
``validate_mechanistic_channel_evidence`` is two implementations of one rule in
two languages with two consequences, free to disagree silently. Existence is a
*different, weaker* fact, so the layering is deliberate rather than duplicated:
the trigger owns the contradiction, the validator owns the coverage.

*The residual gap is queryable*, which is the property ADR 0010 celebrates
turning an unqueryable gap into. Three set-difference queries find any computed
solve that passes existence and would fail coverage; before this revision there
was no predicate at all.

Each condition is gated on the topology for one further reason: **a backstop
must never be stricter than the front door.** The validator requires
``energy_transfer == wells × bath_gas`` and ``barriers == saddle-point paths``,
so a network with no wells must supply *zero* ⟨ΔE⟩down rows and an
all-barrierless network *zero* barriers. Unconditional requirements here would
reject payloads the upload path is obliged to accept and turn a clean 422 into
a raw Postgres error. In the other direction the implication runs the safe way:
``computed`` forces ``bath_gas`` to sum to 1.0 and hence to be non-empty, so
one well implies at least one expected (well, collider) pair; and one
saddle-point path implies one expected barrier. Where the trigger asks for a
row, the validator already required one.

Coverage is also, unlike existence, a statement about the *network topology* as
much as the solve: a faithful coverage trigger would have to fire on
``network_state``, ``network_channel``, ``network_channel_microreaction`` and
``network_solve_bath_gas``, each of which can change in a *later* transaction
than the one that wrote the solve. Checked only at solve-commit, it would be
checkable-but-evadable — add a state next month and the "guarantee" lapses
while the schema advertises that it cannot.

That argument has a dual, and it bites the *existence* rules too. Because
applicability is read from topology at check time rather than at write time,
adding topology later makes an already-committed solve **retroactively
non-conforming**. Concretely: a well-less ``computed`` solve legitimately
commits with zero energy-transfer rows; someone then inserts a
``network_state`` with ``kind='well'`` into its network, which is accepted
silently because ``network_state`` carries no trigger; from that moment every
write touching the solve or its evidence is refused, including
``UPDATE network_solve SET note='typo fix'``. The same happens when a
saddle-point path is added to a previously all-barrierless network. The record
is readable but frozen: it can be neither corrected nor annotated in place
until the now-expected evidence class is supplied.

This is judged acceptable rather than fixed. The pre-existing state really is
scientifically wrong once the well exists — a pressure-dependent well with no
collisional energy transfer is not a solve — so refusing to let it drift
further is defensible, and the escape is to supply the evidence or re-declare
the solve ``reported``. It is recorded here because the failure surfaces as an
opaque ``CheckViolation`` on an innocuous edit, and only an operator working
outside the wired path can reach it, which is precisely the audience this
backstop exists for.

This is belt and braces, not a replacement. ``validate_mechanistic_channel_evidence``
is untouched and remains the full contract; the trigger is the floor under
write paths that never reach it — the unwired ``NetworkSolveCreate`` entity
schema, an admin's ``psql`` session, a future bulk importer.

Backfill design
---------------
A trigger, unlike ``ADD CONSTRAINT CHECK``, does **not** scan the rows already
in the table. Creating one over data that already violates it yields a rule
true only of the future, which is the kind of half-guarantee ADR 0010 exists to
stop — so this revision does the scan the DDL will not.

``c4d8f1b2a9e6.upgrade()`` refuses to apply while any solve lacks state
energies, so every solve present *at that revision* provably has them. That is
a real guarantee and it is deliberately **not** relied on alone, for three
reasons this revision checks rather than assumes:

* it establishes nothing about ``network_solve_energy_transfer`` or
  ``network_solve_channel_barrier``, which this trigger also requires;
* it says nothing about rows written *after* ``c4d8f1b2a9e6`` applied and
  before this revision does — precisely the window in which a write path that
  bypasses the validator would leave a row this trigger later refuses; and
* it is a claim about a foreign database, which the operator applying this
  revision can check for themselves in one statement and should not have to
  take on trust.

Citing an ancestor migration's guard as proof about today's rows is the same
shape of argument that was wrong in ``c4d8f1b2a9e6``'s own first draft, where a
validator was offered as proof about pre-existing rows. So ``upgrade()`` asks
the database directly, for both conditions, and refuses with the row list if
either fails — a trigger created over data that already violates it would be a
constraint that is true only of the future.

The deployed hydrazine network (network 4, ``net_o6bt63kjeyvhvxx26w6kdi433a``:
one computed solve, 7 state energies, 2 ``per_well`` energy-transfer rows, 21
channels, 42 kinetics rows) was deposited through the wired upload path under
the post-``c1d2e3f4a5b6`` contract, so ``validate_mechanistic_channel_evidence``
held it to full coverage of all three classes and it satisfies these weaker
existence rules a fortiori. That is an inference, not a measurement — which is
exactly why ``upgrade()`` measures.

``downgrade()`` drops the triggers and the function. It needs no guard:
removing a constraint can invalidate no stored row, and the validator that
carried this rule before this revision still carries it after.

Revision ID: f9b2e6c4a1d7
Revises: c4d8f1b2a9e6
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f9b2e6c4a1d7"
down_revision: Union[str, Sequence[str], None] = "c4d8f1b2a9e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FUNCTION_NAME = "network_solve_computed_evidence"

# (trigger name, table, events)
TRIGGERS = (
    ("ct_network_solve_computed_evidence", "network_solve", "INSERT OR UPDATE"),
    (
        "ct_network_solve_state_energy_computed_evidence",
        "network_solve_state_energy",
        "DELETE OR UPDATE",
    ),
    (
        "ct_network_solve_energy_transfer_computed_evidence",
        "network_solve_energy_transfer",
        "DELETE OR UPDATE",
    ),
    (
        "ct_network_solve_channel_barrier_computed_evidence",
        "network_solve_channel_barrier",
        "DELETE OR UPDATE",
    ),
)

# A row-level trigger does not fire for TRUNCATE, so the constraint triggers
# above cannot see it: ``TRUNCATE network_solve_state_energy CASCADE`` would
# leave every ``computed`` solve unevidenced in a database whose schema
# advertises that this cannot happen. Naming that hole is not enough when the
# audience this backstop exists for -- an admin at a psql prompt -- is exactly
# who reaches for TRUNCATE. Statement-level, because TRUNCATE has no rows to
# iterate, and a CONSTRAINT TRIGGER cannot cover it by construction (those must
# be ``AFTER ... FOR EACH ROW``). Same shape as the guards ``e3f4a5b6c7d8``
# installs over the release layer.
TRUNCATE_FUNCTION_NAME = "network_solve_computed_evidence_truncate"
TRUNCATE_GUARD_TABLES = (
    "network_solve_state_energy",
    "network_solve_energy_transfer",
    "network_solve_channel_barrier",
)

_TRUNCATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {TRUNCATE_FUNCTION_NAME}() RETURNS trigger AS $$
DECLARE
    computed_count bigint;
BEGIN
    SELECT count(*) INTO computed_count
      FROM network_solve
     WHERE kind = 'computed';

    IF computed_count > 0 THEN
        RAISE EXCEPTION
            'truncate_would_strand_computed_evidence: TRUNCATE on % would '
            'remove master-equation evidence while % network_solve row(s) '
            'still declare kind=''computed'', which asserts that this '
            'database holds the derivation behind their k(T,P).',
            TG_TABLE_NAME, computed_count
            USING ERRCODE = '23514',
                  HINT = 'Use DELETE instead -- it is row-level, so the '
                         'deferred constraint triggers check each affected '
                         'solve at COMMIT. To discard the solves too, delete '
                         'them first, or re-declare them kind=''reported'' '
                         'with the publication their rates came from '
                         '(ADR 0010).';
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

# Applicability predicates, kept next to each other so the shape is obvious:
# each rule bites only when the network topology says the class of evidence is
# expected at all. Held in SQL rather than in prose because the trigger and the
# ``upgrade()`` guard must ask exactly the same question.
_HAS_WELL = """
    EXISTS (
        SELECT 1 FROM network_state st
         WHERE st.network_id = {network_id}
           AND st.kind = 'well'
    )
"""

_HAS_SADDLE_POINT_PATH = """
    EXISTS (
        SELECT 1
          FROM network_channel_microreaction m
          JOIN network_channel c ON c.id = m.channel_id
         WHERE c.network_id = {network_id}
           AND m.transition_state_entry_id IS NOT NULL
    )
"""


# ``RETURN NULL`` throughout: this is an AFTER trigger, so the return value is
# discarded. Every early return is a case where the row is not this trigger's
# business — most importantly the first one, which is what lets a whole network
# be torn down in a single transaction. At COMMIT the solve is already gone, so
# there is no computed claim left to evidence and nothing to complain about.
_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION_NAME}() RETURNS trigger AS $$
DECLARE
    target_id bigint;
    solve record;
    applicable boolean;
BEGIN
    IF TG_TABLE_NAME = 'network_solve' THEN
        target_id := NEW.id;
    ELSE
        -- Evidence tables: only losing a row can break an existence rule, so
        -- the solve at risk is always the one OLD pointed at.
        target_id := OLD.solve_id;
    END IF;

    SELECT s.id, s.kind, s.network_id, s.public_ref
      INTO solve
      FROM network_solve s
     WHERE s.id = target_id;

    IF NOT FOUND THEN
        -- Solve deleted in this same transaction (network teardown).
        RETURN NULL;
    END IF;

    -- A reported solve holds none of these inputs by construction (ADR 0010).
    IF solve.kind <> 'computed' THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM network_solve_state_energy e WHERE e.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_state_energy: network_solve % declares '
            'kind=''computed'', which asserts that this database holds the '
            'master-equation derivation behind its k(T,P), but no '
            'network_solve_state_energy row was committed for it.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Deposit the state energies with the solve, or '
                         'declare kind=''reported'' and cite the publication '
                         'the rates were transcribed from (ADR 0010).';
    END IF;

    -- Gated on the topology so the trigger is never stricter than
    -- validate_mechanistic_channel_evidence, which requires exactly zero
    -- energy-transfer rows for a network that declares no wells.
    SELECT {_HAS_WELL.format(network_id="solve.network_id")} INTO applicable;

    IF applicable AND NOT EXISTS (
        SELECT 1 FROM network_solve_energy_transfer t WHERE t.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_energy_transfer: network_solve % declares '
            'kind=''computed'' on a network with an energized well, but no '
            'network_solve_energy_transfer row was committed for it. '
            'Collisional energy transfer is what makes the network '
            'pressure-dependent; without it there is no solve to speak of.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Supply one per_well entry per (well, bath-gas '
                         'collider) pair, or a single network_wide entry '
                         '(ADR 0009); or declare kind=''reported'' (ADR 0010).';
    END IF;

    -- Likewise gated: an all-barrierless network is correct science with
    -- legitimately zero barriers, and refusing a correct result is what
    -- ADR 0008 forbids the blocking tier to do.
    SELECT {_HAS_SADDLE_POINT_PATH.format(network_id="solve.network_id")}
      INTO applicable;

    IF applicable AND NOT EXISTS (
        SELECT 1 FROM network_solve_channel_barrier b WHERE b.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_channel_barrier: network_solve % declares '
            'kind=''computed'' on a network with at least one saddle-point '
            'path, but no network_solve_channel_barrier row was committed '
            'for it. The barrier heights are what the microcanonical rates '
            'were computed from.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Supply one barrier per saddle-point channel path '
                         'and none for a barrierless one; or declare '
                         'kind=''reported'' (ADR 0010).';
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


# Deliberately the same predicates the trigger uses, so the pre-flight scan and
# the rule it installs cannot disagree about which rows are acceptable.
_SCAN_SQL = f"""
SELECT s.id,
       s.public_ref,
       NOT EXISTS (
           SELECT 1 FROM network_solve_state_energy e WHERE e.solve_id = s.id
       ) AS no_energy,
       ({_HAS_WELL.format(network_id="s.network_id")}
        AND NOT EXISTS (
            SELECT 1 FROM network_solve_energy_transfer t WHERE t.solve_id = s.id
        )) AS no_transfer,
       ({_HAS_SADDLE_POINT_PATH.format(network_id="s.network_id")}
        AND NOT EXISTS (
            SELECT 1 FROM network_solve_channel_barrier b WHERE b.solve_id = s.id
        )) AS no_barrier
  FROM network_solve s
 WHERE s.kind = 'computed'
 ORDER BY s.id
"""


def _refuse_unevidenced_computed_solves(bind) -> None:
    """Ask the database, rather than inherit a claim from an ancestor guard."""
    rows = bind.execute(sa.text(_SCAN_SQL)).all()
    offenders = [
        row for row in rows if row.no_energy or row.no_transfer or row.no_barrier
    ]
    if not offenders:
        return

    def _describe(row) -> str:
        missing = []
        if row.no_energy:
            missing.append("state energies")
        if row.no_transfer:
            missing.append("energy transfer")
        if row.no_barrier:
            missing.append("channel barriers")
        return f"{row.public_ref or row.id} (missing {', '.join(missing)})"

    listed = ", ".join(_describe(row) for row in offenders[:20])
    more = "" if len(offenders) <= 20 else f" (and {len(offenders) - 20} more)"
    raise RuntimeError(
        f"{len(offenders)} network_solve row(s) with kind='computed' hold none "
        f"of the master-equation evidence this revision makes structural: "
        f"{listed}{more}.\n"
        "Creating the constraint trigger over them would produce a rule that "
        "is true only of future rows, which is the kind of half-guarantee "
        "ADR 0010 exists to stop.\n"
        "Classify them first, then re-run:\n"
        "  1. Inspect: SELECT id, public_ref, network_id, me_method, "
        "literature_id FROM network_solve WHERE id IN (...);\n"
        "  2. If the master-equation inputs exist elsewhere, re-upload the "
        "network under the current contract (see "
        "backend/docs/deployment/migrations.md).\n"
        "  3. If the rates came from a publication, attach the literature and "
        "set kind='reported'.\n"
        "  4. If the rows are abandoned scaffolding, delete them."
    )


def upgrade() -> None:
    """Add the deferred constraint trigger backing ``kind='computed'``."""
    bind = op.get_bind()
    _refuse_unevidenced_computed_solves(bind)

    op.execute(_FUNCTION_SQL)
    for name, table, events in TRIGGERS:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {name} "
            f"AFTER {events} ON {table} "
            "DEFERRABLE INITIALLY DEFERRED "
            f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}()"
        )

    op.execute(_TRUNCATE_FUNCTION_SQL)
    for table in TRUNCATE_GUARD_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_computed_evidence_truncate "
            f"BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {TRUNCATE_FUNCTION_NAME}()"
        )


def downgrade() -> None:
    """Drop the trigger; the Pydantic validator still carries the rule."""
    for table in TRUNCATE_GUARD_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS {table}_computed_evidence_truncate ON {table}"
        )
    op.execute(f"DROP FUNCTION IF EXISTS {TRUNCATE_FUNCTION_NAME}()")
    for name, table, _events in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_NAME}()")
