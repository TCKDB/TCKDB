"""Measure the read API against a catalog-scale corpus and record the evidence.

What this measures, and what it does not
----------------------------------------
Each shape in :mod:`bench.query_shapes` is issued through the real FastAPI
application against a ``tckdb_bench_*`` database, so the number reported is
end-to-end: routing, dependency resolution, the service layer, every SQL
round-trip it makes, and response serialization. That is what a client
experiences, and it is the only number worth publishing.

Alongside the latency, the harness records **how many SQL statements** each
shape issued and the **slowest statement's** ``EXPLAIN (ANALYZE, BUFFERS)``
plan. The statement count is the diagnostic that matters most here: a shape
that issues one query per candidate record has a cost curve no index can fix,
and averaging its latency on a small corpus hides that completely.

Honesty constraints this harness enforces
-----------------------------------------
- A shape that returns **zero** records is reported as ``empty`` and its
  timings are flagged, because a search that matched nothing is fast for the
  wrong reason.
- The measured environment (CPU, RAM, PostgreSQL and RDKit versions, corpus
  counts) is captured into the output, so a number can never be quoted
  without the machine it came from.
- Nothing here converts an x86 measurement into a claim about the arm64
  Raspberry Pi deployment. The output labels the hardware and says so.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.build_corpus import _validate_db_name, measure_corpus
from bench.query_shapes import QueryShape, shapes

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Environment capture
# ---------------------------------------------------------------------------


def _read_cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _read_total_ram_gib() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError):
        pass
    return None


def capture_environment(db_name: str) -> dict[str, object]:
    """Everything needed to interpret a timing in this file."""
    import rdkit
    import sqlalchemy

    engine = create_engine(_sqlalchemy_url(db_name), future=True)
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("SELECT version()")).scalar_one()
            cartridge = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='rdkit'")
            ).scalar()
            tuning = {
                row[0]: f"{row[1]}{row[2] or ''}"
                for row in conn.execute(
                    text(
                        "SELECT name, setting, unit FROM pg_settings WHERE name IN "
                        "('shared_buffers','work_mem','effective_cache_size',"
                        "'max_parallel_workers_per_gather','random_page_cost',"
                        "'jit')"
                    )
                )
            }
    finally:
        engine.dispose()

    return {
        "hardware_class": "x86_64 developer workstation",
        "NOT_the_deployment_target": (
            "The TCKDB deployment target is an arm64 Raspberry Pi. Every number "
            "in this file was measured on the x86_64 workstation described "
            "here. No Pi measurement was taken and none of these figures may "
            "be extrapolated to Pi performance."
        ),
        "cpu_model": _read_cpu_model(),
        "cpu_arch": platform.machine(),
        "cpu_logical_cores": os.cpu_count(),
        "ram_gib": _read_total_ram_gib(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "postgresql": server_version,
        "postgresql_tuning": tuning,
        "rdkit_python": rdkit.__version__,
        "rdkit_cartridge": cartridge,
        "sqlalchemy": sqlalchemy.__version__,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    }


def _sqlalchemy_url(db_name: str) -> str:
    env = {
        "DB_USER": os.environ.get("DB_USER", "tckdb"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "tckdb"),
        "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "DB_PORT": os.environ.get("DB_PORT", "5432"),
    }
    return (
        f"postgresql+psycopg://{env['DB_USER']}:{env['DB_PASSWORD']}"
        f"@{env['DB_HOST']}:{env['DB_PORT']}/{db_name}?client_encoding=utf8"
    )


# ---------------------------------------------------------------------------
# Statement recording
# ---------------------------------------------------------------------------


@dataclass
class StatementRecord:
    """One SQL statement issued while serving a request."""

    sql: str
    params: object
    seconds: float


class StatementRecorder:
    """Records every SQL statement a request issues, and how long it took.

    Attached with SQLAlchemy ``before_cursor_execute`` /
    ``after_cursor_execute`` events. The statement *count* is as important as
    the total time: it is what distinguishes "this query needs an index" from
    "this endpoint issues one query per candidate row", and only the second
    explains why a shape that is fine on 55 species collapses on 50,000.
    """

    def __init__(self) -> None:
        self.statements: list[StatementRecord] = []
        self._enabled = False
        self._start: float = 0.0

    def install(self, engine) -> None:
        @event.listens_for(engine, "before_cursor_execute")
        def _before(conn, cursor, statement, parameters, context, executemany):
            self._start = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def _after(conn, cursor, statement, parameters, context, executemany):
            if not self._enabled:
                return
            self.statements.append(
                StatementRecord(
                    sql=statement,
                    params=parameters,
                    seconds=time.perf_counter() - self._start,
                )
            )

    def start(self) -> None:
        self.statements = []
        self._enabled = True

    def stop(self) -> list[StatementRecord]:
        self._enabled = False
        return list(self.statements)


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class ShapeResult:
    """The measured evidence for one query shape."""

    name: str
    group: str
    breadth: str
    path: str
    params: dict
    note: str

    status_code: int = 0
    records_returned: int = 0
    total_matched: int | None = None
    empty: bool = False

    iterations: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0

    sql_statement_count: int = 0
    sql_total_ms: float = 0.0
    slowest_statement_ms: float = 0.0
    slowest_statement_sql: str = ""
    slowest_statement_plan: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, round(q * (len(ordered) - 1)))
    return ordered[index]


def _explain(engine, sql: str, params) -> list[str]:
    """Record EXPLAIN (ANALYZE, BUFFERS) for one statement.

    Runs inside a rolled-back transaction: ANALYZE really executes the
    statement, and the benchmark must not be able to mutate the corpus even
    if it is pointed at a shape that writes.
    """
    lowered = sql.strip().lower()
    if not lowered.startswith(("select", "with")):
        return ["(not a read statement; not explained)"]
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                rows = conn.exec_driver_sql(
                    f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params
                ).fetchall()
                return [row[0] for row in rows]
            finally:
                transaction.rollback()
    except Exception as exc:
        return [f"(EXPLAIN failed: {type(exc).__name__}: {exc})"]


def measure_shape(
    client: TestClient,
    engine,
    recorder: StatementRecorder,
    shape: QueryShape,
    *,
    iterations: int,
    warmup: int,
) -> ShapeResult:
    """Measure one shape and collect its plan evidence."""
    result = ShapeResult(
        name=shape.name,
        group=shape.group,
        breadth=shape.breadth,
        path=shape.path,
        params=dict(shape.params),
        note=shape.note,
    )

    def issue():
        return client.get(shape.path, params=shape.params)

    # Warm the cache so the reported p50 is steady-state rather than a
    # cold-start artifact. Cold-start cost is a different question.
    for _ in range(warmup):
        issue()

    timings: list[float] = []
    response = None
    for _ in range(iterations):
        started = time.perf_counter()
        response = issue()
        timings.append((time.perf_counter() - started) * 1000.0)

    result.status_code = response.status_code if response is not None else 0
    result.iterations = iterations
    result.p50_ms = round(_percentile(timings, 0.50), 2)
    result.p95_ms = round(_percentile(timings, 0.95), 2)
    result.min_ms = round(min(timings), 2)
    result.max_ms = round(max(timings), 2)

    if result.status_code != 200:
        body = response.text[:400] if response is not None else ""
        result.warnings.append(f"non-200 response: {result.status_code}: {body}")
        return result

    payload = response.json()
    records = payload.get("records")
    if isinstance(records, list):
        result.records_returned = len(records)
    pagination = payload.get("pagination") or {}
    result.total_matched = pagination.get("total")
    if result.records_returned == 0:
        result.empty = True
        result.warnings.append(
            "shape matched ZERO records — its timing measures an empty result "
            "and must not be quoted as evidence that the shape is fast"
        )

    # One more instrumented pass to collect the SQL profile.
    recorder.start()
    issue()
    statements = recorder.stop()
    result.sql_statement_count = len(statements)
    result.sql_total_ms = round(sum(s.seconds for s in statements) * 1000.0, 2)
    if statements:
        slowest = max(statements, key=lambda s: s.seconds)
        result.slowest_statement_ms = round(slowest.seconds * 1000.0, 2)
        result.slowest_statement_sql = " ".join(slowest.sql.split())
        result.slowest_statement_plan = _explain(engine, slowest.sql, slowest.params)

    if shape.expects_index:
        plan_text = "\n".join(result.slowest_statement_plan)
        if shape.expects_index not in plan_text:
            result.warnings.append(
                f"expected index {shape.expects_index!r} did not appear in the "
                "slowest statement's plan (it may be used by a different "
                "statement in this shape; check the recorded plan)"
            )
    return result


def _pick_identifiers(engine) -> dict[str, str]:
    """Draw real identifiers from the corpus so no shape matches nothing."""
    with engine.connect() as conn:
        formula = conn.execute(
            text(
                "SELECT (mol_formula(mol_from_smiles(smiles::cstring)))::text AS f, "
                "count(*) AS n FROM species GROUP BY f "
                "HAVING (mol_formula(mol_from_smiles(smiles::cstring)))::text IS NOT NULL "
                "ORDER BY n DESC LIMIT 1"
            )
        ).fetchone()
        smiles_row = conn.execute(
            text("SELECT smiles, public_ref FROM species ORDER BY id LIMIT 1")
        ).fetchone()
        entry_ref = conn.execute(
            text("SELECT public_ref FROM species_entry ORDER BY id LIMIT 1")
        ).scalar()
        entry_id = conn.execute(
            text("SELECT id FROM species_entry ORDER BY id LIMIT 1")
        ).scalar()
        reaction_entry_id = conn.execute(
            text("SELECT id FROM reaction_entry ORDER BY id LIMIT 1")
        ).scalar()
        reaction_entry_ref = conn.execute(
            text("SELECT public_ref FROM reaction_entry ORDER BY id LIMIT 1")
        ).scalar()
        # Reaction search matches the participant **multiset** on both sides,
        # so a query naming only reactants matches only entries that have no
        # products at all. A shape that supplies one side would therefore
        # measure an empty result. Pick a real reaction and use its actual
        # participants, choosing the entry whose most-popular reactant
        # participates in the largest number of reactions — that is the
        # popularity cliff the review describes.
        popular_entry = conn.execute(
            text(
                """
                WITH participation AS (
                    SELECT se.species_id, count(*) AS n
                    FROM reaction_entry_structure_participant p
                    JOIN species_entry se ON se.id = p.species_entry_id
                    GROUP BY se.species_id
                )
                SELECT p.reaction_entry_id, max(participation.n) AS popularity
                FROM reaction_entry_structure_participant p
                JOIN species_entry se ON se.id = p.species_entry_id
                JOIN participation ON participation.species_id = se.species_id
                GROUP BY p.reaction_entry_id
                ORDER BY popularity DESC
                LIMIT 1
                """
            )
        ).fetchone()
        rare_entry = conn.execute(
            text(
                """
                WITH participation AS (
                    SELECT se.species_id, count(*) AS n
                    FROM reaction_entry_structure_participant p
                    JOIN species_entry se ON se.id = p.species_entry_id
                    GROUP BY se.species_id
                )
                SELECT p.reaction_entry_id, max(participation.n) AS popularity
                FROM reaction_entry_structure_participant p
                JOIN species_entry se ON se.id = p.species_entry_id
                JOIN participation ON participation.species_id = se.species_id
                GROUP BY p.reaction_entry_id
                ORDER BY popularity ASC
                LIMIT 1
                """
            )
        ).fetchone()

        def sides(reaction_entry: int) -> tuple[list[str], list[str]]:
            rows = conn.execute(
                text(
                    "SELECT p.role::text AS role, s.smiles "
                    "FROM reaction_entry_structure_participant p "
                    "JOIN species_entry se ON se.id = p.species_entry_id "
                    "JOIN species s ON s.id = se.species_id "
                    "WHERE p.reaction_entry_id = :rid "
                    "ORDER BY p.role, p.participant_index"
                ),
                {"rid": reaction_entry},
            ).fetchall()
            return (
                [r.smiles for r in rows if r.role == "reactant"],
                [r.smiles for r in rows if r.role == "product"],
            )

        popular_reactants, popular_products = sides(popular_entry.reaction_entry_id)
        rare_reactants, rare_products = sides(rare_entry.reaction_entry_id)

    return {
        "formula": formula.f if formula else "C2H6",
        "formula_family_size": formula.n if formula else 0,
        "smiles": smiles_row.smiles,
        "species_ref": smiles_row.public_ref,
        "species_entry_ref": str(entry_id),
        "species_entry_public_ref": entry_ref,
        "reaction_entry_ref": str(reaction_entry_id),
        "reaction_entry_public_ref": reaction_entry_ref,
        "popular_reactants": popular_reactants,
        "popular_products": popular_products,
        "popular_participation": popular_entry.popularity,
        "rare_reactants": rare_reactants,
        "rare_products": rare_products,
        "rare_participation": rare_entry.popularity,
    }


def run(db_name: str, *, iterations: int, warmup: int,
        only: str | None = None) -> dict:
    """Run the full benchmark against ``db_name`` and return the evidence."""
    _validate_db_name(db_name)

    # Import inside the function so a missing app dependency fails loudly here
    # rather than at module import in an unrelated context.
    from app.api.app import create_app
    from app.api.config import settings
    from app.api.deps import get_db, get_write_db
    from app.api.rate_limit import reset_rate_limit_store

    # The public rate limiter keys on client IP; every benchmark request comes
    # from the same loopback host, so it would reject the run after the first
    # minute's budget and turn the measurement into a 429 latency test.
    settings.rate_limit_enabled = False
    reset_rate_limit_store()

    engine = create_engine(_sqlalchemy_url(db_name), future=True)
    recorder = StatementRecorder()
    recorder.install(engine)

    identifiers = _pick_identifiers(engine)
    shape_list = shapes(
        formula=identifiers["formula"],
        smiles=identifiers["smiles"],
        popular_reactants=identifiers["popular_reactants"],
        popular_products=identifiers["popular_products"],
        rare_reactants=identifiers["rare_reactants"],
        rare_products=identifiers["rare_products"],
        species_ref=identifiers["species_ref"],
        species_entry_ref=identifiers["species_entry_ref"],
        reaction_entry_ref=identifiers["reaction_entry_public_ref"],
    )
    if only:
        wanted = {name.strip() for name in only.split(",")}
        shape_list = [s for s in shape_list if s.name in wanted]

    app = create_app()
    session = Session(bind=engine, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_write_db] = lambda: session

    results: list[ShapeResult] = []
    with TestClient(app) as client:
        for shape in shape_list:
            print(f"  measuring {shape.name} ...", flush=True)
            results.append(
                measure_shape(
                    client, engine, recorder, shape,
                    iterations=iterations, warmup=warmup,
                )
            )
    session.close()

    environment = capture_environment(db_name)
    corpus = measure_corpus(db_name)
    engine.dispose()

    return {
        "database": db_name,
        "environment": environment,
        "corpus_row_counts": corpus,
        "corpus_identifiers": identifiers,
        "measurement": {
            "iterations_per_shape": iterations,
            "warmup_per_shape": warmup,
            "latency_is": "end-to-end HTTP through the real FastAPI app",
        },
        "shapes": [asdict(r) for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="tckdb_bench_s4")
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--only", default=None, help="comma-separated shape names")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--label", default="baseline",
        help="tag for this run, e.g. 'baseline' or 'after-fix'",
    )
    args = parser.parse_args(argv)

    evidence = run(
        args.db, iterations=args.iterations, warmup=args.warmup, only=args.only
    )
    evidence["label"] = args.label

    payload = json.dumps(evidence, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"evidence written to {args.out}")
    else:
        print(payload)

    print("\n=== summary ===")
    header = f"{'shape':46} {'n':>6} {'p50ms':>9} {'p95ms':>9} {'sqlN':>6}"
    print(header)
    for shape in evidence["shapes"]:
        flag = "  EMPTY" if shape["empty"] else ""
        print(
            f"{shape['name']:46} {shape['records_returned']:>6} "
            f"{shape['p50_ms']:>9.2f} {shape['p95_ms']:>9.2f} "
            f"{shape['sql_statement_count']:>6}{flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
