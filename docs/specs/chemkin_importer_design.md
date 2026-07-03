# CHEMKIN Mechanism Importer — Design Spec

**Status:** design, ready for implementation.
**Author intent:** a rigorous, implementation-ready plan for an adapter that
ingests a CHEMKIN-format mechanism (the lingua franca of combustion/kinetics
modeling — emitted or consumable by RMG, Cantera, EStokTP, FlameMaster, and
most in-house codes) into TCKDB through the existing content-only upload API.
Pairs with `bulk_export_design.md` (the reverse direction).

---

## 1. Why this adapter is the highest-leverage producer integration

TCKDB is `workflow_tool`-agnostic and uploads are content-only, so the effort
of onboarding a producer is entirely in the **adapter** that maps the
producer's native output to TCKDB payloads. Rather than write one adapter per
code (ARC, RMG, EStokTP, the RWTH code, …), a single CHEMKIN importer covers
*any* tool that can emit a CHEMKIN mechanism — which is nearly all of them.
The Phase 4B/4C kinetics work (Troe/Lindemann falloff, third-body
efficiencies, standalone PLOG/Chebyshev) was done specifically so CHEMKIN
kinetics forms have a lossless home.

## 2. Scope

**In scope (v1):**
- Parse a CHEMKIN gas-phase mechanism: `ELEMENTS`, `SPECIES`, `THERMO`
  (inline or separate `therm.dat`, NASA-7), `REACTIONS`, and a separate
  transport file (`tran.dat`).
- Kinetics forms: elementary (modified Arrhenius), reversible/irreversible,
  duplicate (`DUP`), third-body (`+M`), falloff (`(+M)` with `LOW`/`TROE`/
  `SRI`), `PLOG`, `CHEB` (Chebyshev). (These map to the models added in
  DR-0032.)
- Thermo: NASA-7 two-range polynomials → `thermo` + `thermo_nasa`.
- Transport: LJ + dipole + polarizability + rotational relaxation →
  `transport`.
- Species identity resolution via a **required structure map** (§5).
- Provenance: attach a single `literature`/mechanism citation to every
  imported record; mark `scientific_origin` appropriately (§7).
- Idempotent re-import of the same mechanism (§8).

**Out of scope (v1) — enumerate explicitly:**
- Surface/heterogeneous chemistry (`SITE`, coverage dependence), plasma,
  ion chemistry beyond neutral gas-phase.
- Landau–Teller (`LT`), `REV` explicit reverse (parse + warn; see §6.7),
  `FORD`/`RORD` custom orders, `UNITS`-per-reaction overrides beyond the
  header, real-gas/`XSMI`.
- Reconstructing calculation-level provenance (Hessians, LOT, geometries):
  CHEMKIN carries none. Imported rows are results without a provenance
  chain — legitimate under the four-bucket model, just lower trust.

## 3. Architecture (five stages, each independently testable)

```
CHEMKIN files + structure map
        │
        ▼
[1 Lexer/Parser]  → raw AST (blocks, reactions, thermo entries, transport rows)
        │
        ▼
[2 Normalizer]    → SI-normalized values, canonical kinetics-form tagging,
        │            unit conversions resolved against the REACTIONS header
        ▼
[3 Identity resolver] → each CHEMKIN species name → {smiles, charge,
        │                multiplicity} via the structure map; FAIL LOUD on
        │                any unmapped species (never guess identity)
        ▼
[4 Payload builder]  → TCKDB upload payloads (thermo / transport / kinetics),
        │               referencing species by scientific content only
        ▼
[5 Uploader]         → generic tckdb-client; async bundle for volume;
                        idempotency keys; per-record error report
```

The importer **never writes the DB directly** — it produces payloads and
POSTs them, honoring the submission-scoped, content-only ingestion model.
Stages 1–4 are pure functions (no network, no DB) and are unit-tested against
mechanism fixtures; only stage 5 needs a live API.

## 4. Inputs

| Input | Required | Notes |
|---|---|---|
| Mechanism file (`chem.inp`) | yes | ELEMENTS/SPECIES/THERMO?/REACTIONS |
| Thermo file (`therm.dat`) | if THERMO not inline | NASA-7 |
| Transport file (`tran.dat`) | optional | LJ etc.; import transport when present |
| **Structure map** | **yes** | name → structure; the crux, see §5 |
| Mechanism citation | strongly recommended | DOI/title for the `literature` link |
| REACTIONS header units | parsed from file | e.g. `REACTIONS CAL/MOLE MOLES` |

## 5. Species identity resolution — the crux

