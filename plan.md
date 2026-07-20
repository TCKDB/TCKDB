# Remediation Plan — Backend Assessment Follow-up

**Started:** 2026-07-02
**Source:** `docs/audits/backend_assessment_2026-07-02.md`
**Companion narrative:** [`docs/guides/the_story_of_a_datapoint.md`](docs/guides/the_story_of_a_datapoint.md)

This is a living tracking document. Each phase is executed one at a time:
design → migration → models → schemas (backend + `tckdb-schemas`) → services/
workflows → client → tests → full verification. Every phase appends to the
**Progress log** and anything unexpected goes in the **Discovered issues**
section, so nothing found along the way is lost.

---

## Locked decisions (Calvin, 2026-07-02)

1. **Species identity key** = RDKit canonical SMILES + `charge` +
   `multiplicity` (unique constraint). `inchi_key` becomes a **non-unique
   search index**. Multiplicity derived from SMILES radical count becomes a
   *default*, not a hard validation — uploads may override (enables CH2(S),
   O2 states, open-shell singlets). Fixes both spin-state collisions and
   InChIKey tautomer merging. Deserves a new decision record (DR-0030).
2. **Migration posture:** only dev/test data exists in long-lived DBs → new
   Alembic revisions per policy (never touch `d861dfd60891`), but backfills
   may recompute from stored SMILES and **fail loudly on collision** — no
   curator-resolution workflow needed.
3. **Scope:** all four workstreams — Science P0, hygiene quick wins, CI
   hardening, query accessibility.

## Standing rules for every phase

- New Alembic revision with both `upgrade()` and `downgrade()`; deployed
  tables are append-only at the migration level (`.claude/rules/migration-rules.md`).
- Enums only in `app/db/models/common.py`; new model modules imported in
  `app/db/models/__init__.py` **and added to `__all__`** (the invariant test
  checks `__all__`).
- Upload schemas carry scientific content, never FK IDs. Mirror payload
  changes into `schemas/python/tckdb-schemas` and bump `tckdb-client`
  version on any client change.
- Fixed-unit columns for canonical physical quantities (`docs/unit_policy.md`).
- Run impact analysis before editing existing symbols; run the relevant test
  subset after each step and the full suite at phase end.
- Each schema-affecting phase gets a decision record if it embodies a
  scientific-policy choice.

---

## Phases

| # | Phase | Status | Outcome |
|---|-------|--------|---------|
| 0 | Hygiene quick wins | **done** | Suite green: 4,734 passed / 14 skipped / 0 failed (16m56s) |
| 1 | CI hardening (ruff+, mypy scoped, nightly full suite) | **done** | ruff+mypy+imports clean; suite 4,734 passed / 14 skipped / 0 failed |
| 2 | Hessian & raw-QC data storage (Part A) | **done** | Full slice + DR-0030 + migration (up/down verified); suite 4,740 passed / 14 skipped / 0 failed. Part B follow-ons deferred |
| 3 | Species identity redesign (spin states + tautomers) | **done** | DR-0031 + migration + core change; suite green (4,746 passed / 14 skipped / 0 failed). 322→0 fixture fallout resolved across ~30 files |
| 4A | Kinetics: tunneling enum + k∞/pressure-context | **done** | DR-0032 Part A; suite 4,750 passed / 0 failed. Caught+fixed real-ARC "Eckart" case-sensitivity |
| 4B | Kinetics: falloff/Troe + third-body efficiencies | **done** | model+migration+schemas+workflow+tests. Suite: only failures were the tckdb-schemas KineticsModelKind mirror-sync (enum-drift invariant caught it) — fixed, 68 drift tests pass; final confirm folded into Phase 5 run |
| 5 | Statmech: optical isomers + electronic levels | **done** | DR-0033 |
| 6 | Level of theory: spin-treatment axis (R/U/RO) | **done** | DR-0034; lot_hash re-hash migration. String normalization (G3.1) deferred |
| 4C | Kinetics: standalone PLOG/Chebyshev (no ME network) | **done** | DR-0032 Part C |
| 7 | Query accessibility: vocabulary/meta endpoints | **done** | Bulk export deferred |

**ALL PHASES COMPLETE.** Definitive full-suite verification (all phases
0–7 together): **4,763 passed / 14 skipped / 0 failed** (17m53s). Migration
chain linear (single head `f2b6d4a8c0e5`); ruff + mypy + import-sweep clean;
OpenAPI golden regenerated. 5 decision records (DR-0030–0034) + chemistry
rationale log + paper outline delivered.
| 5 | Statmech: optical isomers + electronic levels | not started | |
| 6 | LOT normalization + spin-treatment axis | not started | |
| 7 | Query accessibility: vocab endpoints + NDJSON bulk export | not started | |

