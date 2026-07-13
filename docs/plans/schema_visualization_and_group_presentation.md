# Schema Visualization & Group Presentation Plan

**Status:** planning — no diagrams produced yet, this is the roadmap.
**Date:** 2026-07-13
**Scope:** how to *show* the TCKDB schema to three different audiences (lab group meeting, day-to-day lab reference, paper reviewers) without pretending one artifact can serve all three.

## The problem

TCKDB now has 98 tables (`backend/schema.dbml`, regenerated 2026-07-13; see `.claude/skills/generate-dbml/SKILL.md`). A single auto-laid-out ER diagram of all 98 tables is unreadable — too dense to present in a group meeting, too dense to reference while working, and too dense to put in a paper. The mistake to avoid is treating "generate one big ER diagram" as a solved problem; instead, three different audiences need three different artifacts, produced by three different tools, and none of them should be an auto-generated full-schema ER dump.

## Three tools, one per need

### (a) Live group-meeting walkthrough → hand-curated conceptual slides

Audience: the lab, verbally, synchronously. Goal: convey the *mental model*, not the DDL.

- Hand-draw (not auto-generate) a small set of conceptual diagrams:
  - One **four-bucket boxes-and-arrows** overview (Identity / Provenance / Result / Curation) — the single most load-bearing idea in the system (see `docs/guides/core_concepts.md`).
  - 6–7 **per-domain mini-diagrams**, each showing only the handful of tables relevant to one domain (species/conformers, calculations, statmech/thermo/kinetics/transport, PDep networks, provenance/literature, trust/review) with real relationships drawn by hand, not auto-layout.
- Tooling: Excalidraw, draw.io, or D2 — whichever is fastest for hand-curated boxes-and-arrows with text callouts. Excalidraw/draw.io suit a live walkthrough with annotations; D2 suits treating the diagram as a small versionable text file if it will be redrawn often as the schema evolves.
- This is **not** DBML-derived. The point of a group-meeting slide is to omit 90% of the columns and most of the tables — auto-layout tools fight this goal.

### (b) Browsable lab reference → self-hosted Azimutt

Audience: any lab member, asynchronously, digging into "what FKs touch `calculation`?" or "show me only the kinetics tables."

- Stand up **Azimutt** (MIT-licensed, `docker-compose`) self-hosted, connected to the local `tckdb_dev` Postgres instance (or point it at `schema.dbml` directly if a live DB connection isn't wanted for a given session).
- Save **per-domain layouts** in Azimutt (e.g., a "species & conformers" layout, a "calculations" layout, a "PDep network" layout) so nobody has to re-hide 90 tables every time they open it.
- Why self-hosted rather than the hosted free tier: Azimutt's hosted free tier caps layouts at **10 tables**, which is far below what's needed for any useful per-domain view of a 98-table schema. Self-hosting removes that cap.

### (c) Publication figures → hand-curated, continuing the paper's existing style

Audience: reviewers and readers of the manuscript.

- Continue the paper's existing **conceptual-table style** (prose + curated tables in `2_methods.tex`), not an auto-generated ER dump. An ER-dump figure at 98-table scale is unreadable in a print column and would read as "we ran a tool" rather than "here is the argument for this design."
- Tooling: draw.io or TikZ, hand-curated per figure — see the 7-figure list below.

## Azimutt vs dbdiagram.io — these solve different jobs

It's easy to conflate these because both consume DBML, but they are not substitutes for each other:

| | dbdiagram.io | Azimutt |
|---|---|---|
| Input | DBML (paste/import) | DBML, or live DB connection |
| Output | One auto-laid-out canvas | Interactive explorer, multiple saved views |
| Best for | A quick static picture, small-to-medium schema | Large schemas, ongoing reference use |
| Per-domain views | No — one canvas, manual pan/zoom | Yes — saved per-domain layouts |
| Show-only-relevant-columns | No | Yes |
| FK-path navigation ("what connects to X") | Manual visual tracing | Built-in navigation |
| Live-DB introspection | No (DBML/SQL import only) | Yes |
| Hosted free-tier limit | N/A (single canvas) | 10 tables/layout (self-host to remove) |

Use dbdiagram.io for a quick one-off "let me just see the current DBML rendered" sanity check (e.g., right after running `generate-dbml`). Use Azimutt for the standing lab reference.

## Group-meeting outline (7 slides)

1. **"Every table is one of four things"** — Identity / Provenance / Result / Curation. The organizing idea; everything else is an instance of it.
2. **Species & conformers** — identity hierarchy, torsional-basin conformer grouping (DR-0005).
3. **Calculation as evidence hub** — the calculation table as the append-only hub that DAG edges, SP/opt/freq results, and geometries hang off of.
4. **Scientific products** (thermo / statmech / kinetics / transport) — no stored "preferred" flag; the "best value" is a read-time selection over append-only results, not a curator-frozen field.
5. **Pressure-dependent networks** — the PDep/network model (DR-0001), flagged as architecture-only (no production data yet).
6. **Provenance & references** — software/software_release, workflow_tool, level_of_theory, literature/author.
7. **Trust overlay** — review and machine-review as an immutable overlay on top of identity/provenance/result, never mutating them.

## Paper figure list (7 figures)

1. **Four-bucket architecture** — Identity / Provenance / Result / Curation, with one example table per bucket.
2. **Species/conformer identity hierarchy** — species → species_entry → conformer_group, torsional-basin matching as the cross-source identity key.
3. **Calculation-as-evidence-hub** — calculation table with its DAG edges and result children (calc_sp_result, calc_opt_result, calc_freq_result). `paper/18__TCKDB/figures/calc_prov_diagram.png` already exists, is currently unused in the manuscript, and is a good starting point for this figure.
4. **Scientific-product family** — thermo/statmech/kinetics/transport siblings and how a "best value" is selected at read time rather than stored.
5. **PDep network model** — channels, kinetics per channel, network state — labeled clearly as architecture without production data.
6. **Provenance & reference layer** — software/software_release, workflow_tool/workflow_tool_release, level_of_theory, literature/author.
7. **Submission/review/trust overlay** — upload → submission → machine/human review → trust label, emphasizing the overlay never mutates the underlying append-only rows.

## First steps

1. Regenerate `backend/schema.dbml` — **done** (2026-07-13, 86 → 98 tables; see the `generate-dbml` skill and the housekeeping PR that shipped it).
2. Stand up Azimutt against local Postgres (`tckdb_dev`) via `docker-compose`; save an initial per-domain layout for species/conformers as a pilot before doing the rest.
3. Draft the four-bucket figure first — it is both slide 1 of the group-meeting deck and figure 1 of the paper, so it pays off twice and de-risks the rest of the figure list.