CHEMKIN species are arbitrary names (`CH3`, `AR`, `C2H5OH`, RMG's `S(123)`);
the format carries **no structure**. TCKDB identity is
`(canonical SMILES, charge, multiplicity)` (DR-0031), so every name must be
mapped to a structure. **The importer must not guess** — a wrong guess
silently corrupts identity, exactly the failure Phase 3 fixed.

Accepted structure-map sources, in priority order:
1. **RMG species dictionary** (`species_dictionary.txt`): adjacency lists →
   RDKit mol → canonical SMILES + multiplicity. Directly available for any
   RMG-generated mechanism (the largest single source of CHEMKIN files).
2. **User-supplied map file** (CSV/JSON): `name, smiles[, charge, multiplicity]`.
3. **Inline structure comments**: some tools annotate species with SMILES/InChI
   in `!`-comments; parse when present.
4. **A curated element-only fallback for bath gases** (`AR`, `HE`, `N2`, `M`):
   a small built-in table for monatomic/diatomic inerts — these have
   unambiguous structure and appear in nearly every mechanism.

Resolution policy:
- Charge/multiplicity: from the map if given, else default (charge 0;
  multiplicity from the RMG adjlist or from SMILES radical count as the
  *default*, override-allowed per DR-0031).
- Any species in the SPECIES/REACTIONS/THERMO blocks that cannot be resolved
  → **hard error listing the unmapped names**; the run aborts before any
  upload (all-or-nothing identity). A `--allow-pseudo` flag may map a named
  lump to a `pseudo` species (MoleculeKind.pseudo) for lumped/surrogate
  species, requiring an explicit source note.
- Cross-check: the NASA thermo entry's elemental composition (the `C 2 H 6`
  columns) must match the resolved structure's formula; mismatch → error.
  This catches structure-map mistakes cheaply.

## 6. Kinetics-form mapping (CHEMKIN → TCKDB)

TCKDB target models are those from DR-0032. The parent `kinetics.model_kind`
selects the form; sub-tables carry the parameters.

| CHEMKIN construct | TCKDB `model_kind` | Where params go |
|---|---|---|
| `A + B <=> C + D  A n Ea` | `modified_arrhenius` (or `arrhenius` if n=0) | `kinetics.a/n/ea_kj_mol` |
| `... => ...` (irreversible) | same | `reaction_entry.reversible=false` |
| `DUP` (duplicate) | same | **two `kinetics` rows on one reaction** (append-only results — natural fit) |
| `A + B (+M) <=> ... ` + `LOW/` | `lindemann` | k∞ on `kinetics.a/n/ea`; k0 in `kinetics_falloff.low_*` |
| `... LOW/ ... TROE/α T*** T* [T**]/` | `troe` | `kinetics_falloff.troe_*` |
| `... LOW/ ... SRI/a b c [d e]/` | `sri` | `kinetics_falloff.sri_*` |
| `A + B + M <=> ...` (simple third body) | `arrhenius`/`modified_arrhenius` | efficiencies only; no falloff row |
| collider efficiency list `H2O/6/ AR/0.7/` | (any of the above) | `kinetics_third_body_efficiency` rows (collider resolved via structure map) |
| `PLOG/ P A n Ea/` (repeated) | `plog` | one `kinetics_plog` row per pressure, `entry_index` in file order |
| `CHEB/`, `TCHEB/`, `PCHEB/` | `chebyshev` | `kinetics_chebyshev` (n_T×n_P matrix + T/P domain) |
| `REV/ A n Ea/` (explicit reverse) | see §6.7 | v1: parse + warn (deferred) |

Reaction identity notes:
- `(+M)` / `+M` and the collider list are **not** part of the reaction's
  stoichiometry — the reaction is the real reactants→products; the third-body
  behavior is attached as falloff + efficiency records. This is exactly why
  those were modeled separately (DR-0032B).
- `DUP` reactions are two result rows on the same `reaction_entry` — the
  append-only result model handles this with no special casing.
- Reactant/product multiset → resolved species entries → elemental-balance
  check runs at ingestion (existing). Mechanisms are balanced, so this is a
  guard, not a burden.

### 6.7 REV handling
CHEMKIN `REV/` gives an explicit reverse-rate expression (rather than
computing it from equilibrium). TCKDB has no reverse-linkage field yet
(assessment gap G5.5). v1: parse and **warn** (drop the reverse expression,
keep the forward, note it in the import report). A follow-on adds a
reverse-rate linkage; do not silently invent one.

## 7. Units mapping

Read the REACTIONS header (`REACTIONS <Ea-units> <A-units>`); defaults are
`CAL/MOLE MOLES` per the CHEMKIN standard.

**Activation energy** → `ea_kj_mol` (fixed unit): `CAL/MOLE`→×4.184e-3;
`KCAL/MOLE`→×4.184; `JOULES/MOLE`→×1e-3; `KJOULES/MOLE`→×1; `KELVIN`→×R
(8.314e-3 kJ/mol/K); `EVOLTS`→×96.485. (`ea_kj_mol` has no sign constraint —
negative Ea is preserved.)