Order rationale: 0 gets the suite green so every later phase has a trusted
baseline; 1 protects the heavy edits that follow; 2 is the user-flagged gap
and is additive (low risk); 3 is the riskiest (identity semantics) and
benefits from the fresh CI net; 4–6 are further schema work; 7 is API-only.

---

### Phase 0 — Hygiene quick wins

- [x] Add `"record_machine_review"` to `app/db/models/__init__.py` `__all__`
      → invariant suite passes (42/42).
- [x] Update `current_project_status.md` (Network / Network PDep reads exist;
      Transport standalone upload exists — verified `uploads.py:487`,
      `app/workflows/transport.py`; date refreshed).
- [x] Fix `docs/guides/core_concepts.md` review-state names to match
      `RecordReviewStatus` (`not_reviewed/under_review/approved/rejected/deprecated`).
- [x] Remove stale `clients/python/tckdb-client/` stub dir (README was
      git-tracked → `git rm`; no doc references found before removal).
- [x] Banner `ARCHITECTURE.md` — found it already carries the exact stale
      banner needed (lines 3–9); no change required.
- [x] Purge working-tree cruft: `backup_tckdb_dev_2026-03-*.sql`,
      `cookies.txt` (root + backend), `clean_run_log.md` — all confirmed
      untracked via `git ls-files` before deletion.
- [x] Verification: full suite green — 4,734 passed / 14 skipped / 0 failed.

### Phase 1 — CI hardening

- [x] Expand ruff rule set: `E,F,W,I` → `+B,SIM,C4,PTH,RUF`. Deliberate
      ignores documented in pyproject: `B008` (FastAPI Depends idiom, 454
      hits), `RUF002/003` (scientific unicode in docstrings), `UP` not
      adopted (~900 mechanical rewrites — churn-only change if ever);
      tests get per-file-ignores `RUF059`, `SIM117` (pytest idioms).
- [x] mypy scoped config (`app/db`, `app/schemas`, `app/chemistry`; 130
      files) + added to dev extras + installed in env. First run: only
      5 errors (2 RDKit-stub false positives in `torsion_fingerprint.py`,
      3 from the owner-mixin typing gap in `schemas/fragments/calculation.py`).
- [x] CI: lint + scoped-mypy steps added to `backend-ci.yml`; new
      `backend-nightly.yml` runs the full suite (test-full.sh) at 02:17 UTC.
- [x] Apply lint fixes to app/tests. Approach: safe autofixes first, then
      manual for the risky/subjective residual. Final rule set narrowed to
      `E,F,W,I,B,C4,RUF` (dropped SIM/PTH as churn). app + tests now
      **ruff-clean**. Real fixes worth noting: F821 latent-typo (see
      discovered-issues), 4× B904 exception chaining, 20 dead
      `import app.db.models` lines removed (verified via full import sweep).
- [x] Fix the 5 mypy errors: 3× RDKit-stub `type: ignore[assignment]` on
      AdjustQueryParameters attrs; `TYPE_CHECKING`-guarded field
      declarations on `CalculationOwnerRequiredMixin` (avoids Pydantic
      collecting them as fields). mypy: **Success, 0 issues, 130 files.**
- [x] Verification: ruff clean, mypy clean, import sweep clean.
      Full suite: **4,734 passed / 14 skipped / 0 failed (17m09s)** —
      confirms the 20 import removals + annotation change + exception-
      chaining edits are behavior-neutral.

### Phase 2 — Hessian & raw-QC data storage (user-flagged)

**Design:** `DR-0030`
— `calc_hessian` as a geometry-bound side table; the `HessianPayload`
carries its own `GeometryPayload` (dedup-safe, ordering-independent,
scientifically explicit binding) rather than inferring the geometry.

Part A (this pass) — **done, pending full-suite confirmation:**
- [x] DR-0030 written.
- [x] Enums: `HessianSource` + `ArtifactKind.hessian` (backend `common.py`
      and `tckdb_schemas.enums`).
- [x] Model `CalculationHessian` (calc_id PK, **geometry_id NOT NULL**,
      natoms, `float8[]` lower triangle, source enum, parser_version, note;
      CHECK cardinality = 3N(3N+1)/2; hub relationship).
- [x] `HessianPayload` in `tckdb-schemas` + field on
      `CalculationWithResultsPayload` + triangle-length validator (reads
      XYZ atom-count header).
