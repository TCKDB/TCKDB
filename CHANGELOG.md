# Changelog

All notable changes to TCKDB are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Two things are versioned, and they are not the same

**The software** (`tckdb-backend`, `tckdb-client`, `tckdb-schemas`,
`tckdb-mcp`, `tckdb-chemkin`) is versioned per package and described in this
file.

**A curated scientific dataset** is versioned independently as a *dataset
release* — a tag such as `2026.07.0`, an immutable SHA-256-checksummed
manifest, an attributed selection ledger, and its own citation string. Dataset
releases are not listed here; they are discoverable at
`GET /api/v1/scientific/releases` and each carries its own
`changelog_entry`. Cite the dataset release when you use TCKDB's numbers; cite
the software (see [`CITATION.cff`](CITATION.cff)) when you use the code.

Conflating the two is the mistake this split exists to prevent: upgrading the
backend must never change what a published dataset says, and re-curating a
dataset must never require a code release.

## Maturity and version policy

TCKDB is **pre-1.0**. Until 1.0:

- **Minor** version bumps may contain breaking changes to HTTP contracts,
  wire schemas, and database schema. Read the entry before upgrading.
- **Patch** bumps are additive or corrective only.
- The **database schema** advances by Alembic revision, never by editing an
  applied revision. The revision a deployment is at is reported by
  `GET /api/v1/readyz`, and is bound into every dataset-release manifest.
- **Append-only tables stay append-only.** `release_selection`,
  `release_manifest`, `release_artifact`, `record_review_event`,
  `record_reproducibility_assessment` and the accepted-science tables are
  guarded by database triggers, not only by application code. No release will
  relax that.
- **A published dataset release is never rewritten.** Superseding a curated
  selection appends a row; retracting a release sets its status to
  `withdrawn` and keeps the row and manifest readable, so an outstanding
  citation never dangles.

Per-package maturity classifiers:

| Package | Status | Notes |
|---|---|---|
| `tckdb-backend` | Alpha | HTTP read contracts stabilising; schema still advancing |
| `tckdb-client` | Beta | Typed methods track the published OpenAPI |
| `tckdb-schemas` | Alpha | Upload wire contracts; version bumped on every change — **enforced in CI since 2026-08-17; five earlier versions are not unique, listed under Unreleased** |
| `tckdb-mcp` | Alpha | Agent integration surface |
| `tckdb-chemkin` | Alpha | Mechanism-export adapter |

The client is deliberately ahead of the backend: it is a thin, well-tested
wrapper over a contract that is itself still moving.

## Unreleased

### Added

- **A conformer basin now says how many optimisations back it, not how many
  optimisation jobs were run.** A two-stage geometry optimisation deposits two
  `opt` calculations — a coarse pre-optimisation and the refinement it feeds,
  joined by `calculation_dependency.dependency_role = 'optimized_from'`. Both
  belong to the basin; between them they are one optimisation.
  `evidence_summary.optimization_chain_count` on the conformer-group read
  counts optimisation **chains**, so a staged optimisation contributes `1`.

  Measured on the hosted instance: **156 anchored `opt` rows across 66
  conformer groups collapse to 136 chains**, with the 20 collapsed chains
  falling across 16 groups. Carbon dioxide, the landing page's showcase
  panel, has 3 `opt` rows and 2 optimisations.

  Only `optimized_from` collapses. `freq_on` (63 both-anchored pairs),
  `single_point_on` (65) and `scan_parent` (46) also join two calculations,
  but a frequency job on an optimised geometry is genuinely different evidence
  from the optimisation that produced the geometry, and folding those together
  would be a scientific error rather than a tidier number. The collapse also
  stops at the observation boundary and is correct for a chain of any length.

  **No previously published number changes.** `evidence_coverage.opt` counts
  observations, not calculations, so a basin whose observation carries a
  two-stage optimisation already read `1` for it; `calculation_count` and
  `geometry_count` deliberately stay row counts, because each is the length of
  a list `include=calculations` / `include=geometries` will hand back. The
  field is additive.

### Changed

- **A depositor can now download their own artifacts. Until now, nobody could.**
  `GET /api/v1/scientific/artifacts/{sha256}/download` served raw bytes only
  when a curator had marked the owning calculation `approved`. Measured on the
  hosted instance, **563 of 563 artifacts** belonged to calculations still
  `not_reviewed`, so the gate had never opened for anybody, including the
  person who uploaded the file. Approval publishes evidence to every
  authenticated caller; it was never a statement about who may see their own
  upload, and using it as one locked every depositor out of everything they
  had deposited.

  The gate is now **approved OR deposited by the caller**. Ownership is not a
  new rule: it is the predicate the upload route already authorizes attaching
  artifacts with — the `created_by` of a live submission that deposited the
  calculation, or `calculation.created_by` for deposits that predate
  submissions — extracted to `app.services.deposit_ownership` so the read path
  and the write path cannot drift into two answers.

  **The authentication gate is unchanged.** Ownership is a reason to serve an
  authenticated caller and never a reason to serve an anonymous one; every
  download path still requires a credential, and public access remains a
  separate, unbuilt scrub-on-download design. A caller who owns nothing still
  gets the same anti-probing 404 for an unapproved digest as for an unknown
  one — 404 and not 403, because a 403 would confirm the digest exists.
  Stored bytes are still verified against their SHA-256 and byte count on
  every path, the owner's included, and a verification failure is still
  recorded as a custody break (ADR 0014).

  [ADR 0004](docs/adr/0004-store-artifacts-verbatim-gate-raw-log-access.md)'s
  argument for gating raw logs at all — they carry scratch paths, usernames
  and scheduler ids, and scrubbing them at rest would break
  content-addressing — is untouched and restated; only its unargued choice of
  *review status* as the gate is amended. No schema or migration change.

