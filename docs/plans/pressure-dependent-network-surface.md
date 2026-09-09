# Pressure-dependent network surface — implementation plan

Status: **PRs 0, 1 and 2 are merged and deployed; PRs 3 and 4 remain.** See
"Build status" below. Base: `main` at `45dc4afe`
(the reaction-entry-page and Arrhenius-chart plan is fully landed — PRs 0–4 of
`docs/plans/reaction-entry-page.md` are merged). Sibling plans:
`docs/plans/provenance-first-website.md` (running house rules),
`docs/plans/reaction-entry-page.md` (the `NetworkSection` this plan replaces
with a real page, and the `ArrheniusChart`/`kineticsTable.ts` this plan must
either reuse or explicitly decline to extend).

## Build status

| PR | What | State |
|---|---|---|
| 0 | Design mock (`docs/plans/mocks/network-entry.html`) | merged #441 |
| 1 | Server-side Chebyshev/PLOG evaluation (`app/chemistry/network_kinetics_eval.py`) | merged, deployed. **Per-record only** — the batch endpoint PR 4 needs was deliberately not built |
| 2 | Network record page (identity, evidence, reactions, review) | merged #442, deployed |
| — | Follow-ups found on the deployed page | #443 (refs were inert `<code>`, now linked), #444 (an unreadable solve blanked the whole page, now degrades with an explicit note) |
| 3 | Network diagram | not started |
| 4 | k(T,P) chart | not started — **blocked on the batch endpoint** |

Two things a PR 3/PR 4 builder should not inherit from the text below.

**The "0 of 7 states have reachable energies" finding in §0 is wrong**, and so
is PR 2's red-first criterion that repeats it. Measured on the deployed
archive: the solve carries a relative electronic-only energy for **7 of 7**
states and a forward/reverse barrier for **4 of 21** channels. The
species-level thermo gap is real and separate (2 of 9 participants carry a
thermo record). The shipped page states both, and its coverage sentence is
computed from the payload, so it was never at risk of asserting the wrong
number — but the plan's prose still says otherwise. Keeping the page
topology-only remains right (mixed conventions, sparse barriers); "no PES
ever" and "no PES yet" are just different claims.

**`reactions/search?network_ref=…` does not exist** (422). Anything below
that assumes it does needs a different join.

## 0. The gap, restated after measurement

Today a pressure-dependent network has exactly one frontend surface: a table
row inside `NetworkSection` on `ReactionEntryPage.tsx`
(`frontend/src/pages/ReactionEntryPage.tsx:349`), showing name, `net_…` ref as
plain `<code>`, T/P range, channel count, review pill — no link, because
there is nowhere to link to. `/networks/:ref` does not exist as a route.
Every number below is a live measurement against `https://tckdb.homecalvin.com`,
anonymous, taken 2026-09-08, against the single network in the archive:
`net_o6bt63kjeyvhvxx26w6kdi433a` ("hydrazine").

## 1. Measured facts

**Scale.** `GET /scientific/networks/net_o6bt63kjeyvhvxx26w6kdi433a?include=states,channels,kinetics`
→ `evidence_summary`: `species_count: 10, reaction_count: 6, state_count: 7,
channel_count: 21, solve_count: 1, kinetics_count: 42,
source_calculation_count: 25, has_chebyshev: true, has_plog: true,
has_point_kinetics: false`. One `network_solve` (`me_method: "modified strong
collision"`), one `software_release`, one `workflow_tool_release`, one
`literature`. That is more internal structure than a species entry ever
carries on its own record page.

**States are a graph, and the join chemists want is already half-built.**
Each of the 7 `states[]` carries `kind` (`well` | `bimolecular`),
`composition_hash` (a server-computed 65-char hash — stable, not depositor
text), a top-level `label` (depositor free text, **null on every one of the 7
states**), and `composition.state_label` — a **server-composed** string built
from each participant's `canonical_smiles` + `species_entry_label` (itself
server-derived, per house rule) + stoichiometry, e.g. `"N=N (Z) + [H][H]"`,
`"2 [NH2]"`, `"[NH-][NH3+]"`. So the "just disconnected molecules" complaint
is fixed at the data layer already — `composition.state_label` is safe,
non-arbitrary, and human-readable. Nobody has wired it into a UI.

