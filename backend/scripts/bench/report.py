"""Render benchmark evidence JSON into the published markdown report.

The report is generated, never hand-written. A transcribed number is a number
nobody can re-derive, and the whole point of this stage is that every
published figure can be traced to a measurement that was actually taken.

Usage::

    python -m bench.report --before docs/benchmarks/baseline.json \
                           --after  docs/benchmarks/after_fix.json \
                           --corpus docs/benchmarks/corpus_manifest.json \
                           --out    docs/benchmarks/README.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _shape_index(evidence: dict) -> dict[str, dict]:
    return {shape["name"]: shape for shape in evidence.get("shapes", [])}


def _environment_block(env: dict) -> list[str]:
    lines = [
        "## Measured environment",
        "",
        "> **These are not the deployed instance's numbers.** TCKDB's deployment",
        "> target is an arm64 Raspberry Pi. Everything below was measured on the",
        "> x86_64 workstation described here. No Raspberry Pi measurement was",
        "> taken, and none of these figures may be extrapolated to Pi",
        "> performance.",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| Hardware class | {env.get('hardware_class')} |",
        f"| CPU | {env.get('cpu_model')} |",
        f"| Architecture | `{env.get('cpu_arch')}` |",
        f"| Logical cores | {env.get('cpu_logical_cores')} |",
        f"| RAM | {env.get('ram_gib')} GiB |",
        f"| Kernel | {env.get('kernel')} |",
        f"| Python | {env.get('python')} |",
        f"| PostgreSQL | {env.get('postgresql')} |",
        f"| RDKit (Python) | {env.get('rdkit_python')} |",
        f"| RDKit cartridge | {env.get('rdkit_cartridge')} |",
        f"| SQLAlchemy | {env.get('sqlalchemy')} |",
        f"| Measured at | {env.get('measured_at')} |",
        "",
        "PostgreSQL tuning in force (server defaults unless noted):",
        "",
        "| Setting | Value |",
        "|---|---|",
    ]
    for name, value in sorted((env.get("postgresql_tuning") or {}).items()):
        lines.append(f"| `{name}` | {value} |")
    lines.append("")
    return lines


def _corpus_block(corpus: dict) -> list[str]:
    lines = [
        "## The corpus",
        "",
        "Built by `backend/scripts/bench/build_corpus.py` into a disposable",
        "`tckdb_bench_*` database. Molecules are RDKit-generated and sanitized,",
        "so the cartridge's GiST index and the `mol_formula` expression index",
        "are exercised over real chemistry rather than strings.",
        "",
        "### Cardinalities scaled from the measured production inventory",
        "",
        "| Table | Production (55 species) | Per species | Corpus |",
        "|---|---:|---:|---:|",
    ]
    inventory = corpus.get("production_inventory", {})
    ratios = corpus.get("production_ratios_per_species", {})
    measured = corpus.get("measured_row_counts", {})
    for table in sorted(inventory):
        lines.append(
            f"| `{table}` | {_fmt(inventory[table])} | {ratios.get(table)} | "
            f"{_fmt(measured.get(table, 0))} |"
        )
    lines += ["", "### Full measured corpus", "", "| Table | Rows |", "|---|---:|"]
    for table, count in sorted(measured.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{table}` | {_fmt(count)} |")

    lines += [
        "",
        "### Declared deviations from production ratios",
        "",
        "Every deviation states its effect on which query shapes remain",
        "representative. A reduction without a stated effect would make every",
        "number measured against it unciteable.",
        "",
    ]
    for deviation in corpus.get("deviations", []):
        production = deviation["production_ratio_per_species"]
        production_text = "not measured" if production is None else str(production)
        lines += [
            f"#### `{deviation['table']}`",
            "",
            f"- **Production ratio:** {production_text}",
            f"- **Built ratio:** {deviation['built_ratio_per_species']}",
            f"- **Reason:** {deviation['reason']}",
            f"- **Effect:** {deviation['effect_on_representativeness']}",
            "",
        ]

    shortfalls = corpus.get("measured_shortfalls", [])
    if shortfalls:
        lines += [
            "### Measured shortfalls",
            "",
            "Tables that finished below their scaled target. Detected by",
            "re-reading the built database, not asserted from the build plan.",
            "",
            "| Table | Planned | Built | Fraction | Reason |",
            "|---|---:|---:|---:|---|",
        ]
        for shortfall in shortfalls:
            lines.append(
                f"| `{shortfall['table']}` | {_fmt(shortfall['planned'])} | "
                f"{_fmt(shortfall['built'])} | {shortfall['fraction_of_plan']:.1%} | "
                f"{shortfall['reason']} |"
            )
        lines.append("")
    return lines


def _results_table(before: dict, after: dict | None) -> list[str]:
    before_shapes = _shape_index(before)
    after_shapes = _shape_index(after) if after else {}

    lines = [
        "## Measured query shapes",
        "",
        "`p50`/`p95` are end-to-end HTTP latency through the real FastAPI",
        "application: routing, service layer, every SQL round-trip, and response",
        "serialization. `sqlN` is the number of SQL statements one request",
        "issued — the diagnostic that distinguishes \"needs an index\" from",
        "\"issues one query per candidate row\".",
        "",
    ]
    if after_shapes:
        before_iterations = before.get("measurement", {}).get(
            "iterations_per_shape"
        )
        after_iterations = after.get("measurement", {}).get("iterations_per_shape")
        if before_iterations != after_iterations:
            lines += [
                "> **The two runs used different sample sizes** — "
                f"`before` took {before_iterations} iterations per shape "
                f"(warmup {before.get('measurement', {}).get('warmup_per_shape')}), "
                f"`after` took {after_iterations} "
                f"(warmup {after.get('measurement', {}).get('warmup_per_shape')}). "
                "Percentiles from different sample sizes are not strictly",
                "> comparable, and the two runs also saw different background",
                "> load on a shared workstation. Treat small deltas as noise.",
                "> The differences that are *categorical* rather than",
                "> quantitative — an HTTP status changing, or a statement count",
                "> changing by an order of magnitude — are the ones this table",
                "> is evidence for.",
                "",
            ]
        lines += [
            "| Shape | matched | p50 before | p50 after | p95 before | p95 after "
            "| sqlN before | sqlN after |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for name, shape in before_shapes.items():
            fixed = after_shapes.get(name, {})
            lines.append(
                f"| `{name}` | {_fmt(shape.get('total_matched'))} | "
                f"{_status_or_ms(shape)} | {_status_or_ms(fixed)} | "
                f"{_p95(shape)} | {_p95(fixed)} | "
                f"{_fmt(shape.get('sql_statement_count'))} | "
                f"{_fmt(fixed.get('sql_statement_count'))} |"
            )
    else:
        lines += [
            "| Shape | breadth | matched | p50 ms | p95 ms | sqlN |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for name, shape in before_shapes.items():
            lines.append(
                f"| `{name}` | {shape.get('breadth')} | "
                f"{_fmt(shape.get('total_matched'))} | "
                f"{_status_or_ms(shape)} | {_p95(shape)} | "
                f"{_fmt(shape.get('sql_statement_count'))} |"
            )
    lines.append("")

    flagged = [
        shape
        for shape in (after_shapes or before_shapes).values()
        if shape.get("warnings")
    ]
    if flagged:
        lines += [
            "### Shapes carrying a warning",
            "",
            "A shape that matched nothing, or returned a non-200, is reported",
            "here rather than being quoted as a fast result.",
            "",
        ]
        for shape in flagged:
            lines.append(f"- **`{shape['name']}`**")
            for warning in shape["warnings"]:
                lines.append(f"  - {warning}")
        lines.append("")
    return lines


def _status_or_ms(shape: dict) -> str:
    if not shape:
        return "—"
    if shape.get("status_code") not in (200, 0, None):
        return f"**HTTP {shape['status_code']}**"
    return f"{shape.get('p50_ms', 0):,.1f}"


def _p95(shape: dict) -> str:
    if not shape:
        return "—"
    if shape.get("status_code") not in (200, 0, None):
        return f"**HTTP {shape['status_code']}**"
    return f"{shape.get('p95_ms', 0):,.1f}"


def _plans_block(evidence: dict) -> list[str]:
    lines = [
        "## Recorded query plans",
        "",
        "`EXPLAIN (ANALYZE, BUFFERS)` for the slowest SQL statement each shape",
        "issued, captured inside a rolled-back transaction.",
        "",
    ]
    for shape in evidence.get("shapes", []):
        plan = shape.get("slowest_statement_plan") or []
        if not plan:
            continue
        lines += [
            f"### `{shape['name']}`",
            "",
            f"Slowest statement: {shape.get('slowest_statement_ms', 0):.1f} ms of "
            f"{shape.get('sql_total_ms', 0):.1f} ms total SQL across "
            f"{shape.get('sql_statement_count', 0)} statements.",
            "",
            "```sql",
            shape.get("slowest_statement_sql", "")[:2000],
            "```",
            "",
            "```",
            *plan,
            "```",
            "",
        ]
    return lines


#: Defects the benchmark measured that were NOT fixed in Stage 4, each with
#: the reason. Publishing the wins and omitting the rest would make this file
#: advertising rather than evidence.
KNOWN_UNFIXED: tuple[dict[str, str], ...] = (
    {
        "shape": "thermo_search_broad",
        "symptom": (
            "~1,330 SQL statements and ~1 second for a 50-record page over 171 "
            "matches."
        ),
        "cause": (
            "`thermo_search` is a *composed* search: it walks every page of "
            "`species_search` to collect the complete candidate set, then calls "
            "the full `get_species_thermo` service once per surviving species "
            "entry, at roughly 14 SQL statements each. The cost is "
            "proportional to the number of matches, not to the page. "
            "`kinetics_search` has the same shape over `reaction_search`."
        ),
        "why_not_fixed": (
            "Collapsing it would mean rewriting the composed searches as single "
            "SQL queries — a large change to the most intricate ranking logic in "
            "the read layer, with real regression risk to the D9 selection "
            "chain. The owner's Stage 4 direction was explicitly to add a "
            "bounded analytics surface for quantitative dataset construction "
            "rather than to keep growing the transactional searches, so the "
            "analytics endpoints are the supported path for this question. The "
            "defect is recorded here, measured, rather than left to be "
            "rediscovered."
        ),
    },
    {
        "shape": "structure_search_substructure",
        "symptom": "~550 ms for a substructure query matching 12,649 entries.",
        "cause": (
            "Not investigated in depth. The RDKit GiST index is used, but a "
            "substructure match over a large candidate set still rechecks many "
            "molecules."
        ),
        "why_not_fixed": (
            "No plan-backed diagnosis was produced, so no fix and no index is "
            "proposed. Recorded as an open question rather than guessed at."
        ),
    },
)


#: Defects this file previously published as measured-but-unfixed, and that
#: have since been fixed. They stay on the page: a defect that is recorded
#: while it is open and deleted the moment it closes leaves a reader unable to
#: tell a problem that was solved from one that was never found.
#:
#: This block is the *only* place the before-fix figures appear. The results
#: table above is regenerated from the current measurement pair, so it shows
#: the post-fix statement count and nothing of what it replaced; the 1,060
#: statements and the 21-to-6 marginal cost survive here or nowhere.
RESOLVED_SINCE: tuple[dict[str, str], ...] = (
    {
        "shape": "calculation_search_by_lot",
        "symptom": (
            "1,060 SQL statements for a 50-record page, even though the hard "
            "503 was fixed and the candidate set was already sliced in SQL."
        ),
        "cause": (
            "Per-*page* work: the shared `build_record` helper issued roughly "
            "21 queries for each of the 50 records it materialized. Bounded by "
            "`limit`, so it never grew with corpus size."
        ),
        "fix": (
            "Two changes, neither of them the record-builder restructure this "
            "entry once said would be needed. The fifteen single-column probes "
            "behind `available_sections` and the evidence-provenance block are "
            "one calculation id each, so they are now columns of one "
            "`SELECT` instead of fifteen round trips — same predicates, same "
            "results. And the search loaded the page's `calculation` rows one "
            "`session.get` at a time; it now loads them in one statement and "
            "indexes them by id. Marginal cost per record on this corpus: 21 "
            "statements to 6. Guarded by "
            "`backend/tests/services/scientific_read/"
            "test_record_builder_statement_cost.py`, which pins the slope "
            "rather than the total."
        ),
        "still_open": (
            "Three statements per record remain — the owner block, the "
            "combined probe and the submission link. Removing those does mean "
            "handing the shared record builder prefetched data, and is still "
            "worth doing on its own evidence rather than folded in here."
        ),
    },
)


def _resolved_block() -> list[str]:
    lines = [
        "## Measured defects that have since been fixed",
        "",
        "Recorded here rather than deleted, so a defect that was found and",
        "solved stays distinguishable from one that was never found.",
        "",
    ]
    for defect in RESOLVED_SINCE:
        lines += [
            f"### `{defect['shape']}`",
            "",
            f"- **Symptom:** {defect['symptom']}",
            f"- **Cause:** {defect['cause']}",
            f"- **Fix:** {defect['fix']}",
            f"- **Still open:** {defect['still_open']}",
            "",
        ]
    return lines


def _unfixed_block() -> list[str]:
    lines = [
        "## Measured defects that were NOT fixed",
        "",
        "Each of these was reproduced at corpus scale and is left open, with the",
        "reason stated. They are listed so the numbers above are read in",
        "context.",
        "",
    ]
    for defect in KNOWN_UNFIXED:
        lines += [
            f"### `{defect['shape']}`",
            "",
            f"- **Symptom:** {defect['symptom']}",
            f"- **Cause:** {defect['cause']}",
            f"- **Why not fixed:** {defect['why_not_fixed']}",
            "",
        ]
    return lines


def build_report(before: dict, corpus: dict, after: dict | None) -> str:
    env_source = after or before
    lines = [
        "# Stage 4 query-performance evidence",
        "",
        "Generated by `backend/scripts/bench/report.py`. Do not edit by hand —",
        "regenerate it from the measurement JSON so every figure stays traceable",
        "to a run that actually happened.",
        "",
        "Reproduce with:",
        "",
        "```bash",
        "cd backend",
        "export DB_USER=tckdb DB_PASSWORD=tckdb DB_HOST=127.0.0.1 DB_PORT=5432",
        "python scripts/bench/build_corpus.py --db tckdb_bench_s4 --species 50000 \\",
        "    --manifest docs/benchmarks/corpus_manifest.json",
        "python scripts/bench/run_benchmark.py --db tckdb_bench_s4 \\",
        "    --out docs/benchmarks/after_fix.json --label after-fix",
        "python scripts/bench/build_corpus.py --db tckdb_bench_s4 --drop",
        "```",
        "",
    ]
    lines += _environment_block(env_source.get("environment", {}))
    lines += _corpus_block(corpus)
    lines += _results_table(before, after)
    lines += _unfixed_block()
    lines += _resolved_block()
    lines += _plans_block(after or before)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=None)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    before = json.loads(args.before.read_text())
    corpus = json.loads(args.corpus.read_text())
    after = json.loads(args.after.read_text()) if args.after else None

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_report(before, corpus, after), encoding="utf-8")
    print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