- [x] Artifact allowlist `.hess`/`.fchk` under `hessian` kind; added to
      backend `_TEXT_KINDS` (both formats are ASCII).
- [x] Resolution persistence in `calculation_resolution.py` (dedupes the
      Hessian geometry through `resolve_geometry_payload`).
- [x] Migration `5eaf03c94f9b` (new revision on head; `ALTER TYPE
      artifact_kind ADD VALUE` + create `hessian_source` + create table;
      `create_type=False` to avoid double-create). **up/down/up verified.**
- [x] Tests `tests/services/test_calc_hessian.py` (6): payload validation,
      geometry-bound persistence, geometry dedup, DB CHECK. All pass.
- [x] Regenerated OpenAPI golden snapshot (additive only: `HessianPayload`
      + `HessianSource`, 72 insertions / 0 deletions).
- [x] Fixed a latent isolation bug the change exposed in
      `test_conformer_upload.py` (see discovered-issues) — scoped its
      geometry assertion to its own calculation instead of a global
      `order_by(Geometry.id)`.
- [x] Final full-suite confirmation: **4,740 passed / 14 skipped / 0
      failed (16m56s)** — Hessian storage + conformer-test fix + OpenAPI
      snapshot all green together.

Part B (deferred follow-ons, tracked here):
- [ ] `calc_freq_mode_displacement` (per-mode 3N vectors).
- [ ] Read-API surfacing of the Hessian on scientific calculation reads.
- [ ] `tckdb-client` builder support + version bump.
- [ ] ARC ingester `.fchk`/`.hess`/log-archive Hessian extraction.
- [ ] Geometry hardening: units column + per-atom isotope masses.
- [ ] Sibling raw-QC gaps (gradients, dipole derivatives, ⟨S²⟩, thermal
      corrections).

### Phase 3 — Species identity redesign

**Design:** `DR-0031`.

Done:
- [x] DR-0031 written.
- [x] `canonical_species_identity`: dropped the multiplicity hard-check
      (payload multiplicity is authoritative); charge still validated.
- [x] `resolve_species`: dedup on `(smiles, charge, multiplicity)`.
- [x] Model: `uq_species_identity` on `(smiles, charge, multiplicity)`;
      `ix_species_inchi_key` (non-unique).
- [x] `_canonical_species` public-ref basis moved from `inchi_key` to
      `smiles` (else tautomer siblings collide on `ix_species_public_ref`).
- [x] Migration `a7c1e9d2f4b8` (up/down verified).
- [x] Tests `tests/services/test_species_identity.py` (6): singlet vs
      triplet CH2 distinct; tautomer split; cross-notation dedup;
      charge-mismatch rejected; InChIKey→multiple species. All pass.
- [ ] Resolve test-fixture fallout (below), then full-suite green.

**Test-fixture fallout (in progress): 322 failures.** Two collision modes:
1. **Within-test duplicate identity** — a helper creates 2 species with
   the same fixed smiles in one transaction (e.g. `_make_reaction_entry`
   → `_make_species` ×2, both `"CCO"`). Guaranteed failure. FIXED in the
   two trust-evaluator files by defaulting `_make_species` to a unique
   placeholder smiles (trust never parses smiles).
2. **Cross-test collision** — a test's fixed common smiles (`"O"` 33×,
   `"CCO"` 28×, `"C"` 24×, `"[H]"`) collides with a species another
   (`db_engine`-committing) test already committed to the shared
   session-scoped DB. Even rollback-isolated tests hit this because the
   INSERT fails at flush against committed rows.

Root of mode 2 = `db_engine`-committing tests leaking species. Fix
strategy: uniquify fixture species so each is globally distinct (trust
layer / calc tests never parse smiles, so unique placeholders are safe),
and for real-chemistry tests use distinct valid molecules or balanced
isomer pairs.

**Fixes applied (2026-07-02, 33 failing files):**
- Shared `_factories.make_species`: `smiles` now optional, defaults to a
  unique value (`unique_smiles()`); fixes all shared-factory read tests
  when their local wrappers drop the fixed smiles.
- api/scientific read files (conformers, networks, TS, transport,
  statmech, artifacts, calculations, calc-search): dropped fixed
  `smiles="X"` from `make_species(...)` calls (sed) → unique per call;
  fixed the odd hardcoded-smiles assertion to compare `species.smiles`.
- Local raw-SQL helpers (irc, param-persist/extract, freq_modes,
  calc_hessian): species smiles now the unique `inchi_key`.
- Param-default helpers (calc-resolution/scan, model_constraints):
  `smiles` default derives from unique `inchi_key`.
