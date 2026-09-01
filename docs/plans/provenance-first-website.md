# TCKDB provenance-first website plan

## Purpose

Build a scientist-friendly, provenance-first path from exact search to the
deposited evidence behind a result:

`Search → Species → Electronic-state entry → Conformer basin → Observation → Calculation / Geometry / Result`

The landing page (the frontend SPA's `ArchiveHomePage`, not the backend's
former root-`/` HTML route, deleted along with `app/api/landing.py`) remains
public discovery and orientation. It should link into this hierarchy rather
than become a complete provenance browser.

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
3. Calculation detail with opt-in results/dependency projections
   (implemented in PR #271).
4. Geometry detail with coordinates, producer/consumer provenance, and a
   structure projection (implemented in PR #273). Validation is not
   served by the geometry endpoint; the page points at the producing and
   consuming calculations that hold it. The original SVG projection was
   replaced by a bundled 3Dmol viewer in slice 7 below.
5. Thermo, statmech and transport record sections on the species-entry
   page (implemented in PR #274). These three APIs are entry-scoped
   lists, so the records render as sections of the entry rather than as
   separately addressable detail routes. Broader indexes remain open.

6. Search results and landing formatting: chemistry rather than a base32
   ref in search results (#278), and the pre-React landing page's
   formatting rules salvaged into the SPA (#279).
7. Interactive 3D geometry viewer, superseding slice 4's SVG projection:
   bundled 3Dmol (#282), usable mouse/labels/representations (#289),
   readable and checkable numbers (#290), a stray accent bar and hidden
   canvas borders fixed (#293), and atom-picking distance/angle/dihedral
   measurement (#295).
8. Species-entry restructure: thermo's own software provenance and its
   conformer (#285), a conformer-first page (#287), an explicit statement
   of what each number counts (#288), and evidence counts narrowed to
   what they actually prove (#291).
9. Presentation tokens: a tokenised palette with a dark-mode toggle
   (#296) and a footer pinned to the viewport on short pages (#303).
10. Archive browse page across species / vdW / transition state (#301),
    with filter dropdowns built from live vocabulary rather than free
    text (#306), counts dropped from the options (#307), and provenance
    filters extended beyond transition states (#312). "Stable" is not
    used as a filter term: a minimum is stable in this archive, so the
    axis is the record kind, not a stability judgement.
11. Thermo Cp-vs-T charts on the species entry thermo tab (#317).
12. Record-page rework, in two halves. The shared shell -- one page
    width, a side table of contents at four or more sections, and facet
    chips replacing the single-string entry heading (#322). Then the
    content promotions on top of it (#323): a shared identity ->
    classification -> provenance header block, the calculation page's
    energy promoted from inside "Result" to a headline figure with a
    hartree/kJ-mol/kcal-mol/eV/cm-1 display toggle, the dependency graph
    and review history demoted below the evidence sections, and the
    single-point energy surfaced as a value on the species entry page
    rather than only as a link.

    The chips in #322 replace a heading that read
    ``species_entry_label ?? electronic_state_label ?? kind-pair``. That
    label is a discriminator built by ``species_entry_label()`` from the
    uniqueness constraint's other columns, so for a ground-state minimum
    distinguished only by stereochemistry it is correctly the single
    character "R" -- and using it as the whole heading made an entry
    render as a bare "R". The discriminator was right; presenting it as
    a title was not.

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
record equivalence, dashboard aggregates, or wholesale rewrite of the
frontend SPA's landing page (`ArchiveHomePage`).

One clarification, since slice 12 touches on it: the submission reference
is shown only to an authenticated reader. That is a read-time gate in the
API (PR #321 omits the `submission_ref` key entirely for an anonymous
caller, rather than serving it as null), not an authentication flow in the
SPA -- there is still no login UI, and a reader authenticates out of band.
The page distinguishes an omitted key from a null one: omitted renders no
row at all, because an anonymous reader is never told "not recorded" for a
question they were not allowed to ask.
