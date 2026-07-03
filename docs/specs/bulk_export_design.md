# Bulk Export — Design Spec

**Status:** design, ready for implementation.
**Purpose:** let a modeler pull many records at once — the single biggest
consumer-side gap (the read API caps at 200 rows / offset ≤ 10k and tells
users to "contact a curator for bulk access"). Pairs with
`chemkin_importer_design.md`; together they make TCKDB *interoperable*, not
just *queryable*.

---

## 1. The two things "bulk export" must be

They are different features with different consumers; build both, but they
share the selection/closure core (§4).

1. **Native NDJSON export** — lossless TCKDB JSON, one record per line,
   streamed. For programmatic consumers, backups, and re-ingestion into
   another TCKDB deployment (round-trips through the content-only upload
   path). Preserves public refs and provenance.
2. **CHEMKIN mechanism export** — thermo `therm.dat` (NASA-7) + kinetics
   `chem.inp` REACTIONS + transport `tran.dat`, ready to drop into
   Cantera/CHEMKIN. This is the **killer feature**: import + export CHEMKIN
   means a modeler can round-trip a mechanism through TCKDB, and TCKDB
   becomes a curation/provenance layer over the tools they already use.
   (Cantera-YAML export is a cheap follow-on once the closure exists.)

## 2. Why this is not just "raise the row cap"

A usable mechanism is not an arbitrary page of rows — it must be
**internally consistent and singular**:
- **Closure:** every species referenced by an exported reaction must be
  present, each with thermo (and transport, for a runnable mechanism).
  Export therefore takes a *seed query* and computes the transitive closure
  of species needed.
- **Singularity:** CHEMKIN wants exactly one thermo per species and (usually)
  one kinetics expression per reaction — but TCKDB is append-only and holds
  *many* candidate values. Export must apply the **read-time selection
  policy** (the existing `collapse=first` sort: review status → evidence
  completeness → recency, etc.) to pick one, and record which policy it used.
- **Trust filtering:** default to approved/curated records
  (`min_review_status`), so an export is not silently contaminated by drafts.

These three are exactly what a naive "SELECT … LIMIT" cannot give, and why
export is its own endpoint rather than a bigger search page.

## 3. API surface

Two streaming, authenticated (curator-gated by default, per the existing
posture) endpoints under the scientific surface:

```
GET  /api/v1/scientific/export/ndjson      ?<selection query>   → application/x-ndjson (streamed)
POST /api/v1/scientific/export/chemkin     {selection + options} → multipart or a zip of
                                                                    {chem.inp, therm.dat, tran.dat}
```

Selection query (shared by both, §4). CHEMKIN export is POST because it
carries options (units, whether to include transport, naming policy).

Streaming is mandatory (mechanisms are 10³–10⁵ rows): use a server-side
generator / `StreamingResponse`, never materialize the whole mechanism in
memory. Above the normal read cap, gate on an authenticated role or an
explicit `export` scope so anonymous traffic can't trigger huge scans; the
query-timeout safety net still applies per-chunk.

## 4. Selection & closure (the shared core)

Input: a **seed selection** — one of:
- an explicit list of species refs / reaction refs,
- a filter (e.g. `reaction_family=H_Abstraction`, `method=CCSD(T)`,
  a SMILES/substructure set), reusing the existing search services, or
- "everything" (curator/admin only, hard-capped and logged).

Algorithm:
1. Resolve the seed to a set of **reaction entries** (and standalone species,
   for a species-only export).
2. **Closure:** collect every participant species entry of every selected
   reaction; add their thermo and transport.
3. **Select one value per record** via the read-time policy
   (`collapse=first`), or, with `?collapse=all`, emit all candidates in
   NDJSON (CHEMKIN cannot represent multiples, so CHEMKIN forces `first`).
4. **Trust filter:** drop records below `min_review_status` (default:
   approved-class); a record with no qualifying value is reported as a
   *gap*, not silently omitted, so the consumer knows the mechanism is
   incomplete.
5. Emit, with a manifest (§6).

Reuse, don't reinvent: the selection sorts and trust logic already live in
`app/services/scientific_read/`; export composes them plus the closure step.

## 5. CHEMKIN serialization rules (the exact inverse of the importer)