- trust-evaluator ×2, machine_review, cli, geometry_validation_wiring:
  unique-per-call smiles.
- reaction_upload: reuses-graph now ethanol→dimethyl-ether (distinct,
  balanced isomers); pseudo test unique smiles.
**Convergence:** round-1 322 failures → round-2 71 → round-3 (running).
Round-2→3 fixes:
- Bug I introduced then fixed: the raw-SQL sed reused `:inchi_key` for both
  the `smiles` (text) and `inchi_key` (CHAR) columns → Postgres
  "inconsistent types deduced for parameter" (AmbiguousParameter). Fixed
  by binding a separate `:smiles` param. (calc_hessian, param-extract/
  persist, irc, freq_modes — all green after.)
- `kw.pop("smiles", "CCO")` / `smiles: str = "CC"` default-smiles helper
  patterns (artifacts, calc-search, get_species_thermo, species_thermo)
  → default `None` so make_species uniquifies.
- Hardcoded `canonical_smiles == "CCO"` detail assertions (calculations,
  statmech, transport, conformers) → compare `species.smiles`.
- Collapse/pre-collapse tests (species_search, search_species) → spin
  variants (same smiles, mult 1 vs 3).
- factories many-reactions loop → unique species per iteration.
All touched files pass individually; full-suite round-3 confirming.