**Channels are edges, keyed by depositor text with no public ref at all.**
21 `channels[]`, each: `channel_key` (e.g. `"channel_1"`), `kind`
(`isomerization` | `association` | `exchange`, enum-backed), `mechanism`
(`elementary`, enum-backed), `source_state_composition_hash`,
`sink_state_composition_hash`, `has_kinetics`, `microreactions[]` (each
carrying a real `rxe_…` and `tse_…`). Checked the model
(`backend/app/db/models/network_pdep.py:125`): **`NetworkChannel` has no
public-ref column** — unlike every other identity table in the schema. The
only schema-enforced unique identity for a channel is
`UniqueConstraint("network_id", "channel_key")`; `(source_state_id,
sink_state_id)` is explicitly *not* unique — the model's own comment says
"distinct mechanistic pathways may have the same macroscopic source and
sink." On this network the 21 `(source, sink)` pairs happen to be unique
(verified: 21 channels, 21 unique directed pairs, and 21 unique undirected
pairs too — this network is a complete graph on 7 nodes, an artifact of
scale, not a guarantee), so today a `source_hash + sink_hash` composite would
work as an anchor, but the model does not promise that stays true.
**`channel_key` is depositor-supplied free text** (`backend/app/schemas/workflows/network_pdep_upload.py:715`,
`key: str = Field(min_length=1)`, no enum, no server generation) — the exact
shape the house rule warns about ("TS0", "conformer_1"). It is also,
inconveniently, the *only* stable per-channel identifier the schema offers.

**`network.name` and `.description` are depositor free text, not curated.**
Checked `backend/app/schemas/workflows/network_upload.py:49`: both
`name: str | None = None` and `description: str | None = None`, normalized
but never validated against a vocabulary. `NetworkSection` already renders
`network.name ?? "not recorded"` today — a pre-existing product decision this
plan does not reverse, but a new page must not treat `name`/`description` as
if curated: no bare assertion, presented as depositor-authored prose (a
`Disclosure` or explicitly labelled block), same as a literature abstract.

**The unfiltered-search gap is real.** `GET /scientific/networks/search`
(no params) → `422 missing_filter`: `"at least one of ['basis',
'has_channels', 'has_chebyshev', 'has_kinetics', 'has_plog',
'has_point_kinetics', 'has_reactions', 'has_solves', 'has_species',
'has_states', 'method', 'network_ref', 'pressure_max', 'pressure_min',
'reaction_entry_ref', 'reaction_ref', 'software', 'software_version',
'species_entry_ref', 'species_ref', 'temperature_max', 'temperature_min',
'workflow_tool', 'workflow_tool_version'] must be supplied"` — 15 named
filters, none optional-in-aggregate. Species and reaction search share this
shape but also have `species/browse` and (as of the reaction-entry-page plan)
`reactions/browse` as open catalogues. No `networks/browse` exists.

**PES energy coverage — measured, not assumed: 0 of 7 states.** The network
payload carries no state energies (states/channels/kinetics sections have no
`energy` field anywhere). Reaching them means joining each state's
participant species entries to their scientific products. Pulled thermo
(`GET /scientific/species-entries/{ref}/thermo`) and statmech
(`.../statmech`) for all **9 unique species entries** referenced across the
7 states:

| species entry | SMILES | thermo record? | `h298_kj_mol` |
|---|---|---|---|
| `spe_7ioiqvdqm6cyumgnrammbhefum` | `[H][H]` | yes | 0.257 |
| `spe_knzmwhplnwnpodfp6frphrl7ei` | `[H]` | yes | 215.20 |
| `spe_wi6mz65sb47vzsyop3tqrqrcai` | `NN` | **no** | — |
| `spe_zsvxdy7fbktworbhss2da62doi` | `[NH-][NH3+]` | **no** | — |
| `spe_cft35qrkqphdifcfqlcenqgdau` | `N=N` (Z) | **no** | — |
| `spe_qefrbgmpyryylyocpghhc2ikki` | `N=N` (E) | **no** | — |
| `spe_fvlbwfuoauoeknq6l5esaozqai` | `[N-]=[NH2+]` | **no** | — |
| `spe_c4ty3ixcmyuljgqfs73pdecmte` | `[NH]N` | **no** | — |
| `spe_gzk56q4jegyyg7ylbcdqb2xvka` | `[NH2]` | **no** | — |

2 of 9 species entries (22%) have any thermo record — both are the trivial
atomic/diatomic species (`[H]`, `[H][H]`). Every polyatomic species involved
in this network — including both wells (`NN`, `[NH-][NH3+]`) and every
non-H2 bimolecular partner — has zero thermo records. Statmech records exist
for all 9 (4–5 each) but **carry no energy field at all**: the full key set
on a `statmech` object is `statmech_treatment, rigid_rotor_kind, point_group,
external_symmetry, is_linear, uses_projected_frequencies, optical_isomers,
rotational_constant_{a,b,c}_cm1, frequency_scale_factor_value` — no
electronic energy, no E0, no ZPE. So statmech cannot substitute for thermo
here even in principle from what the read surface serves. Joining
per-state: **0 of 7 states have full-participant thermo coverage** — even
the best case (a bimolecular state pairing `[H][H]`, which has thermo, with
a partner that doesn't) fails because a state's relative energy needs every
participant. A PES cannot be drawn on this network today; the correct
statement is "0% state coverage," not "mostly missing."

**k(T,P) parameters ARE fully retrievable — the gap is evaluation, not
access.** `GET /scientific/network-kinetics/search?network_ref=…&include=coefficients,plog&limit=50`
returns **all 42** kinetics records in one request, with the Chebyshev
coefficient matrix (confirmed 6×4 = 24 coefficients for one record) and the
full PLOG entry table (confirmed 5 pressure-keyed modified-Arrhenius sets:
`{pressure_bar, entry_index, a, a_units, n, ea_kj_mol}`) inlined per record —
21 chebyshev + 21 plog rows populated in that single call, response body
~165 KB. So there is no N+1 problem and nothing is hidden; a client already
has everything needed to evaluate k(T,P) for the whole network in one
request. What's missing: **no evaluation endpoint anywhere** — grepped
`backend/app/api/routes/scientific/networks.py`: the only routes are
`/networks/search`, `/networks/{ref}`, `/network-solves/search`,
`/network-solves/{ref}`, `/network-kinetics/search`,
`/network-kinetics/{ref}` — none takes a `T`/`P` and returns `k`. And **no
client-side Chebyshev/PLOG math exists either**: the reaction entry page's
existing `ArrheniusChart` explicitly *excludes* PLOG/Chebyshev/falloff/
third-body records from its k(T) plot today
(`frontend/src/domain/kineticsTable.ts:45`,
`frontend/src/domain/arrheniusChartLayout.ts:218-223`) — confirmed by
reading `exclusionReasons()`. There is nothing partially built to extend;
this is greenfield math either way.

## 2. The five decisions

### 2.1 Does a network get its own page? — Yes.

The scale numbers in §1 (7 states, 21 channels, 42 kinetics fits, its own
solve/software/workflow-tool/literature) exceed what a species entry page
carries, and species entries have had pages for months. A `net_…` ref
currently renders as inert `<code>` with nothing to click — that is a dead
end for the one most-structured record type in the archive. Recommendation:
`/networks/:networkRef` as a full record page, mirroring the reaction-entry
page's `RecordIdentityHeader` + `SectionHeading`-per-section pattern. There
is no chooser-page problem here (no `net_`-vs-`nsolve_` prefix ambiguity
comparable to `rxn_`/`rxe_` — one network, one canonical detail ref) so this
is a single route, not two.

### 2.2 Is the primary representation a graph? — Yes, at this scale; must degrade explicitly past it.

7 nodes / 21 edges is genuinely a graph problem, not a table dressed up as
one: a table of 21 channel rows forces the reader to mentally reassemble
adjacency, while a diagram shows it at a glance. Nodes = states (labelled
with the server-computed `composition.state_label`, shaped by `kind`: well
vs bimolecular — visually distinct, e.g. filled vs outlined, never inferred
from label text); edges = channels (styled by `kind`, and by `has_kinetics`
— a channel *can* exist with no kinetics fit yet, and that must be visibly
different, not silently identical). 21 edges on 7 nodes is already near a
complete graph (in fact *is* one here) — force-directed layout is
appropriate at this density; a fixed grid/matrix would not read better. Node
click → jumps to that state's row in an accompanying `.data-table` (the
accessible equivalent, same pattern as the Arrhenius chart's own
table-behind-`Disclosure`); edge click → same, to the channel table row,
which is where `channel_key` and the microreaction refs live (data face, not
label — see §1's channel-identity finding). **Degradation plan for a bigger
future network** (the archive will grow): past a node/edge threshold — start
at "more edges than nodes²/2 rendered legibly," roughly 15–20 nodes on the
same 720px chart budget as the Arrhenius chart — fall back to the table-only
view with a banner explaining why ("this network's diagram would not stay
legible; showing the channel table"), never let the diagram silently
degrade into overlapping unreadable spaghetti. This is a design constant to
pick during PR 1 build, not a number to guess now.

### 2.3 Can it be a potential-energy surface? — No, not on today's data; do not promise one.

§1 measured 0 of 7 states with full-participant thermo coverage. A PES needs
a y-axis; there is no y-axis. Recommendation: **do not build a PES view**
against this data. Instead, ship the state/channel graph from §2.2 with no
y-axis at all (a pure connectivity diagram), and add a `ProductLevelsFact`-
style "energy coverage" fact to the network's evidence section stating the
measured fraction honestly (something like "0 of 7 states have relative
energies deposited" today, whatever the true count is at build time — this
must be **computed live from the served thermo sections**, never hand-typed,
so it stays correct as the archive grows). If a future network reaches
partial coverage, the honest presentation is per-node energy annotations on
the *same* connectivity diagram (label a covered node with its `h298_kj_mol`
relative to a chosen reference state, grey out uncovered nodes with "energy
not deposited") — not a separate PES chart that silently drops uncovered
states, which would misrepresent the surface as complete. A real y-axis PES
becomes viable only once coverage is high enough that gaps don't dominate
the picture; that threshold is an owner call once real data exists, not
something to build against a single trivial-species-only 22% baseline.

### 2.4 k(T,P) is the useful half — spec the endpoint, recommend server-side evaluation.

§1 showed the parameters are fully reachable in one request but nothing
evaluates them. Two evaluation math families are needed: Chebyshev
(orthogonal-polynomial expansion over reduced T, reduced log₁₀P — six
temperature × four pressure basis here, but shape varies per record via
`chebyshev_shape`) and PLOG (log-linear interpolation between the two
bracketing pressure-keyed Arrhenius fits, extrapolated flat outside the
table's pressure range — a genuinely different algorithm per RMG/Cantera
convention, easy to get subtly wrong, e.g. off-by-one in bracket selection
or wrong log-interpolation base). Weighing where this math lives:

- **Client-side** (extends `arrhenius.ts`/`arrheniusChartLayout.ts`
  pattern): keeps evaluation next to the existing single-Arrhenius `k(T)`
  math the reaction page already ships, and needs no new backend surface —
  but Chebyshev/PLOG evaluation is materially more involved than the
  existing `A·Tⁿ·exp(−Ea/RT)` one-liner, and a wrong published rate
  coefficient on a chemistry site is a worse failure mode than a wrong pixel
  position. Two independent implementations (Python-authored data, JS-
  evaluated) also diverge silently if the physical-constant/unit convention
  drifts (the same `R` value, degeneracy convention, and unit families
  already have to be kept in lockstep per the reaction-entry plan's own
  `arrheniusUnits.ts`).
- **Server-side** (new endpoint, evaluated once against the same code path
  that presumably validated the fit on ingestion): the archive already has
  Python chemistry utilities (`app/chemistry/`) that are the authoritative
  place for this kind of formula, per `CLAUDE.md`'s layer table; evaluating
  server-side means one implementation, testable against known reference
  points (e.g. RMG/Cantera's own Chebyshev evaluator, or Arkane's) with a
  fixture-pinned test the way `arrheniusChartLayout.test.ts` pins hand-
  computed Arrhenius values today.

**Recommendation: server-side.** The correctness stakes are higher than the
existing single-Arrhenius case (which is simple enough that a JS
reimplementation was low-risk), and centralizing the math means the API can
also serve a future CLI/notebook consumer without reimplementing Chebyshev
evaluation in every client — consistent with "TCKDB is sovereign, ARC and
every producer conform to TCKDB" (this cuts the other way here: TCKDB should
be the one authority on what its own stored fits evaluate to, not push that
authority out to whichever frontend happens to render it).

**Endpoint shape.** `GET /scientific/network-kinetics/{ref}/evaluate?temperature_k=…&pressure_bar=…`
(repeatable query params for a batch of points, capped like every other
list endpoint — e.g. `limit=200` points per call, matching the existing
`limit=1..200` convention on search routes), returning
`{network_kinetics_ref, points: [{temperature_k, pressure_bar, k, k_units, in_range: bool}]}`.
`in_range` is `false` (not omitted) when the requested point falls outside
`[tmin_k, tmax_k]` × `[pmin_bar, pmax_bar]` — extrapolation happens
mathematically for PLOG's log-interpolation table lookup but must be flagged,
never silently presented as fitted. A companion **network-scoped** batch
variant is worth adding in the same PR given the "165 KB in one call"
pattern already proven for coefficients: `POST /scientific/networks/{ref}/kinetics/evaluate`
with a shared `{temperature_k, pressure_bar}` grid and `channel_keys` (or
composition-hash pairs) to select which of the network's channels to
evaluate, so a network page's contour/heatmap view needs one request instead
of 21. Chart the result the same way `ArrheniusChart` charts today: x = T
(or a P-selector with T on x, given the second free variable — a
`<select>` for pressure the way the Arrhenius chart already has one for
x-axis mode), y = log₁₀ k, one curve per selected pressure, with the served
`in_range` flag driving a dashed/solid line split rather than a client-side
range guess.

### 2.5 The unfiltered-search gap — no `/networks` browse index yet; do add the open filter form.

With exactly one network in the archive, a `/networks` browse index would
show one row — not worth the route, the empty-state design, or the
`browse.css` fourth-`BrowseKind` wiring the reaction-entry plan already paid
for three other kinds. Recommendation: **no `networks/browse` route now.**
But `networks/search`'s all-filters-required gate is a real dead end for a
user who lands on the network page wanting "show me networks containing
this species" with only a SMILES in hand and no `net_ref` — that's exactly
`species_entry_ref`/`species_ref`/`reaction_ref` filters, which the search
endpoint already accepts; nothing new is needed there, only a frontend
search form (reachable from the network page's own species/reaction links,
not a global nav entry) that pre-fills one of those filters rather than
presenting an empty free-text box against a mostly-empty catalogue. Revisit
the browse-index question once a second network lands.

## 3. Information architecture

**Route.** `/networks/:networkRef` (`net_…`). No chooser page (§2.1).

**Sections, in order** (mirrors the reaction-entry page's section-heading /
side-ToC pattern):

1. **Identity header** — kicker "Pressure-dependent network · deposited
   scientific record," review pill, title = `network.name ?? "not named"`
   rendered as depositor-authored text (not an assertion — see §1), `net_…`
   ref with copy button, `description` behind a `Disclosure` when present
   (depositor prose, long — 400+ chars measured on the hydrazine network).
   Identity `.kv-list`: solve T range, solve P range, `me_method`, software
   release, workflow tool release, literature.
2. **Evidence** — `EvidenceChecklist`: species count, reaction count, state
   count, channel count, solve count, kinetics count (from
   `evidence_summary`, all served, never inferred), `has_chebyshev`/
   `has_plog`/`has_point_kinetics` as model-kind pills, and the §2.3 energy-
   coverage fact computed live from the joined thermo sections.
3. **Network diagram** — the §2.2 graph, with its degrade-to-table fallback
   and the accessible `.data-table` equivalent always present (never
   SVG-only), one row per state and one per channel.
4. **k(T,P)** — per-channel evaluated-rate chart (§2.4) once the evaluation
   endpoint ships; until then, a table of fit metadata only (`model_kind`,
   T/P bounds, `chebyshev_shape`/`plog_entry_count`) with an explicit note
   that evaluated values are not yet served — never plot raw coefficients as
   if they were rate values.
5. **Reactions** — table of the 6 `network_reaction` members (`rxe_…` links,
   already-existing pattern from `reactions/search?network_ref=` or the
   `microreactions[]` already inlined per channel).
6. **Review** — same three-tier pattern as every other record page.

## 4. API work

Files: `backend/app/api/routes/scientific/networks.py`,
`backend/app/services/scientific_read/network_kinetics.py`, new
`backend/app/services/network_kinetics_eval.py` (or under `app/chemistry/`
per the layer table — evaluation math belongs in `app/chemistry/`, the
service wraps it), `backend/app/schemas/reads/scientific_network_kinetics.py`.

- **A. Evaluation endpoint(s)** per §2.4: per-record `GET
  /scientific/network-kinetics/{ref}/evaluate` and network-scoped batch `POST
  /scientific/networks/{ref}/kinetics/evaluate`. Chebyshev math: reduced
  variables `T̃ = (2/T − 1/Tmin − 1/Tmax)/(1/Tmax − 1/Tmin)`, `P̃ = (2 log P −
  log Pmin − log Pmax)/(log Pmax − log Pmin)`, `log₁₀ k = Σ Σ α_{ij}
  φ_i(T̃) φ_j(P̃)` (Chebyshev polynomials of the first kind) — this is
  standard Arkane/Cantera convention and must be unit-tested against a
  reference value computed independently (e.g. Cantera's own evaluator on
  the same coefficient matrix) before trusting the TCKDB implementation,
  not just internally self-consistent. PLOG: bracket the requested pressure
  between the two nearest fitted pressures (or use the nearest single fit
  with `in_range=false` outside the table), log-linear interpolate
  `log₁₀ k(T)` between the two bracketing Arrhenius evaluations. Both need
  fixture-pinned tests (hand- or Cantera-computed at a few (T,P) points)
  the way `arrheniusChartLayout.test.ts` pins Arrhenius values today.
- **B. `include=coefficient_summary` on network detail** (optional, cheap
  win): the network detail endpoint (`GET /scientific/networks/{ref}`) does
  not currently surface anything about model-kind mix beyond
  `evidence_summary.has_chebyshev`/`has_plog`; if the diagram needs
  per-channel `has_kinetics` styling it already has that (`channels[].has_kinetics`
  is served today — confirmed in §1), so this is likely unnecessary; verify
  during PR 1 build before adding a field nobody consumes.
- **C. No new browse route** (§2.5).

**Regen steps**: `UPDATE_OPENAPI_GOLDEN=1 conda run -n tckdb_env pytest tests/api/test_openapi_snapshot.py`;
new error codes (`evaluate_out_of_range`? — decide whether out-of-range is a
soft `in_range:false` flag per §2.4, which needs no new error code, or a
hard 422, which does — this plan recommends the soft flag, so no catalogue
entry needed) go in `code_catalogue.py`/`test_api_code_catalogue.py` only if
the soft-flag recommendation is overturned; no ORM model changes, so
`schema.dbml` is untouched; `clients/python`: add `evaluate_network_kinetics`
and typed fields in `scientific_types.py`.

## 5. Frontend work

Files (new): `frontend/src/pages/NetworkEntryPage.tsx`,
`api/networkEntryApi.ts`, `components/NetworkDiagram.tsx`,
`components/NetworkKineticsEvalChart.tsx`, `domain/networkDiagramLayout.ts`,
`domain/chebyshevEval.ts`/`plogEval.ts` (thin client wrappers around the
server's evaluated points — **no client-side Chebyshev/PLOG math**, per
§2.4's server-side recommendation; these modules only shape/sample the
already-evaluated `points[]` for the chart, mirroring how
`arrheniusChartLayout.ts` shapes already-computed samples today), `network-
entry.css`; edited: `App.tsx` (new route), `ReactionEntryPage.tsx`'s
`NetworkSection` (link the `net_…` ref instead of plain `<code>`, one-line
change once the route exists).

**Diagram.** Reuse the Arrhenius chart's fixed-pixel-SVG /
`overflow-x: auto` pattern (not percentage-scaled — same accessibility-floor
defect it was built to avoid) rather than inventing a new chart shell. Force-
directed layout computed client-side from `states[]`/`channels[]` (7×21 is
trivial to lay out at request time; no server-side layout needed). Node
shape by `kind` (well vs bimolecular — a visual encoding, not text), edge
style by `channel.kind` and dashed/solid by `has_kinetics`. Every label from
`composition.state_label` (server-computed, safe) — `channel_key` appears
only in the data-table row and the SVG's `data-channel-key` attribute for
test/automation hooks, never as a rendered edge caption (per §1's channel-
identity finding).

**k(T,P) chart.** Requests the batch evaluate endpoint once per page load
(one request, per §2.4's "165 KB in one call" precedent), one panel per
selected channel or a small multi-select, y = log₁₀k, x = T, a `<select>`
for which fixed pressure(s) to show (mirrors the Arrhenius chart's x-axis-
mode `<select>` pattern) — reuses `chartScale.ts`'s `linearScale`/
`domainWithPadding`/`formatTicks` and `seriesColor`, same as every other
chart on the site, so no new scale primitives are needed. Table-behind-
`Disclosure` accessible equivalent, same as `ArrheniusChart`.

## 6. Slicing into PRs

- **PR 0 — design review of a static mock**, same discipline as the
  reaction-entry plan's PR 0: hand-built HTML page with the hydrazine
  network's literal measured values (7 states, 21 channels, the
  `composition.state_label` strings from §1) pasted in, reviewed at four
  widths, both themes, before any product code.
- **PR 1 — evaluation endpoint(s)** (§4 A). Red first: Chebyshev/PLOG
  evaluation matches an independently-computed reference at several (T,P)
  points; `in_range` flips correctly at each bound; batch endpoint returns
  all 21 channels' evaluated points in one call at a fixed grid. Deploy: API
  image, then Pi.
- **PR 2 — network entry page, sections 1–2, 5–6 of §3** (identity, evidence
  incl. the live-computed energy-coverage fact, reactions, review) — no
  diagram or k(T,P) chart yet, so this PR does not depend on PR 1. Red
  first: DOM-vs-payload identity on the hydrazine fixture; energy-coverage
  fact recomputes against the live fixture and does not regress to a
  hardcoded string; `NetworkSection`'s `net_…` ref becomes a real link.
  (This originally said the fact "recomputes to 0/7". That number was wrong
  — see "Build status" above. The criterion that mattered, and the one the
  shipped tests enforce, is that the sentence is computed from the payload
  rather than fixed, so changing the fixture changes the sentence.)
- **PR 3 — network diagram** (§3.3, §5). Red first: node/edge counts match
  `evidence_summary`; degrade-to-table fires past the chosen threshold on an
  injected larger fixture; accessible table always renders regardless of
  SVG state; `channel_key` never appears as a rendered `<text>` label
  (grep the rendered DOM in the test).
- **PR 4 — k(T,P) chart**, sequenced after PR 1 and PR 3. Red first: chart
  renders only served `points[]`, never re-derives k(T,P) client-side;
  dashed/solid split matches served `in_range`; one-request-per-page-load
  assertion (mock the batch endpoint, assert call count).

## 7. Open questions for the owner

- **§2.2's degrade threshold** is a real number someone has to pick (this
  plan suggests ~15–20 nodes as a starting point, not a measured constant —
  there is only one network to measure against, so no data-driven threshold
  exists yet).
- **§2.3's "not yet a PES" framing**: confirm the wording is acceptable
  wherever it appears (network evidence section, and anywhere a future
  species page cross-links into a network) — it commits the site to
  visibly advertising a coverage gap rather than quietly shipping a
  best-effort PES with missing nodes omitted.
- **§2.4's server-vs-client evaluation call**: this plan recommends
  server-side for correctness; confirm before PR 1, since it determines
  which layer (`app/chemistry/` vs `frontend/src/domain/`) owns the
  Chebyshev/PLOG formulas and is expensive to reverse after PR 4 ships
  against one shape.
- **`channel_key` display**: confirmed depositor free text with no
  alternative public ref (§1). This plan's position is "data face only,
  never a graph label" — confirm that holds even once a channel gets its
  own public ref (a natural follow-up given every other identity table has
  one and this is the one exception), since a real `nch_…` ref would let
  `channel_key` retire from the UI entirely.
- **`network.name`/`.description` as depositor prose** (§1): the existing
  `NetworkSection` already displays `name` bare; this plan does not propose
  changing that shipped behavior, but flags it for the same "depositor
  free-text on a public page" review the house rules ask for elsewhere —
  owner call on whether it needs a "as named by the depositor" qualifier
  the way literature abstracts get one, or whether "network name" reads
  differently enough (a title, not a scientific claim) to not need it.
- **§2.5**: confirmed no `/networks` browse index now; revisit once a
  second network is deposited — no action needed today beyond noting the
  trigger condition here so it isn't re-litigated from scratch later.

## 8. Verification log

Requests made against `https://tckdb.homecalvin.com/api/v1`, anonymous,
2026-09-08: `GET /scientific/networks/net_o6bt63kjeyvhvxx26w6kdi433a?include=states,channels,kinetics`;
`GET /scientific/networks/search` (unfiltered, confirms `422 missing_filter`);
`GET /scientific/network-kinetics/search?network_ref=…&limit=5`;
`GET /scientific/network-kinetics/{ref}?include=plog` and `?include=coefficients`
(one PLOG record, one Chebyshev record); `GET
/scientific/network-kinetics/search?network_ref=…&limit=50&include=coefficients,plog`
(batch, confirms 21+21 populated, ~165 KB); `GET
/scientific/species-entries/{ref}/thermo` and `/statmech` for all 9 species
entries referenced by the network's 7 states. Model reads (not live-DB
queries): `backend/app/db/models/network_pdep.py` (`NetworkChannel`,
`NetworkState` — no public-ref columns), `backend/app/schemas/workflows/network_upload.py`
and `network_pdep_upload.py` (`name`/`description`/`channel_key` field
definitions), `backend/app/api/routes/scientific/networks.py` (full route
list — confirms no evaluation endpoint exists), `frontend/src/domain/kineticsTable.ts`
and `arrheniusChartLayout.ts` (confirms PLOG/Chebyshev/falloff are excluded
from today's k(T) chart, so no client-side evaluation math exists to reuse).
