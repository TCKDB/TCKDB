# TCKDB provenance-first website plan

## Purpose

Build a scientist-friendly, provenance-first path from exact search to the
deposited evidence behind a result:

`Search → Species → Electronic-state entry → Conformer basin → Observation → Calculation / Geometry / Result`

The landing page remains public discovery and orientation. It should link into
this hierarchy rather than become a complete provenance browser.

## Information architecture

- `/species/:speciesRef` — Species identity and an explicit chooser for its
  electronic-state entries. Entries are grouped by state; independent `spe_…`
  records remain separate and linkable.
- `/species-entries/:entryRef` — One deposited electronic-state entry, with
  identity, review, availability, conformer-basin summaries, and section links.
- `/conformer-groups/:groupRef` — One torsional basin, with observations,
  calculation-stage evidence, levels of theory, reviews, and stored geometries.
- `/conformer-observations/:observationRef` — One deposition/observation and
  its provenance boundary to derived calculations.
- `/calculations/:calculationRef` — One calculation with method, software,
  workflow, review/trust, inputs, outputs, results, and true dependencies.
- `/geometries/:geometryRef` — One geometry with coordinates, validation,
  producer/consumer links, and an accessible 3D viewer plus non-WebGL fallback.

Pages use progressive drill-down: overview pages stay scannable, while
provenance and machine detail live on dedicated linked pages.

## Scientific rules

- “Ground electronic state” classifies records; it does not make them the same
  deposit. Never merge or silently choose between matching entries.
- Multiple levels of theory stay attached to their individual calculations and
  stages. Do not flatten mixed-level or composite workflows into one method.
- Draw calculation dependency graphs only from explicit dependency data; do not
  infer optimisation → frequency → single-point arrows from calculation type.
- Separate summary, provenance, review/trust, and raw machine detail into
  distinct visual layers. Stable public references remain visible and copyable.
- Formula/canonical structure is the stable identity fallback. PubChem names and
  equivalence/supersession curation are later enhancements, not inferred UI data.

## Accessibility and interaction

- Use native headings, landmarks, lists, tables, links, and disclosure elements.
- Keep one page landmark per route and provide breadcrumbs/local navigation.
- Distinguish loading, empty, malformed, unavailable, and resolved states.
- Preserve table headers on mobile; wrap long formulas, labels, and references.
- Require keyboard access, visible focus, sufficient contrast, reduced-motion
  support, and useful coordinate/raw fallbacks when WebGL is unavailable.

## Implementation sequence

1. Species overview with grouped electronic-state entries and exact-search
   routing (implemented in PRs #264–#265).
2. Conformer-basin evidence ledger (implemented in PR #263) and observation
   detail (implemented in PR #270).
3. Calculation detail with opt-in results/dependency projections.
4. Geometry detail with coordinates, validation, and the 3D viewer.
5. Complete thermo/statmech/transport detail pages and broader indexes.

Each slice must use the existing public API where possible, add only bounded
additive API projections when necessary, and pass Terra implementation, Sol
standards/spec review, CI, PR merge, image build, and Pi verification.

## First-slice acceptance criteria

An exact `CH3` search opens a species overview rather than a placeholder; the
user explicitly selects its entry; the entry links to its conformer basin; and
the basin preserves the distinctions between one basin, observations,
calculations, and stored geometries. A species with repeated ground-state
deposits is grouped by state without losing any stable entry record.

## Non-goals

No editing/submission/authentication flows, PubChem name resolution, guessed
record equivalence, dashboard aggregates, or wholesale landing-page rewrite.