- **A transition state now says how many of its entries have the evidence,
  not that one does.** `GET /api/v1/scientific/transition-states/{ref}`
  returned an `evidence_summary` carrying seven `has_*` booleans —
  `has_opt`, `has_freq`, `has_sp`, `has_irc`, `has_path_search`,
  `has_geometry_validation`, `has_scf_stability` — OR-ed across **every
  entry** under the transition state. `has_sp: true` therefore meant "at
  least one entry somewhere under this TS has a single point", which a
  reader takes as "this transition state has a single point". The flag was
  asymmetrically informative: `false` was strong (nothing anywhere has it)
  while `true` was nearly empty, so it was reliable only in the direction
  nobody reads it for.

  TS-concept scope now reports **`entry_count`** and an
  **`evidence_coverage`** block: per evidence kind, how many of those
  entries have at least one calculation of that kind. Coverage counts
  *entries*, never calculations — an entry with three `freq` jobs
  contributes `1` — so a value can never exceed `entry_count` and
  `sp: 1` against `entry_count: 3` shows an unevenly covered TS at a
  glance. `count > 0` reproduces the retired boolean exactly.

  A **full** count says coverage is complete, not that the entries are
  **comparable**: they may sit at different levels of theory, come from
  different codes, and describe different geometries. Coverage is not
  consistency; that still requires `include=calculations`.

  **TS-entry scope keeps its booleans** and is unchanged — one entry, one
  set of calculations, nothing for a `true` to hide behind. That covers
  `GET /scientific/transition-state-entries/{ref}`, the search surface
  (which returns entry-grain records and therefore needed no quantifier
  knob), and the `transition_states[*].evidence_summary` block embedded in
  `/reaction-entries/{id}/full`.

  **Breaking for callers reading `has_*` off the TS-concept detail
  endpoint. Migration: `has_x` becomes `evidence_coverage.x > 0`.** The
  defect is **latent today** — measured against the hosted instance, all
  34 transition states have exactly one entry, so the OR cannot currently
  differ from that entry's own value and nobody has been misled yet. It is
  a bug waiting for its trigger: the first second entry deposited under one
  transition state (a re-optimisation at another level, say) makes it
  silently wrong, with nothing in the response to mark the change. Fixing
  it while it is still latent is why the migration costs one expression
  rather than a data audit. No database schema or migration impact — this
  is a read projection, not a table. No client or MCP change: `tckdb-client`
  types this block as an opaque JSON object and the MCP search allowlist
  gains no field.

  Sibling of the conformer-group fix in the same release, which replaced
  the same OR-across-children booleans with `evidence_coverage` at group
  scope.

- **Reaction search now means containment, and a one-sided query finally
  works.** `GET|POST /api/v1/scientific/reactions/search` accepted
  `reactants` or `products` alone, and the validator said so explicitly —
  but the matcher compared *both* roles for multiset equality. A query
  naming only reactants therefore asked for "entries whose reactants are
  exactly {X} **and** whose products are exactly {}". No reaction has zero
  products, so a reactants-only search could not match anything, for any
  input, ever. It returned `200` with an empty result set rather than an
  error: measured against the hosted instance, `?reactants=NN` answered
  "no reactions" while the database held 24 hydrazine reactions.

  A **`match`** parameter now selects the comparison, and it defaults to
  **`contains`**: set containment per role. Every species the caller names
  must appear in that role; a side left empty constrains nothing. So
  `?reactants=NN` means "every reaction with NN among its reactants,
  products unconstrained" — the question a chemist was asking. `match` is
  accepted on `/scientific/reactions/search` and on
  `/scientific/kinetics/search`, which resolves reaction identity through
  the same service and had the same defect.

  Containment is deliberately **set**-based, not multiset. A reaction
  consuming two NN matches `reactants=NN`, and `reactants=NN&reactants=NN`
  matches a reaction consuming one. Stoichiometry is not a filter in this
  mode; a caller who wants counts to line up is asking for a specific
  equation, and that is what `match=exact` is for. The opposite reading is
  defensible enough that it is written into the enum's docstring and into
  the OpenAPI description rather than left to fall out of the code.

  **Breaking for existing callers who supply both sides.** They got exact
  multiset equality; under the new default they get containment, which
  returns a superset — a two-species query will now also match the larger
  reactions that contain those two. **Migration: send `match=exact`**,
  which reproduces the previous behaviour byte for byte on both endpoints
  and remains a first-class query (`reaction_ref` does not replace it —
  you do not always hold the ref). Callers who supply one side were
  getting an empty list and cannot regress. `direction` is unchanged and
  composes with `match` on both axes: under `direction=either`,
  containment is tested in both orientations and `matched_direction`
  reports which one matched under the same semantics the matcher used.

  Per the pre-1.0 policy above, a minor bump may break an HTTP contract.
  This one does, which is why it is written down here rather than shipped
  quietly. No database schema or migration impact: this is a query
  projection, not a table. **`tckdb-client` 0.53.0 → 0.54.0** (new `match`
  keyword on `search_reactions` and `search_kinetics`; omitted means the
  server default applies). **`tckdb-mcp` 0.1.1 → 0.2.0** (new `match`
  field on `tckdb_search_reactions` and `tckdb_search_kinetics`).

### Added

- **The scientific data has a license: CC BY 4.0.** The code was MIT and said
  so; nothing anywhere said what a reader may do with the *numbers*. Since
  `dataset_release.data_license` is a required, non-blank column, that
  unanswered question was the hard blocker on publishing anything — every
  release must name a license, and no release had ever been cut.

  `LICENSE-DATA` at the repository root is the notice: CC BY 4.0 (attribution
  required, reuse otherwise unrestricted, and the attribution is a citation of
  the dataset release), stated by reference to the canonical deed and legal
  code rather than by vendoring a copy that would drift. It says what it
  covers — deposited records, derived products, raw calculation artifacts
  published in a release — and what it does not: the source code (MIT, see
  `LICENSE`) and the third-party test fixtures carried in from RMG-Py and ARC,
  which are code-adjacent test inputs under their own licenses and are
  excluded by name.

  `CC-BY-4.0` and `MIT` are now the defaults in both release-creation paths
  (`create_release`, `POST /api/v1/releases`), so a curator cutting an
  ordinary release does not restate the project's licensing on every request.
  An explicitly supplied license still wins and is stored verbatim; an empty
  string is still refused. No schema change: the column and its
  `data_license_nonblank` constraint are unchanged.

  Recorded alongside it, because it stops being moot the moment a second
  person uploads: **an operator may license their own deposits and nobody
  else's.** The license must become part of the upload contract *before* a
  deployment accepts deposits from a second contributor — not retrofitted
  afterwards. That constraint is written into `LICENSE-DATA`,
  `backend/docs/specs/ingestion_submission_model.md`, and
  `backend/docs/specs/dataset_release_and_profiles.md` §7b. The mechanism is
  deliberately not built here.