- **Species names:** TCKDB stores structures, CHEMKIN wants names. Naming
  policy options: (a) use the species `public_ref`-derived short name,
  (b) use a stored/first-seen mechanism name if present, (c) generate from
  formula with a disambiguating suffix. Emit a `!`-comment on each species
  with its SMILES + TCKDB public ref for round-trip traceability.
- **Thermo:** `thermo_nasa` (t_low/t_mid/t_high, a1–a7 high, b1–b7 low) →
  standard NASA-7 14-line-coefficient block, with the elemental composition
  from the resolved structure. Species with only tabulated (`thermo_point`)
  or scalar data and no NASA fit → reported as a gap (CHEMKIN needs NASA-7);
  optionally auto-fit NASA from points as a flagged follow-on.
- **Kinetics:** invert §6 of the importer — `modified_arrhenius`→ Arrhenius
  line; `troe`/`lindemann`/`sri`→ `(+M)` with `LOW`/`TROE`/`SRI`;
  third-body efficiencies→ efficiency list; `plog`→ `PLOG/` lines;
  `chebyshev`→ `CHEB`/`TCHEB`/`PCHEB`. Two kinetics on one reaction→ `DUP`.
- **Transport:** `transport` → `tran.dat` (name, geometry index, ε/kB, σ,
  dipole, polarizability, rot. relaxation). Geometry index (0/1/2) from the
  species' linearity/atomicity (statmech `is_linear` when available; else
  infer from the structure).
- **Units:** default emit `REACTIONS CAL/MOLE MOLES` (the CHEMKIN norm) and
  convert from the stored fixed units; make the header a POST option.

## 6. Manifest & provenance-preserving traceability

Every export carries a `manifest.json`:
- the seed selection, the selection policy used, `min_review_status`, the
  export timestamp and TCKDB schema version,
- per-record: the source public ref(s) and review status,
- the list of **gaps** (species/reactions dropped for lacking a qualifying
  value or a NASA fit).

This makes an exported mechanism auditable back to TCKDB — the same
"traceable value" principle as the rest of the database, carried into a
format that otherwise loses provenance.

## 7. Consistency, size, and safety

- **Determinism:** the same selection + policy + data snapshot must produce a
  byte-identical export (modulo timestamp) — required for the round-trip
  test and for reproducible mechanisms. Sort deterministically.
- **Size guards:** stream; cap total records for anonymous/non-curator
  callers; log every export (who, selection, row count) for the "no silent
  bulk scan" posture.
- **Snapshot semantics:** run the export in a single read transaction so a
  concurrent write can't produce an inconsistent mechanism.

## 8. Testing

- **Round-trip (the headline test):** import GRI-Mech 3.0 (importer spec),
  export CHEMKIN, and diff against the original — species set, NASA
  coefficients, Arrhenius/Troe/PLOG parameters, and transport within
  numerical tolerance. This single test validates both adapters and proves
  interoperability.
- Closure test: seed with one reaction, assert all participant species +
  their thermo appear.
- Selection test: two candidate thermos for a species, assert export picks
  the policy-preferred one and the manifest records the choice.
- Gap test: a species with points-only thermo → reported gap, not a broken
  CHEMKIN block.
- NDJSON re-ingest test: export NDJSON, feed it back through the upload path,
  assert idempotent (no duplicates).

## 9. Deliverables & milestones

1. **M1 — selection + closure core** (service): seed → reactions → species
   closure → policy selection → trust filter → in-memory record set, tested
   without HTTP.
2. **M2 — NDJSON streaming endpoint** + auth/gating + manifest.
3. **M3 — CHEMKIN serializer** (thermo + kinetics + transport), the exact
   inverse of the importer's builder.
4. **M4 — round-trip test** against GRI-Mech; publish an export CLI.
5. **M5 (follow-on)** — Cantera-YAML target (reuses the closure + selection);
   NASA-from-points auto-fit for species lacking a stored NASA block.

Package placement: the CHEMKIN serializer lives beside the importer
(`clients/python/adapters/chemkin/`, sharing the form-mapping tables so
import and export can't drift); the streaming endpoints + closure/selection
core live in the backend (`app/services/scientific_read/export.py` +
`app/api/routes/scientific/export.py`).