**Round-3→4:** 71 → 5 → 1. The 5 stragglers were cross-test collisions
only visible in full-suite ordering (a committing test persists a common
smiles that a later test's raw insert collides with). Fixed 4 via
order-independent unique smiles / spin-variants. The last one,
`test_reaction_upload::...reuses_graph...`, needs a balanced two-species
reaction (ethanol→dimethyl-ether); made its `_make_species_entry`
get-or-create by identity so it reuses an already-committed `CCO` instead
of colliding. Round-4 still failed: the traceback showed the collision had
moved to the **species_entry** insert — reusing a committed `CCO` species
that already had a ground/minimum entry means a second identical entry
violates the species_entry identity constraint. Final fix: get-or-create
the *entry* too (reuse the reused species' existing ground/minimum entry).
Verifying with services+workflows.

**Broader lesson (for a follow-up phase):** this whole 322→1 saga is the
symptom of a shared session-scoped test DB where `db_engine` tests commit
without rollback, so fixtures leak across tests. The identity change just
made previously-hidden fixture aliasing (same molecule as "distinct"
species via fabricated inchi_keys) fail loudly. The durable fix is a
per-test transaction-rollback harness (the assessment's "no isolated test
tier" finding) — recommended as its own cleanup, separate from the science
phases.
- [ ] Chemistry layer: canonicalization function (tautomer-preserving
      canonical SMILES), multiplicity default-vs-override logic; keep hard
      validation only for impossible combos (e.g. even-electron mult=2 —
      decide: warn vs reject).
- [ ] Model: `species.canonical_smiles` column; unique constraint swap;
      `inchi_key` non-unique index. Check every consumer of the old
      constraint (species_resolution race handling relies on IntegrityError).
- [ ] Migration: add col → backfill by recompute → **raise on collision** →
      constraint swap; downgrade restores.
- [ ] Ripple: species_resolution, lookup endpoints, structure search,
      read schemas, docs (`species_design.md`), tckdb-schemas validators,
      client (bump).
- [ ] Tests: CH2 singlet vs triplet distinct; O2 states; 2-pyridone vs
      2-hydroxypyridine distinct; cross-notation SMILES still dedupes;
      existing-data backfill; race-condition path.

### Phase 4 — Kinetics representability

**Design:** `DR-0032`.

Part A — **done, pending full-suite confirmation** (gaps G5.3, G5.4):
- [x] Enums `TunnelingModel` (none/wigner/eckart/sct/other) and
      `PressureContext` (high_p_limit / apparent_at_pressure /
      pressure_dependent) in backend `common.py` + `tckdb_schemas.enums`.
- [x] Model: `kinetics.tunneling_model` Text→enum; added
      `pressure_context` (enum, nullable) + `pressure_bar` (nullable) with
      CHECKs (pressure_bar>0; apparent_at_pressure requires pressure_bar).
- [x] Migration `b8d2f0a3c6e1`: text→enum conversion (maps known tokens,
      folds other→`other`) + 2 columns + 2 CHECKs. up/down verified.
- [x] Schemas: `entities/kinetics.py` (Base/Update), `kinetics_upload.py`,
      bundle `computed_reaction_upload.py` — tunneling→enum, pressure
      fields + validator; removed tunneling text-normalization.
- [x] Resolution (`kinetics_resolution.py` ×2) + `computed_reaction.py`
      workflow pass the new fields through.
- [x] Tests: 4 new in `test_kinetics_upload.py` (tunneling enum persists;
      k∞ persists; apparent-at-pressure persists with pressure; validator
      rejects apparent without pressure_bar). Kinetics subset 202 pass.
      OpenAPI golden regenerated; ruff + mypy clean.
- [x] **Real-data robustness fix:** first full-suite run failed 42 tests —
      all real ARC payloads carry `tunneling_model: "Eckart"` (capitalized),
      which the strict lowercase enum rejected (Pydantic coercion is
      case-sensitive). Added `normalize_tunneling_model` (case-insensitive,
      unknown→`other`, enum-aware for the read path) as a `mode="before"`
      validator on all kinetics schemas — mirrors the migration and makes
      the enum tolerant of varied producer conventions. Updated 2 test
      assertions that checked the old capitalized value.
- [ ] Full-suite confirmation v2 (running).

Parts B & C — **deferred follow-ons** (designed in DR-0032):
- [ ] Part B falloff: `kinetics_falloff` (low-P Arrhenius + Troe/SRI) +
      `kinetics_third_body_efficiency` (collider species FK); enum +=
      `troe`/`lindemann`/`sri`. (Assessment G5.1 — top adoption blocker.)
- [ ] Part C standalone PLOG/Chebyshev on reaction-level kinetics (no fake
      ME network); enum += `plog`/`chebyshev`. (G5.2.)
- [ ] `tckdb-client` builder support + version bump for the new fields.

### Phase 5 — Statmech completeness

- [ ] `statmech.optical_isomers` (int, ≥1, default-null=unknown semantics
      documented) — entropy contribution auditability.
- [ ] `statmech_electronic_level` table: (energy_cm1, degeneracy) pairs,
      ordered; covers OH ²Π, O(³P), halogens.
- [ ] Migration, schemas, resolution service, reads, client bump, tests
      (OH with spin-orbit levels round-trip; Arkane-style input parity).

### Phase 6 — Level-of-theory integrity

- [ ] Normalization layer in LOT resolution: case-folding, whitespace,
      dispersion-token extraction from method strings (`-D3(BJ)` etc.),
      basis alias table (curated, small, growable). Applied *before*
      `lot_hash`; DR documenting normalization rules.
- [ ] `spin_treatment` column/enum (`restricted/unrestricted/restricted_open/unknown`)
      as part of LOT identity + hash.
- [ ] Migration incl. re-normalization backfill with loud collision report
      (dev-data posture); dedupe merged rows.
- [ ] Tests: "B3LYP" == "b3lyp"; "B3LYP-D3(BJ)/def2-TZVP" method-string vs
      split-field uploads resolve to one LOT; UCCSD(T) ≠ ROCCSD(T).

### Phase 7 — Query accessibility

- [ ] `GET /scientific/meta/{families|methods|bases|software}` vocabulary
      endpoints (distinct values + counts, review-status-filtered).
- [ ] Curator-gated streaming NDJSON export (species+thermo first; kinetics
      second) above the 200-row cap; document as the bulk path.
- [ ] Tests + OpenAPI snapshot update + docs (query cookbook entries).

---

## Progress log

*(append-only; newest last)*

- **2026-07-02 — Plan created.** Scope and identity decisions locked with
  Calvin. Baseline: full suite 4,733 passed / 14 skipped / 1 failed
  (`record_machine_review` missing from models `__all__`).
- **2026-07-02 — Phase 0 started.** `__all__` fix landed and invariant
  suite green (42 passed). Status doc corrected against verified router
  registrations; while verifying, found the **Transport upload row was also
  stale** (standalone `POST /uploads/transport` + workflow exist) — fixed.
  `core_concepts.md` review states aligned with the enum.
  `ARCHITECTURE.md` needed no change (already bannered). Remaining Phase 0
  items (stub-dir removal, cruft purge, full-suite verification) blocked by
  a transient harness outage (Bash safety classifier unavailable); resume
  from here.

## Agent-implementation review (CHEMKIN importer + bulk export)

- **CRITICAL bug caught + fixed in BOTH adapters — NASA a/b convention.**
  My delegation prompt mis-stated the convention as `a`=high/`b`=low. The
  authoritative TCKDB convention (verified in `tckdb_schemas.thermo` and
  `scientific_read/thermo.py`) is **`a1..a7`=LOW, `b1..b7`=HIGH**, and
  CHEMKIN lists high-T coefficients first. Both agents flagged the
  contradiction but followed the (wrong) prompt, so the importer mapped
  CHEMKIN-high→`a` and the exporter emitted `a`→CHEMKIN-high — both
  backwards, and *mutually consistent*, so a naive round-trip would have
  passed with silently wrong thermochemistry. Fixed: importer
  (`clients/python/adapters/chemkin/tckdb_chemkin/payloads.py` `_nasa_payload`,
  CHEMKIN-high→`b`, low→`a`) and exporter
  (`backend/app/services/scientific_read/chemkin_serialize.py` `_nasa_card`,
  CHEMKIN high←`b`, low←`a`), plus the two affected tests. Round-trip now
  preserves intervals AND stores the correct convention (proven by direct
  check). **Lesson: neither adapter's own test pinned the coefficient→
  interval mapping, so only a real import→export→diff round-trip catches
  this — build it (both agents deferred it as M4/M5).**
- **Confirmed gap (follow-on, not an agent error): simple third-body A-units.**
  `kinetics_upload.py:280` sets `molecularity = len(reactants)`, so a simple
  `A+B+M` reaction's order-N A-units (`cm6_mol2_s`) are rejected. Falloff
  reactions validate fine (main line is order-2 k∞). Cleanly accepting
  simple-third-body uploads needs a backend third-body marker. The importer
  emits the correct physics; document + defer.
- **Inherent drift risk:** importer form-mapping (`forms.py`, client pkg) and
  exporter serializer (backend pkg) live in different packages and cannot
  share a module. They currently agree on the form set
  (troe/lindemann/sri/plog/chebyshev ↔ LOW/TROE/SRI/PLOG/CHEB). The
  round-trip integration test is the guard against future drift.

## Agent review round 2 (third-body marker + CHEMKIN round-trip test)

- **Third-body A-units marker — verified correct.** New `kinetics.is_third_body`
  column (migration `a3f1c7e9b2d5`, linear head); validator now uses
  `len(reactants) + (1 if is_third_body and falloff is None else 0)` — simple
  `A+B+M` accepts order-N units, falloff keeps order-2 k∞, ordinary reactions
  stay strict. Importer sets the flag only for simple `+M`. Tests + golden green.
- **Round-trip test caught a SECOND copy of the NASA a/b bug (I fixed it).**
  The `SelectedThermo.to_dict` NDJSON export path
  (`backend/app/services/scientific_read/export.py`) had the same backwards
  convention (`high_coefficients=[a1..a7]`) that I'd fixed in the CHEMKIN
  serializer — a separate code path re-encoding the same NASA data. The
  round-trip surfaced it via a strict-xfail; I fixed export.py and converted
  the xfail into a permanent regression guard. **This is the entire reason the
  round-trip test exists.**
- **Fixed my own review-fix bugs, exposed by the consolidated run:**
  (a) inline `sys.path.insert(0, chemkin_adapter)` in the round-trip test
  shadowed the backend `tests` package → changed to `append`; (b) the
  round-trip agent's `tests/integration/conftest.py` collided with the bare
  `from conftest import` in `test_db_name_resolution.py` → inlined the path
  bootstrap and deleted the conftest; (c) ruff I001/RUF100/B905 in the new
  test → fixed (E402 isn't an enabled rule here).
- **Diagnosed a PRE-EXISTING ordering fragility (not agent-caused, left as-is):**
  all `tests/workflows/*` files use `with Session(db_engine) as session,
  session.begin():`, which COMMITS (escapes per-test rollback). Workflow tests
  leak committed species (e.g. water, content-derived public_ref); the new
  export tests' raw-insert factories collide IF workflows run first. The
  natural collection order (`services` < `workflows`) avoids it, so the full
  `pytest tests/` passes; only an unnatural explicit file order triggers it.
  Fixing would touch 10 established files — out of scope; noted as a hygiene
  follow-on (consider `create_savepoint`-style isolation for workflow tests).

## Real-data validation (RMG ammonia–methane oxidation mechanism)

Committed a real RMG output as a round-trip fixture:
`backend/tests/integration/fixtures/rmg_ammonia_methane/` (chem.inp +
species_dictionary.txt + tran.dat; 21 species NH₃/CH₄/NOx, 64 reactions;
provenance: run `OriginalEnas_L1`, emulating García-Ruiz et al., Energy &
Fuels 38 (2024) 1399–1415; see PROVENANCE.md). Real data immediately exposed
two importer bugs (both fixed by hand before delegating):

- **`THERM ALL` keyword** — RMG's abbreviated thermo header wasn't in the
  parser's `_BLOCK_STARTS`, so the entire inline NASA block was skipped
  (thermo=0). Fixed: `parser.py` maps `THERM` → THERMO.
- **`(+M)` + Chebyshev** — RMG writes Chebyshev P-dependent reactions as
  `R(+M)<=>P(+M)` + `TCHEB/PCHEB/CHEB` with a dummy `1.0 0 0` main line and
  NO `LOW/`; the normalizer treated `(+M)` as Lindemann/Troe falloff and
  crashed. Fixed: `normalizer.py` routes `(+M)` + Chebyshev block → chebyshev.

After these, the importer fully parses + resolves the real mechanism
(forms: modified_arrhenius/arrhenius/troe/chebyshev; RMG adjlist identity
resolves NH3→N, O2→[O][O], etc.). Delegated the full real-data round-trip
(+ remaining quirk fixes: dummy Chebyshev Arrhenius, RMG bracketed names,
efficiency colliders) to an agent for review.

## Cantera-as-ground-truth export validation (duplicate robustness)

Added `validate_chemkin_mechanism(files)` (chemkin_serialize.py) — writes the
export to a temp dir, runs strict `ck2yaml.convert` AND `cantera.Solution()`
load; raises on any Cantera rejection. Wired as two guard tests on the real
ammonia-methane fixture (positive: real export loads clean; negative:
DUPLICATE-stripped export is rejected). This is the authoritative guard
against emitting a file a downstream interpreter would reject.

Cantera validation immediately caught a real export bug + two mistakes I made:
- **NASA card column-80 bug (real, pre-existing):** `_nasa_card` put the
  card-index digit in column 76; Cantera's NASA-7 grouping requires column 80,
  so no species' thermo linked. Our lenient parser masked it. Fixed.
- **My `_dup_key` "improvement" was a regression:** canonicalizing reversible
  direction OVER-declared duplicates (8 vs the 6 Cantera actually demands, per
  `Kinetics::checkDuplicates`). Reverted to the same-direction key; the
  original count (6) was correct.
- **My `validate_chemkin_mechanism` was incomplete:** `ck2yaml.convert` only
  translates CK→YAML and never checks duplicates; the check runs only on
  `Solution()` instantiation. Added the `Solution` load so the guard fulfills
  its promise.
All caught because Cantera is the objective oracle — exactly the T3
`fix_cantera.py` philosophy the user pointed to. Real round-trip: 12 passed;
consolidated adapter+integration+export: 83 passed / 2 skipped; ruff+mypy clean.

**Design decision recorded:** core upload API stays JSON/content-only; raw
CHEMKIN/transport file parsing lives in the client adapter (or a future
dedicated `/ingest/chemkin` bundle endpoint), never by overloading the
`/uploads/*` endpoints. Formats live at the edges; science lives in the core.

## Discovered issues & new gaps

*(anything found during execution that wasn't in the assessment)*

- **2026-07-02 — `current_project_status.md` Transport row was also stale**
  (beyond the Network rows the assessment caught): it claimed no standalone
  transport upload workflow, but `POST /uploads/transport` and
  `app/workflows/transport.py` exist. Corrected. Lesson: the status doc has
  no update trigger — consider a CI check or a "status doc updated?" PR
  checklist item (relates to assessment §9 "watch the doc corpus").
- **2026-07-02 — Lint debt was larger than the assessment implied:** the
  old `E,F,W,I` config was never actually enforced anywhere — `ruff check`
  had ~170 E/F/W/I violations in `app/` (122 unsorted imports, 72 unused
  imports) and ~230 in `tests/` before any rule expansion. The "no lint in
  CI" finding understated it: there was no lint run at all.
- **2026-07-02 — LATENT BUG (fixed): wrong type annotation in
  `computed_reaction.py`.** `_anchor_species_calculation_to_observation`
  annotated `calc_in: CalculationIn`, but `CalculationIn` is not imported
  in that module (it lives in `network_pdep.py`); the caller passes a
  `ComputedReactionCalculationIn`. Harmless today only because the module
  uses `from __future__ import annotations` (PEP 563 stringifies it), but
  `typing.get_type_hints()` on that function — or any future eager-eval —
  would raise `NameError`. A copy-paste from the network_pdep workflow.
  Corrected to `ComputedReactionCalculationIn`. Caught by ruff F821.
- **2026-07-02 — DANGER logged: F401 auto-fix is unsafe in this repo.**
  The codebase pervasively uses *implicit re-exports* (import a symbol in
  module A, then `from A import X` in module B, with no `__all__`). Running
  `ruff --select F401 --fix` removed re-exported symbols such as
  `SchemaBase` from `app/schemas/common.py`, breaking imports app-wide
  (caught by a full import sweep, then reverted). Resolution: classified
  all 103 F401 (77 protected re-exports + 6 pytest-fixture conftest imports
  + 20 genuinely-dead `import app.db.models` lines); removed only the 20
  dead ones, per-file-ignored F401 on the 21 verified re-export hubs +
  conftest. **Follow-up (deferred):** convert those hubs to explicit
  `__all__` / `import X as X` re-exports and re-enable F401 there. Anyone
  touching those files must NOT blindly `ruff --fix` F401.
- **2026-07-02 — POSSIBLE GAP for Phase 4 (kinetics): TS frequency
  evidence dropped from kinetics trust.** In
  `app/services/scientific_read/kinetics.py`, `provenance.ts_freq_calculation_id`
  is extracted but never passed into `_evidence_breakdown` (only
  `ts_opt_calc_id` and `ts_sp_calc_id` are). So the kinetics trust/evidence
  breakdown ignores whether the TS has a frequency job — arguably the most
  important saddle-point confirmation. Left as-is (fixing changes trust
  scoring); revisit when doing Phase 4 or a trust-layer review. Marked with
  an in-code NOTE.
- **2026-07-02 — PRE-EXISTING MIGRATION DRIFT (new finding, Phase 2).**
  `alembic revision --autogenerate` against a *freshly migration-built* dev
  DB reports changes unrelated to any pending work: `molecular_property_observation`
  columns typed `INTEGER` in the DB but `BigInteger` in the model
  (`literature_id`, `software_release_id`, `workflow_tool_release_id`), a
  `created_at` timezone mismatch, a model-dropped `updated_at` still in the
  DB, and an `ix_species_entry_mol_gist` index present in the DB but not
  declared in model metadata. This means the models and migrations do **not
  round-trip** — a real integrity gap the CI does not catch (CI has a
  single-head check and an OpenAPI snapshot but **no `alembic check` /
  autogenerate-is-empty gate**). I scoped my Hessian migration to exclude
  this drift (documented in the revision docstring). **Follow-up:** add an
  `alembic check`-style CI gate and reconcile the `molecular_property_observation`
  model-vs-migration mismatch + the undeclared gist index in a dedicated
  revision. Candidate for Phase 1's CI-hardening scope or a new small phase.
- **2026-07-02 — LATENT TEST ISOLATION BUG (fixed), exposed by Phase 2.**
  `tests/workflows/test_conformer_upload.py::test_persist_conformer_upload_creates_expected_rows`
  asserted `select(Geometry).order_by(Geometry.id)` had `natoms == 1` — a
  **global, unscoped** query. The `db_engine`-direct service tests COMMIT
  (the session-scoped test DB is not rolled back for these), so the shared
  DB accumulates geometries across tests. My new `test_calc_hessian.py`
  (collected under `tests/services/`, before `tests/workflows/`) commits an
  H2 `natoms=2` geometry, which became the lowest-id row and broke the
  conformer test's global assertion. Root-caused and fixed by scoping the
  assertion to the calculation's own input-geometry link. **General risk:**
  any `db_engine`-committing test that does a global `order_by(id)` /
  `select(X)` with no filter is isolation-fragile; there may be more of
  these latent (grep candidate for a future hygiene pass). This is the
  clearest instance of the assessment's "tests need a DB-free / better-
  isolated tier" theme.
- **2026-07-02 — Stale dev DB re-confirmed the documented trap.** The dev
  DB had drifted (public_ref columns from in-place-edited in-flight
  revisions) and polluted the first autogenerate; drop/recreate +
  `alembic upgrade head` fixed it, per `.claude/rules/migration-rules.md`.
  Reminder that manual dev DBs need periodic rebuilds.
- **2026-07-02 — Incomplete feature found: proximity atom-mapping stub.**
  `app/chemistry/torsion_fingerprint.py` had a dead `xyz_coords` array and
  a comment promising proximity-based XYZ→reference atom assignment, but the
  implementation falls back to element-order ("for now"). Removed the dead
  code; the element-order fallback is what actually runs. Not a bug (the
  fallback is correct for the common case) but a documented half-feature —
  relevant if conformer-basin matching accuracy is ever revisited.
- **2026-07-02 — `ArtifactKind` naming note for Phase 2:** the enum lives at
  `common.py:187-192`; adding `hessian` must also extend the per-kind
  extension allowlist in `schemas/python/tckdb-schemas/tckdb_schemas/fragments/artifact.py:22-30`
  (two repos-in-one: backend enum + shared schemas package must move together).