- **Every token TCKDB puts on the wire now has a published definition.**
  `matched_direction: "reverse"`, `under_review` beside `not_reviewed`,
  `"ts_graph_or_smiles_present": "missing"`, `spc_` beside `spe_` — each is
  a real distinction the code takes seriously and none is guessable from
  the string, and nothing in the API or the documentation said what they
  meant. `docs/guides/api_vocabulary.md` is a glossary of 438 of them: the
  review statuses, the trust badges and check outcomes, the 143 trust check
  names, the identifier prefixes with their content-derived/opaque split,
  the reaction-search vocabulary, and every refusal code a caller can
  receive.

  It is **generated** from the sources that define those tokens — the
  read-schema enums, `public_refs.PREFIXES`, the trust rubrics, the code
  catalogue — by `backend/scripts/generate_api_vocabulary.py`, and
  `backend/tests/scripts/test_api_vocabulary.py` fails if the committed
  document drifts from a fresh render, if an entry names a token no source
  has, or if an enum every response carries has no entry. A hand-written
  glossary would be stale the first time somebody added a status, and a
  stale glossary answers confidently and wrongly.

  Inclusion is two halves: a reader must be able to meet the token (checked
  mechanically against the enums the read schemas and the trust fragment can
  serialise) and chemistry must not already decode it (declared per
  vocabulary). Chemistry-valued vocabulary is deliberately out. Reaction
  family display names are recorded as a **gap** rather than invented:
  `reaction_family` holds only `id`, `name` and `created_at`, so
  "Hydrogen Abstraction" for `H_Abstraction` is not a fact this database
  holds.

- **The base URL now answers a person.** `https://<host>/` served a bare
  `404 application/json` blob, and so did `/docs`, `/redoc` and
  `/openapi.json`, because hosted deployments set `EXPOSE_API_DOCS=false`
  and no route was registered at `/` at all. Anyone following the URL a
  paper prints — a referee, most importantly — got an error object with
  nothing in it to say a database was there. `/` now serves a
  self-contained HTML landing page: what TCKDB is, a worked example of a
  real check refusing to accept a frequency list it cannot justify, the
  four write-behaviour roles every table plays, how to cite the software
  versus a dataset release, and where the API starts. It loads no CDN, no
  web font and no script, so it renders on a locked-down network and with
  JavaScript disabled. Nothing else about the route table changed: `/` is
  registered last, matches one exact path, and is excluded from the
  OpenAPI document.
- **`EXPOSE_API_REFERENCE`**, a new setting, defaulting to `false`. With
  `EXPOSE_API_DOCS=false` it registers ReDoc at `/redoc` and the schema at
  `/openapi.json` and still leaves Swagger UI at `/docs` unregistered.
  `EXPOSE_API_DOCS` was all-or-nothing, and the startup guard refuses to
  boot a hosted deployment with it on — correctly, because it brings
  Swagger's live request console with it. That is not the same risk as
  publishing the contract, which a hosted instance wants to do. Note that
  FastAPI's ReDoc page loads its renderer from `cdn.jsdelivr.net` and its
  fonts from Google Fonts; the landing page does not.

### Fixed

- **A species found by searching for its formula was served without one.**
  `GET /api/v1/scientific/species/search?formula=CH3` matched the methyl
  radical and returned it with `"formula": null` — the field was declared on
  `SpeciesScientificRecord` and assigned by nothing. The database had already
  computed the formula in order to answer the query, through the functional
  index `ix_species_formula_lookup` over
  `mol_formula(mol_from_smiles(smiles))`, and then the read path discarded it.
  The landing page renders a **Formula** row for every species; it had always
  read "not recorded".

  The formula is now **the same SQL expression that answered the filter**,
  projected onto the page's species rows. Not a second implementation in
  Python: search and display cannot disagree about the formula of one row
  because there is one expression, used twice, in one function. No stored
  column and no migration — `species` still has no `formula`, and the value
  stays derived.

  What arrives is Hill notation as the RDKit cartridge spells it: `H2O`,
  `C6H6`, and `CH3` for the `[CH3]` radical — **radicals carry no marker** —
  with a trailing charge suffix for ions (`HO-`, `H4N+`, `Fe+2`). Isotopes are
  not distinguished, so heavy water reports as `H2O`. The landing page needed
  no change: it subscripts a formula only when the parsed element/count
  sequence round-trips to the original string, so the ionic forms print
  verbatim rather than being rendered wrongly.

  `formula` stays `str | None` and is now null in exactly one case: a species
  whose stored SMILES will not parse, where the cartridge yields SQL NULL
  rather than raising.