**A-factor units** → `ArrheniusAUnits` enum, molecularity-aware
(TCKDB already validates A-units against molecularity):
- `MOLES` + bimolecular → `cm3_mol_s`; unimolecular → `per_s`;
  termolecular / falloff-k0 → `cm6_mol2_s`.
- `MOLECULES` → the `*_molecule*` variants.
- The low-pressure `LOW/` A-factor is one order higher in concentration
  (k0 for `H+O2(+M)` is `cm6_mol2_s`) — the builder infers this from the
  falloff context, not the reaction's own molecularity.

**Chebyshev / PLOG pressures** → bar (`kinetics_plog.pressure_bar`,
`kinetics_chebyshev.pmin/pmax_bar`); CHEMKIN pressures are atm → ×1.01325.

## 8. Provenance, trust, and idempotency

- **Provenance:** CHEMKIN has none at the calculation level. Attach the
  mechanism's `literature` citation to every `thermo`/`kinetics`/`transport`
  record. Record the source tool as a `workflow_tool_release` (e.g. the
  mechanism name/version) so the whole import is attributable and queryable.
- **`scientific_origin`:** default `experimental` or `estimated` per the
  mechanism's own labeling; never `computed` (there is no computation behind
  a bare CHEMKIN number). Make it a per-import flag with a sane default.
- **Trust:** imported records enter `under_review` like any upload; the
  read-time trust fragment will rank them below fully-provenanced computed
  values (they have no source calculations) — which is *correct*.
- **Idempotency:** derive an idempotency key per logical record from
  `(mechanism_id, record_kind, reaction/species canonical key)`. Re-importing
  the same mechanism must not duplicate. Species/reaction identity dedup is
  automatic (content-only resolution); the guard is against duplicate
  *result* rows on re-run.

## 9. Volume & delivery

Mechanisms are large (GRI-Mech 3.0 ≈ 53 species / 325 reactions; detailed
mechanisms reach 10³–10⁴ species and 10⁴–10⁵ reactions). Therefore:
- Use the **async job / bundle** path, not one synchronous POST per reaction.
- Batch species (thermo+transport) first, then reactions (so reaction
  uploads find their species already resolved).
- Stream and checkpoint: the importer should be resumable and emit a
  per-record success/skip/error report (never abort the whole mechanism on
  one bad reaction after identity has passed — collect and report).

## 10. Error handling

- **Identity errors** (unmapped species, formula mismatch): abort **before**
  any upload; print the full list. Identity must be all-or-nothing.
- **Per-record parse/convert errors** (a malformed Troe line, an
  unsupported form): skip that record, record it in the report with the
  source line number, continue. Log what was dropped (no silent truncation).
- Emit a machine-readable summary: counts of species/thermo/transport/
  reactions imported, skipped, and errored, with reasons.

## 11. Testing strategy

Fixtures (small, checked-in, license-clean):
- **GRI-Mech 3.0** subset (with its species dictionary or a hand map): the
  canonical end-to-end fixture — Arrhenius, third-body, and Troe reactions,
  full thermo + transport.
- A **PLOG** reaction fixture and a **Chebyshev** reaction fixture (exercise
  DR-0032C tables).
- A **falloff/Troe** fixture with a collider-efficiency list (DR-0032B).
- A **units** fixture (KCAL/MOLE + MOLECULES) to test conversion.
- A **negative** fixture: unmapped species → hard error; formula mismatch →
  hard error; unsupported `LT` form → skipped-with-report.

Tests assert: parsed AST correctness (stage 1–2, no DB), payload correctness
(stage 4, no DB), and an end-to-end import into a test DB that verifies the
resulting `kinetics.model_kind`, falloff/PLOG rows, NASA coefficients,
transport values, and third-body efficiencies. A **round-trip test** pairs
with the exporter (§ bulk_export): import GRI-Mech, export it, and diff the
re-exported mechanism against the original within numerical tolerance.

## 12. Deliverables & milestones

1. **M1 — parser + normalizer** (stages 1–2, pure): CHEMKIN AST + unit
   normalization, tested against fixtures. No TCKDB coupling.
2. **M2 — identity resolver** (stage 3): structure-map ingestion (RMG dict +
   CSV), formula cross-check, fail-loud behavior.
3. **M3 — payload builder** (stage 4): AST → thermo/transport/kinetics
   payloads, covering every form in §6, tested without a live API.
4. **M4 — uploader + idempotency** (stage 5): async bundle upload, report,
   resumability; end-to-end GRI-Mech import into a test DB.
5. **M5 — round-trip** with the exporter; publish an importer CLI
   (`tckdb-chemkin-import mech.inp --thermo therm.dat --transport tran.dat
   --species-dict species_dictionary.txt --citation DOI`).

Package placement: a dedicated adapter package (e.g. `clients/python/
adapters/chemkin/`) that depends on the generic `tckdb-client` for transport
and on RDKit only in the identity-resolver stage (keep RDKit out of the
parser so the parser is dependency-light and reusable).
