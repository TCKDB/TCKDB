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

13. Whole-site review pass. A Fable reviewer walked every page through
    headless Chrome for visual design, accessibility, right-data and
    redundancy, and the 24 findings were fixed in file-scoped batches
    (#342-#353). Two were fabricated identity: unplottable Cp series
    merging across conformer groups, and a grouped thermo card showing
    provenance from a record outside its group. The rest were
    redundancy (levels of theory repeated, review status twice),
    contrast tokens, three table-of-contents test races, and a
    transition-state browse row that led to the species route. That last
    one produced the transition-state entry page (#352): identity by
    reaction rather than SMILES, the saddle-point geometry led with and
    each IRC direction behind a disclosure showing its point count, and
    a geometry cited by several calculations shown once -- plus a real
    not-found route in place of the silent home-page fallback.

14. Second review pass, two pages deep. Two independent reviewers (a
    scientist reading the record, an information designer) walked the
    calculation detail page for optimisations and, separately, the
    transition-state entry page and browse list, each through headless
    Chrome against the live site. Calculation page (#358): identity ->
    classification -> provenance header with an "Optimisation of C2H4"
    h1, a new read-model conformer block so one of eighteen ethylene
    optimisations says which conformer and which stage it belongs to,
    related calculations as one fixed-subject sentence per edge (the
    old Relationship/Role table read backwards), each fact stated once,
    count tiles removed, a per-page conformer lookup batched after the
    statement-cost gate was extended to see it. Transition-state entry
    (#357): the reaction equation as the h1 with the ARC label demoted
    to a facet, the imaginary frequency served and rendered as the first
    evidence statement, IRC reported as "ran, endpoint identity not
    deposited" (no entry in the archive carries a validation-evidence
    record), the trust label beside the review pill with the rubric's
    "IRC evidence present" check relabelled to what it tests, other
    saddle points for the same reaction, and the References disclosure
    CSS moved out of the species stylesheet so it no longer depended on
    which page loaded first. Transition-state browse (#356): level of
    theory and software per row so same-reaction deposits are
    distinguishable, stretched-link rows with the ref selectable,
    reactant/product SMILES and family filters on both browse and search,
    and a species-page link to its transition states. Four data facts
    surfaced and left alone: no validation evidence is ever deposited by
    the ARC uploads; every optimisation parses as two steps; path-search
    energies on one entry look mis-united; hydrazine reactions have no
    family.

15. Visual consistency programme. Two reviewers measured the live site
    through headless Chrome, computed styles over 25 routes: 49 distinct
    font sizes, monospace carrying 58 percent of all text, one
    uppercase-tracked mono voice doing seven jobs, six h1 rules, seven
    disclosure styles, headings fake-bold because only the 400 and 600
    faces are loaded, notes at up to 198 characters per line. The answer
    was a diet, not a redesign: keep the three Plex faces and the tokens,
    define a twelve-step type scale, an eight-step spacing scale and one
    primitive per role (key/value list, data table, disclosure, pill,
    card, note, data run), then move every page onto them. Four PRs:
    foundations (#364), index pages (#365), species entry and its
    sections (#366), record pages (#367). Two shared defects surfaced
    only under review: route-chunk CSS leakage (a component importing
    the design-system sheet emitted a second copy, so pill colour
    depended on which page loaded first), and the narrow layout's
    content column taking its max-content width, so tables widened the
    page instead of scrolling. Both were fixed at the root and pinned by
    tests that measure rendered style or emitted CSS, not class names.
    The one deliberate visible change is the h1 cap, 88 to 102px down to
    64px, one token to revisit.
16. Re-review and residuals. The same two reviewers re-measured the
    site after the four PRs: font sizes 49 to 36, monospace 58 to 47
    percent of text, one disclosure style, weights 400 and 600 only.
    What remained was fixed in two PRs, record pages (#371) and species
    entry, browse and chrome (#372), each verified by a reviewer who
    rebuilt main and the branch and measured both. Two lessons worth
    keeping. Wrapping fixes must be measured, not reasoned: a break
    opportunity after every underscore and a keep-all rule both looked
    right in the diff and were inert or harmful on the page; the real
    fix for a reference wrapping mid-token was a wider key/value
    column. And a guard has to be watched failing: three tests in this
    round passed with their rule deleted, an allowlist grew silently,
    and a class named only in a comment counted as used. Each was
    proven red under mutation before merge. On the way, a user report
    turned out to be a route remount introduced in slice 12: the entry
    page and its section routes rendered different element types, so
    every tab click unmounted the page, refetched three requests, and
    tripped the anonymous rate limit on Back. Fixed by rendering one
    element for both routes and deduplicating in-flight requests with
    a five-minute cache (#370), verified live: a tab click now issues
    no request at all. Open for the design owner: the conformer card's
    references box keeps a third disclosure style because it is the
    tested fix for a double border, and a reviewer proposed promoting
    it to a named inset modifier.
17. Owner walk-through fixes. The owner read the calculation, geometry
    and observation pages and named what still looked wrong. Two PRs,
    on disjoint files, each reviewed by measurement. Record header
    (#375): the SMILES and InChIKey copy buttons lost in slice 16 are
    back on every page that composes the header; the conformer card's
    references box moved onto a named inset disclosure, and the
    references box's own lighter border was dropped, so the site has
    two box styles and a test that no page restyles a disclosure.
    Calculation and geometry pages (#374): the measurements panel sits
    directly under the 3D picture sharing its left edge (it had centred
    itself in the wide column, 300px to the right); the electronic
    energy comes down from 24px to the ordinary data step; the evidence
    box uses label-above-value rows in one column. One misreading was
    caught by a builder: the bare "R" that linked to the species entry
    is not a submitter-typed label but the server-computed stereo
    facet, so it now reads "C9H9 · R enantiomer" through the existing
    facet helper. Two fields really are submitter-typed and still
    render, transition-state labels ("TS0") and conformer labels
    ("conformer_1"); the owner has not yet ruled on them. Backend
    follow-up: the conformer-observation payload carries no formula, so
    that page falls back to the entry ref until it does.

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