- **The species search served cis- and trans-diazene as the same record, and
  a reader picking one had even odds of citing the other molecule.** `GET
  /api/v1/scientific/species/search?smiles=N=N` returned two species entries
  whose *every served field* was byte-identical apart from an opaque
  `species_entry_ref`: same `species_entry_kind`, same
  `electronic_state_kind`, same availability counts, same review badge. The
  database knew exactly what they were — `stereo_label = Z` (cis-diazene) and
  `stereo_label = E` (trans-diazene), two molecules with different
  thermochemistry — and the read projection dropped the column. Nothing on
  the wire hinted that anything had been withheld, so a reader asking for
  diazene statmech chose a ref at random. That is a wrong scientific answer,
  not a missing feature, and it was live and anonymous.

  The two **species-discovery** surfaces — `/species/search` and
  `/species/structure-search` — now serve the entry's identity columns:
  **`stereo_label`, `electronic_state_label`, `term_symbol` and
  `isotope_key`** (`electronic_state_kind` was already served), plus
  **`species_entry_label`** — those five rendered as one short string
  (`"E"`, `"excited T1"`). Those five columns are exactly
  `uq_species_entry_species_id` minus the species itself, which is what makes
  the set a real discriminator rather than a hint: two entries of one species
  differ in at least one of them by construction, so they cannot both render
  as `null` and cannot render the same. Both records also gain
  **`stereo_kind`** from the parent species, which is what makes a *null*
  stereo label readable — `achiral` says there is nothing to label,
  `ez_isomer` says stereoisomers exist and this entry has not been labelled.

  **The same defect was on nine further blocks, and they are fixed too.**
  Discovery is where a reader picks a ref, but it is not where they read a
  number. A statmech search for the deployed `N=N` species returns *eight*
  records spanning both diazene entries, and every one of their `species`
  context blocks reported `canonical_smiles: "N=N"` and nothing else — so a
  reader could not tell which partition function belonged to which molecule
  even after picking correctly. Each of these blocks now carries
  `species_entry_label`:

  | Block | Surface |
  |---|---|
  | `StatmechSpeciesContext` | `/statmech/search`, statmech detail |
  | `ThermoSearchSpeciesContext` | `/thermo/search` |
  | `TransportSpeciesContext` | `/transport/search`, transport detail |
  | `ConformerSpeciesContext` | conformer search + detail |
  | `SpeciesCalculationsSpeciesContext` | `/species/calculations/search` |
  | `SpeciesEntryOwnerSummary` | calculation detail |
  | `NetworkSpeciesSummary` | `/networks/{ref}?include=species` |
  | `ReactionParticipantSummary` | `/reactions/search` |
  | `ReactionFullSpeciesParticipant` | `/reaction-entries/{ref}/full` |

  A test enumerates `app.schemas.reads` and fails on **any** response block
  that names a species (`canonical_smiles` or `smiles`) together with a
  `species_entry_ref` but carries no `species_entry_label`. The guard is
  written over the schema package rather than over a list of surfaces,
  because the defect was on eleven blocks at once — a checklist would have
  missed the twelfth.

  **One thing deliberately not changed: the reaction `equation` string.** It
  is still rendered from participant `smiles` alone, so `NN <=> N=N + [H][H]`
  does not say which diazene. Fixing it would change a served value that
  consumers parse, which this release is not doing; the participant blocks
  now carry what is needed to render the equation unambiguously, and
  changing the default rendering is its own decision.

  The derivation is **not new**. `species_entry_label` already existed for
  the pressure-dependent network surface, which hit this same defect on this
  same species (the hydrazine network's two diazene wells) and solved it
  there — for network *state* composition only, while the network's own
  `include=species` block still had it. It has moved to
  `app/services/scientific_read/species_identity.py` and every surface
  imports it, so a species reads the same way in a species search, a
  structure search, a statmech record and a network state label. Two
  functions producing species labels that can disagree is a failure this
  project has hit repeatedly (`points`, `literature`, `parameters`,
  `workflow_tool_release`, `R`, `CO`, `F`). A second test asserts the
  derivation reads every column of `uq_species_entry_species_id`, read off
  the ORM model rather than restated, so adding a sixth identity column
  cannot silently turn the label back into a hint.

  **Default projection, not an `include=` token.** A field whose absence
  makes two records indistinguishable belongs in the default, because a
  reader who does not know to ask is exactly the reader who gets the wrong
  molecule. Every value comes from a row the surface already loaded — the
  discovery surfaces hold the ORM entity, the context builders gained four
  columns on a `SELECT` they were already running — so this costs no
  additional query anywhere.

  **A null column renders as `null`, never `""`.** Measured against the
  hosted instance: 60 species entries, of which **9** carry a stereo label
  (`S`x4, `E`x3, `Z`x1, `R`x1) and **51** carry none;
  `electronic_state_label`, `term_symbol`, `isotope_key`,
  `isotopologue_label` and non-`ground` `electronic_state_kind` are at **0**.
  `stereo_kind` is non-nullable and populated on all 59 species. So the acute
  case is narrow — `N=N` is the only species with two entries — and the
  under-description was broader. The four zero-population columns are served
  anyway because they are identity components: the first entry that carries
  one must be distinguishable the day it lands, not the day someone notices.

  `isotopologue_label` is deliberately **not** served. It is deprecated, is
  no longer part of the entry's unique identity, and is never written by the
  application, so it cannot discriminate between two entries; serving it
  would advertise an identity component that is not one.

  Purely additive — no served field is removed, renamed, or changed in value.
  No database schema or migration impact: this is a read projection, not a
  table. `tckdb-client` **0.55.0 -> 0.56.0** declares `stereo_kind` on
  `SpeciesRecord` and the five identity fields plus `stereo_kind` on
  `SpeciesStructureRecord` (`SpeciesRecord.entries` was already an untyped
  JSON list, so nested entry fields flow through either way). No MCP change:
  `tckdb-mcp` passes the search response through without knowing its shape.

- **A linear molecule could deposit a frequency list with one vibration
  missing, and nothing said a word.** A harmonic analysis of `N` atoms has
  `3N - 6` vibrations if the molecule is bent and `3N - 5` if it is linear —
  linear molecules have one *more*, because a bend that would be two distinct
  motions in a bent molecule stays degenerate along a straight axis. TCKDB
  already flagged one direction of that one-mode gap (a bent geometry carrying
  the linear count). The other direction was open: a **linear** geometry
  carrying `3N - 6` modes is one vibration short, and every check accepted it.
  The wire-side completeness floor could not catch it either — `3N - 6` *is*
  that floor and it warns strictly below, so the deposit landed exactly on the
  accepted line.

  The consequence is the one that matters: a consumer recomputing a partition
  function from that record gets a *number*, not an error, and the number is
  wrong. Silently.

  Now reported as `freq_list_bent_mode_count_for_linear_geometry`, an
  `UploadWarning` on an accepted 201 — advisory, never blocking, because the
  linear/bent boundary is a tolerance rather than a definition (ADR 0008) and
  because a genuine partial or frozen-atom Hessian produces the same count
  honestly. The message names the cause that actually produces it: **a linear
  molecule's bending modes are doubly degenerate** — CO2's four vibrations are
  two stretches and one bend counted twice — so a parser that de-duplicates
  equal frequencies drops one component and lands exactly on `3N - 6`. The
  generic short-list warning argues from partial Hessians and frozen-atom
  regions, and would have sent a depositor looking in the wrong place; that is
  why this is a second code rather than an extension of it.

  The two codes are mutually exclusive by construction, not by convention: the
  counts differ by one and each is admitted only under the opposite geometry
  verdict. Reaches every upload shape — the conformer route, the
  computed-species and computed-reaction bundles, standalone transition
  states, the statmech/thermo/transport product uploads and the
  pressure-dependent network route — because all six walkers already funnelled
  through one judgement function.

- **Two pull requests could claim the same package version, and `git` would
  merge it silently.** Every change to a published package bumps its version.
  Two branches that start from the same base therefore bump to the *same next
  number*, and when the second merges, the version line on both sides is
  **byte-identical** — so `git` reports no conflict, the file does not appear
  in the merge diffstat, and one version number ends up describing two
  different packages. This was caught by hand on 2026-08-17, one line-diff
  before it shipped.

  A pull-request check now refuses it. It makes **two** comparisons against
  **two different refs**, because one ref cannot do both: *monotonicity*
  against the merge base (`git merge-base(base.sha, head.sha)` — not `main`'s
  tip, or a branch merely behind would be blamed for someone else's merge),
  and *novelty* against `origin/main` fetched at job runtime plus existing
  `<name>-v<version>` tags. The near-miss above passes the first check and is
  caught only by the second. Comparison uses a PEP 440 sort key, not string
  order, so `0.10.0` correctly exceeds `0.9.0`. Covers `tckdb-client`,
  `tckdb-schemas`, `tckdb-chemkin` and `tckdb-mcp`; a test fails if a new
  `pyproject.toml` appears in neither the covered nor the excluded list.

- **Five `tckdb-schemas` versions already carry more than one package, and
  this is the record of them.** Measured twice, by two independently written
  scripts that agreed, comparing *tree contents* per version rather than
  commit counts:

  | Version | Distinct states |
  |---|---|
  | `0.2.0` | 3 |
  | `0.8.0` | 2 |
  | `0.14.0` | 2 |
  | `0.30.0` | 2 |
  | `0.33.0` | 2 |

  `tckdb-mcp` `0.1.0` carries four states — it has never been bumped at all.
  **`tckdb-client` is clean** across its whole history.

  The difference is not cosmetic. Across the two `0.2.0` states, one lacks
  `HessianSource`, `TunnelingModel`, and the `lindemann` / `troe` / `sri` /
  `plog` / `chebyshev` entries entirely: **one state rejects payloads the
  other accepts.**

  **`0.8.0` is the one to know about.** The annotated tag
  `tckdb-schemas-v0.8.0` reads *"pinned for tckdb-adapters/tckdb_arc (Phase
  1)"*, and holds the **first** of its two states —
  `git merge-base --is-ancestor 7ad5cb99a tckdb-schemas-v0.8.0` returns false.
  Commit `7ad5cb99a` changed `tckdb_schemas/enums.py` and
  `tckdb_schemas/workflows/computed_reaction_upload.py` and left
  `version = "0.8.0"` untouched, so the two states **accept different upload
  fields**.

  **No consumer resolves a version number**, which is why nothing is being
  re-published to correct this. These packages were only ever installed from
  git, never from a package index, and ARC — the only known downstream —
  installs from a branch with no tag, SHA, or version constraint at all. So a
  duplicated version cannot mis-resolve for anybody today.

  **The `v0.8.0` tag is deliberately left where it is.** Moving or deleting a
  tag is its own hazard: anyone who has already fetched it holds the old
  target either way, and a moved tag makes two clones disagree about what a
  name means. It is recorded here instead, which is the honest fix for
  something that has already happened.

- **The `mypy` gate could not see the wire-contract package, and said
  "Success" anyway.** `tckdb-schemas` is a first-party package that lives in
  this repository and is installed *editable*. `mypy` does not read an
  editable install's import hook, so it could not find the package by name and
  reported `import-not-found` on all **38** of its imports from
  `backend/app/schemas` — which `ignore_missing_imports = true` then absorbed
  silently. The gate reported `Success: no issues found in 149 source files`
  while every type error *inside* the wire package, and every type error in
  backend code arising from how it uses the wire package, was invisible to it.

  Made resolvable and made a check target: `mypy_path` points at the package,
  the package is listed in `files`, and `ignore_missing_imports` is now
  **off**. All three are necessary and none substitutes for another.
  `follow_imports = "silent"` means a merely-*imported* module is analysed but
  its own errors are suppressed, so `mypy_path` alone would still have said
  nothing about a broken annotation inside the package; and
  `ignore_missing_imports` cannot distinguish "third-party package with no
  stubs" from "first-party package we failed to point mypy at", which is what
  made the original failure silent by construction. Measured at the time of
  the change: nothing in scope needed the setting — all 38 suppressed errors
  were `tckdb_schemas`. A stubless third-party dependency now needs a narrow
  per-module override, the way `rdkit` already has one.

  **12 findings** the gate had been missing, all in code that merged green.
  Eleven are fixed here; one is a documented suppression with its argument on
  the line. The one that was a live defect rather than an annotation
  infelicity: `EnergyTransferIn.model` on the pressure-dependent network
  upload is a **required** `str`, but its normalizer was
  `normalize_optional_text`, which collapses a blank string to `None`. A
  whitespace-only energy-transfer model name passes `min_length=1` and then
  left the field holding `None`. It is now `normalize_required_text`, which
  refuses the blank with a 422 — which is what `min_length=1` was already
  promising. The rest: two `float | None` comparisons in the NASA polynomial
  temperature-bound validator that were guarded by a `None`-count the checker
  could not read; two loop variables reused across loops over differently
  shaped payloads; and one dict annotated with the ORM `CalculationType` while
  holding the wire `CalculationType` — two distinct classes with identical
  members, whose every comparison worked only because both subclass `str`.

  Gate scope went from 149 to 183 source files. Proven by mutation, because a
  configuration change that silently still ignores the package looks identical
  to one that works: a deliberate `return 12345` from a `-> str` function
  inside the wire package, a deliberate misuse of a `tckdb_schemas` symbol
  from `backend/app`, and a deliberately unresolvable `mypy_path` each fail
  the gate now and each passed it green before.

- **A full artifact store is no longer reported as "retry later", and no
  longer reports itself as healthy.** Two halves of one defect.

  *The wrong answer.* `artifact_storage.py` special-cased exactly one
  condition — the store answering "no such key" — and every other error a
  botocore `ClientError` can carry collapsed into a single
  `503 artifact_storage_unavailable ... Retry later.` A **full** store landed
  in that residue, so a depositor uploading into a store with no disk left was
  told to do the one thing that cannot work: retrying a full disk fails until
  an *operator* frees space. Uploads into a store that has no room now answer
  **`507 artifact_storage_full`**, whose body says an operator must act.
  507 rather than a second code at 503 because the *status* then carries the
  advice: it is registered with exactly this meaning (RFC 4918), it is what
  MinIO itself answers, and it is absent from `tckdb-client`'s default retry
  set — so a pinned client and a non-Python caller both stop after one attempt
  without knowing the code exists. The `Replay` vocabulary is deliberately not
  extended: `never_succeeds` would be a false claim (an operator frees space
  and the identical request then succeeds) and any declaration at 507 is inert
  by `is_replay_futile`'s own rule.

  The error codes are **measured, not inferred**. MinIO
  `RELEASE.2025-09-07T16-13-09Z` was filled on a size-capped scratch volume
  and answered `XMinioStorageFull` at HTTP 507; with a hard bucket quota it
  answered `XMinioAdminBucketQuotaExceeded` at HTTP 400. `EntityTooLarge` is
  deliberately *not* treated as a capacity signal — it is a fact about one
  object, not about free space.

  *The silent health check.* `/status` probed artifact storage with a
  `head_bucket`, and a full store answers a `head_bucket` with **200**: every
  read succeeds, and — measured — even a 1-byte write succeeds on a store that
  refuses a 4 MiB one, because MinIO's threshold check is sized against the
  incoming object. So `/status` reported green while every artifact upload
  failed for want of space, and nobody was told, including whoever was on
  call. No read-only probe can detect this and no *cheap* write probe can
  either; the S3 API exposes no capacity query to ask instead. `/status` now
  reports what the real write path was told, as
  `artifact_storage.storage_full` with the observation timestamp, and degrades
  on it. Its limits are documented rather than papered over: `false` means "no
  refusal is outstanding", not "there is space", and onset still needs one
  upload attempt to fire. See
  [`docs/deployment/troubleshooting.md`](docs/deployment/troubleshooting.md).

- **That observation is now durable, and clears only on evidence of the right
  size.** It lived in a module global, so a restart forgot it and `/status`
  reported healthy until the next upload failed. It is now an append-only
  `artifact_storage_capacity_event` log, with no `is_full` column — "is the
  store full?" is computed head-of-log, because a stored flag would be a
  second source of truth able to disagree with the log it summarises (the
  shape ADR 0007 rejected for curated selections).

  The clearing rule is the point, and the obvious version of it is wrong.
  "Clear when a write succeeds" would have restored a green light while every
  real upload still failed, because the same store **refused 8 MiB and
  accepted 1 byte in the same second**. So a refusal records the *size* it was
  refused at, and is answered only by a later write of at least that size, a
  free-space reading of at least that size, or an operator. There is
  deliberately **no time-based expiry**: a timer guesses, a size-qualified
  success measures, and a stale "full" an operator can clear in one command is
  safer than a flag that goes quiet while the disk is still full.

  `/status` additionally consults **MinIO's admin API** for free space while a
  refusal is outstanding, so recovery is noticed on the next poll instead of
  on the next sufficiently large upload. It needs no new credentials (the
  compose file already gives the API MinIO's root user), writes nothing, and
  is skipped entirely on a healthy store. It is supplementary, never
  authoritative: any failure — a non-MinIO store, a 403, a timeout — is "no
  opinion" and changes nothing. It is also, measured, **blind to bucket
  quotas** (418 MiB reported free while a 2 MiB write was refused), so it may
  not clear a quota refusal.

  New admin endpoints `GET`/`POST /admin/artifact-storage/capacity[/clear]`
  let an operator read the state and clear it with a required reason, which is
  recorded. Clearing appends; it never edits or deletes the refusal.

  Also fixed on the way: `artifact_persistence._store_and_record`'s broad
  `except Exception` caught the already-typed `ArtifactStorageUnavailable` and
  raised a *fresh* one, discarding every discriminator set upstream — so the
  pre-existing `missing` flag was being erased on the upload path too; and
  `store_artifact` let a raw `ClientError` from `create_bucket` escape past
  every `except ArtifactStorageUnavailable` downstream.

  Behaviour of `artifact_storage_unavailable` (503) and
  `artifact_object_missing` (502) is unchanged. **`tckdb-client`
  0.51.0 → 0.52.0** (documentation and one test; no code change — 507 was
  already outside the default retry set). No schema or migration impact.

- **A client no longer retries a lost artifact forever.**
  `GET /scientific/artifacts/{sha256}/download` reports two different storage
  failures: `503 artifact_storage_unavailable` when the object store did not
  answer, and `502 artifact_object_missing` when it answered and the bytes a
  still-published record points at are gone. Only the first can clear by
  waiting. Both statuses are in `tckdb-client`'s default retry set and the
  code that says which is which was excluded from `RejectionCode` by
  construction (the enum is `4xx` only, because a `5xx` refuses nothing the
  caller did) — so the server was honest and no client could act on it, and a
  custody break was replayed on a backoff schedule for a condition guaranteed
  never to clear.

  The code catalogue now carries a second, **declared** classification
  (`Replay`) beside the derived `is_client_facing`, and the generator emits it
  as **`NON_RETRYABLE_CODES`** — a `frozenset[str]`, not enum members, since
  these are not refusals. `RetryPolicy` reads the response body's `code` at the
  point it decides to retry (the whole response is in scope there) and stops
  after one attempt on a match. It is a **deny list**: an unrecognised code is
  retried exactly as before, so a pinned client never abandons a transient
  failure a newer server introduced. No HTTP status, code spelling or response
  body changed; no `RejectionCode` member was added or removed.
  **`tckdb-client` 0.49.0 → 0.50.0.** No schema or migration impact.

### Added

- **Imaginary modes get a determination, not just a threshold.**
  `GET /api/v1/scientific/calculations/{ref}?include=imaginary_mode_projections`
  projects each imaginary mode onto rigid-body motion and onto a dihedral
  rotation about each rotatable bond, as ADR 0012 asked for. The eigenvectors
  are recovered from the Hessian already stored in `calc_hessian`, so **nothing
  new is stored**: no table, no column, no migration, and the block is
  recomputed per request. The depositor's declared `imaginary_disposition` is
  reported *beside* the computed determination with the raw overlaps and the
  thresholds applied; a disagreement is surfaced as `agreement: conflicts` and
  never silently resolved. Where no Hessian is stored the block reads
  `hessian_not_stored` -- not determinable, which is a different answer from
  "no residue found". Detail-only and opt-in by name, like `include=trust`.
  `available_sections.has_hessian` is new on every calculation record.
  `backend/scripts/ops/project_imaginary_modes.py` runs the same projections
  over a whole corpus; run over all 18 live records carrying an imaginary mode
  and a Hessian, every one determines as `internal_vibration` with
  `rigid_body_overlap` 0.0000, and none carries a declared disposition. ADR 0013
  held that this was uncomputable because TCKDB stores no displacement vectors;
  that claim is corrected in place.

- **Curated vs exploratory read profiles.** Every `/api/v1/scientific/*`
  endpoint accepts `?profile=exploratory|curated`. `exploratory` is the default
  and is explicitly labelled as carrying **no TCKDB recommendation**; `curated`
  raises the review floor to `approved` (applied both to searches and to
  detail-by-ref reads) and reports `approved_floor_only` — it does not claim a
  curator selected those records. The release-backed endorsement
  (`tckdb_curated_release`) is emitted only by `/scientific/releases/*`, where
  records really are resolved through an attributed selection. The resolved
  profile is echoed in every scientific response and in every dataset manifest.
- **Attributed, append-only release selections.** New `curation_policy`,
  `dataset_release` and `release_selection` tables record which candidate a
  named curator chose for a subject, under which policy version, with what
  rationale, for which release. Selections never mutate the record they point
  at; superseding appends a new row.
- **Immutable, citable dataset manifests.** New `release_manifest` and
  `release_artifact` tables freeze the manifest document, each shipped file's
  bytes, and a SHA-256 over both, bound to the Alembic revision, backend and
  wire-schema package versions, curation-policy version and review-policy
  version. Publication is the only write: later uploads, review progressing, a
  DOI being attached or a withdrawal cannot change what a citation resolves to.
  Whether the live database still agrees is reported separately and
  non-fatally as `live_divergence`. A release ships its selections *and* the
  full candidate set and review history behind them, each line carrying
  chemical identity (SMILES/InChIKey) and level-of-theory/software provenance
  so a deposited file is interpretable offline.
- Selections may only name records at or above the `approved` review floor,
  checked on append and re-checked at publication; a release that selects
  nothing cannot be published.
- Public read surface: `GET /api/v1/scientific/releases`, `/{handle}`,
  `/{handle}/manifest` (with live re-verification), `/{handle}/selections`,
  `/{handle}/artifacts/{path}`. Curator-gated writes under `/api/v1/releases`.
- `CITATION.cff`, `SECURITY.md`, and this changelog.

### Changed

- **A calculation's citation is a paper on every upload root, not a row id on
  five of them.** `CalculationPayload.literature_id` is replaced by an inline
  `literature` fragment, resolved to a `literature` row by the workflow layer.
  The field was the calculation block of `/uploads/conformers`,
  `/uploads/transition-states`, `/uploads/statmech`, `/uploads/thermo` and
  `/uploads/transport` (and their `/jobs/*` twins). A depositor has a DOI, not
  our primary key: supplying one required having already queried this database.
  Resolution moves to `resolve_and_persist_calculation_with_results`, the one
  seam every upload root reaches, instead of being repeated in three
  workflows.

  **`tckdb-schemas` 0.32.0 → 0.33.0.** Breaking: `literature_id` no longer
  validates on those roots. `SchemaBase` is `extra="forbid"`, so an old payload
  gets a 422 naming the field rather than a 201 with the citation silently
  dropped. **No stored value changes** — `calculation.literature_id` is
  unaffected and no migration is involved.

  The same field had already been removed from the reaction bundle, the
  network-PDep route and `CalculationIn`, each time by hand, each time on one
  root. The reason it kept surviving is that the no-FK-ids invariant was
  asserted by exactly one test over exactly one upload root. That walker now
  runs over **every** upload root, discovered from the live route table so a
  new route is covered the moment it is registered
  (`backend/tests/schemas/test_upload_roots_expose_no_fk_ids.py`). Generalising
  it surfaced four further FK-shaped fields on depositor-facing surfaces
  (`SCFStabilityPayload.source_calculation_id` / `source_artifact_id`,
  `CalculationScanPointCreate.geometry_id`,
  `ReactionParticipantUpload.species_entry_id`); each is read by the server
  today, so each is frozen in a documented inventory with its reason rather
  than removed unreviewed.

- **Three codes a client could import but never receive are no longer
  exported.** `app/api/code_catalogue.py` gains a `Reach` field, so an entry
  can now record that no request produces it — the middle case of a three-way
  distinction that previously had no spelling: catalogued and client-facing (a
  caller can provoke it), catalogued and not client-facing (a real guard no
  request can trip), not catalogued at all (not a code). Before this, a guard
  could only be recorded by telling clients they might receive it, or by
  deleting the entry and leaving the next reader to rediscover the literal.
  `Reach` governs the client enum and nothing else; promotion
  (`MESSAGE_PREFIX_CODES`) deliberately does not consult it.

  **`tckdb-client` 0.39.0 → 0.40.0**, and three `RejectionCode` members are
  removed: `TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH`,
  `APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH` and
  `IDEMPOTENCY_IN_PROGRESS`. Removing a member is breaking for an importer
  even when the code was unreachable, hence the minor bump. **No code changes
  value**, and all three keep a catalogue entry stating why no request reaches
  them. The two ownership guards stay in the code: against a bug five lines up
  they are a cheap tripwire, and in a database where a mis-attached
  calculation is a scientific error rather than a crash that is worth keeping.
  `idempotency_in_progress` is a contingency, not a milestone —
  `docs/specs/upload-idempotency-key-spec.md` lists it under *Optional* ("Only
  implement `idempotency_in_progress` if needed by the chosen approach") and
  the approach chosen does not need it; `app/api/idempotency.py` used to say
  "for v0", which does not distinguish a deferral from a decision, and now
  says which one it is.

- **A refusal that was recorded as unreachable is reachable, and is now
  tested.** `statmech_torsion_scan_calculation_owner_mismatch` was classified
  alongside the two above on the rule "an ownership guard is reachable exactly
  when the field it guards accepts a foreign row id". That rule is
  incomplete: a guard is also reachable when its key resolves in a namespace
  wider than the target's owner. `_persist_statmech_block` is shared by the
  species bundle, where the calc-key map is one species entry's own, and the
  PDep bundle, where it spans every species and transition state — and the
  PDep schema narrows a *species* statmech's keys to that species's own
  calculations but does not do the same for a *transition state*'s. A TS
  torsion naming a species-owned rotor scan therefore reaches the guard.
  Measured on the wire at `POST /api/v1/uploads/networks/pdep`; the code
  stays exported and `tests/api/test_api_network_pdep_ownership.py` provokes
  it, and the sibling `statmech_source_calculation_owner_mismatch`, through
  the route.

- **Two refusals stopped naming the function they were raised in.** The
  `detail` of the group-additivity missing-thermo guard and of the two keyset
  argument guards used to begin `create_applied_group_additivity: ` and
  `keyset_predicate: ` — the enclosing function in the position a client reads
  as a code. #164 stopped either being *promoted* into the `code` field; this
  reworks the messages themselves, so what a caller reads says what went wrong.
  Both entries are gone from `app/api/code_catalogue.py`, because a message
  with no token in the code position is not a code by any spelling.
  **No `RejectionCode` member is removed and `tckdb-client` is unchanged at
  0.38.0**: an `accidental_prefix` entry was never client-facing, so neither
  string was ever generated into the enum — checked with
  `generate_client_rejection_codes.py --check`, which reports the committed
  file up to date. Neither code was ever emitted, either: the runtime observer
  recorded 101 distinct `(status, code)` pairs across all three gates and
  neither appears. Both guards are reachable only by a direct programmatic
  call — `persist_thermo_upload` passes a row it has just flushed, so
  `session.get` cannot return `None` from any HTTP path — and the catalogue
  said otherwise, which is corrected.
- **The origin guard can now tell a code from a function of the same name.**
  `test_every_origin_still_defines_its_code` matched the code anywhere a
  double quote preceded it, so `"keyset_predicate"` in `__all__` satisfied the
  entry for `keyset_predicate` — the guard was blindest exactly where that
  class of defect lives, and #164 reworded that message with the guard staying
  green. Matching is now by syntactic position, shared with the catalogue's
  closure scan so the two cannot drift. That scan also reads `*_code=`
  arguments at any call, not only at a `raise`, which brings the six
  `*_handle_conflict` codes into static view (five of them are emitted by no
  test) and revealed one uncatalogued code, `database_error` — a dead
  `fallback_code` in the operational-error handler, now listed and annotated.
- `backend/pyproject.toml` now declares the repository's actual MIT license
  instead of `TBD — see repository root`.

### Notes

- **No DOI is minted.** The release machinery, manifest and checksums are
  implemented; depositing a release and recording its DOI is a documented
  manual step (`backend/docs/deployment/cutting_a_dataset_release.md`) to be
  run when a paper tag is cut. A DOI is not retractable and the corpus is not
  yet publishable.

## Earlier work

Before this file existed, changes were tracked only in the git history and in
`docs/decisions/`. Notable recent milestones, newest first:

- Stage 2 — scientific integrity blockers closed (#66).
- Execution-environment manifests recorded as provenance, not graded (#62, #64).
- Durable leases and heartbeats for async upload jobs.
- PDep scientific-integrity hardening: explicit pathway identity and solve
  inputs.
- Atom-resolved isotope identity for geometries and species entries.
- Raw artifact downloads gated behind authentication (ADR 0004, #48).
- Single-point energy and Cartesian Hessian extraction from uploaded ESS
  artifacts (#49, #51, #52).
- Lossless `tckdb.archive.v1` operator archive and restore path.
- Reproducibility assessments as an append-only curation projection.
