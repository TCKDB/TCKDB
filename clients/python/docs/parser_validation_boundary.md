# Parser & validation boundary

Status: **design / spec only.** No parser implementation in this
document. Audience: `tckdb-client` maintainers, future ingestion
authors, and workflow-tool adapter authors (ARC, RMG, KinBot,
hand-rolled pipelines).

Companion to [`builder_api_mvp.md`](builder_api_mvp.md),
[`builder_api_stability.md`](builder_api_stability.md),
[`source_calculation_ergonomics.md`](source_calculation_ergonomics.md),
and [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md).
Those documents pin the *shape* and the *semantics* of upload
payloads. This document pins the **layering** — where parsing,
validation, and policy each live, and what is deliberately *not*
the builder's job.

---

## 1. Purpose

`tckdb-client` is growing in capability: structured builders, role
helpers, artifact planning. A natural next request is "should the
client parse my Gaussian / ORCA / Arkane / RMG files and populate
fields like `charge`, `multiplicity`, `final_energy_hartree`,
`software_release`, or `xyz` for me?"

The honest answer is *not in the base client*. This boundary exists
to:

- **Keep `tckdb-client` lightweight.** A producer running the client
  on an HPC submit host or inside a CI runner should not need
  Gaussian or ORCA installed, and should not need RDKit pulled in
  by transitive imports.
- **Avoid ESS-specific shapes in the base package.** Once the
  client knows how to read a Gaussian log, the temptation to make
  Gaussian *the* default — and to mirror Gaussian's data model in
  the builders — becomes structural. It is much harder to keep ORCA,
  Q-Chem, Molpro, Psi4, NWChem, Turbomole, and the rest first-class
  if one of them lives inside the client and the others don't.
- **Avoid Gaussian/ORCA/ARC-flavoured assumptions.** Parsers ship
  defaults: "imaginary mode threshold is −10 cm⁻¹", "treat any
  `Normal termination` as success", "trust the `# wb97xd/def2tzvp`
  route card". Workflow tools encode policy too: ARC's selected
  conformer, RMG's "best of N", Arkane's species-by-species blocks.
  These are real choices and they belong **above** the client, not
  inside it.
- **Preserve server-side authority.** The backend is the only place
  with deduplication, identity, permissions, and curation context.
  A client-side parser cannot replace it, and a client-side parser
  that *silently* corrects user-supplied values invites confusion
  when the server sees a different shape than the user typed.

The layering this document recommends:

```text
+---------------------------------------------------+
| Workflow-tool adapters                            |   workflow knowledge,
|   (ARC TCKDBAdapter, RMG, KinBot, …)              |   "which conformer", roles,
|                                                   |   artifact attachment policy
+-----------------------------+---------------------+
                              |
                              v
+----------------------------------+ +--------------+
| Parser / ingestion helpers       | | Hand-shaped  |
|   (optional; future package)     | | producer code|
|   parse_gaussian_job(...)        | | direct       |
|   parse_orca_job(...)            | | construction |
|   parse_arkane_yaml(...)         | | from values  |
+-----------------+----------------+ +------+-------+
                  |                         |
                  v                         v
+---------------------------------------------------+
| tckdb-client builders                              |  structured types,
|   Species / Calculation / Thermo / Statmech / …    |  local validation,
|   ComputedSpeciesUpload / ComputedReactionUpload   |  payload assembly,
|   SourceCalculations / Artifact planning           |  diagnostics
+-------------------------+-------------------------+
                          |
                          v
+---------------------------------------------------+
| TCKDB backend                                     |  authoritative validation,
|   schema, identity, dedupe, permissions, audit    |  persistence, hashing
+---------------------------------------------------+
```

The four layers compose top-to-bottom. Each layer is allowed to use
layers below it; nothing should reach *upward*. The base
`tckdb-client` install ships only the bottom two layers (builders +
the thin HTTP client). Parsers and adapters are explicitly above the
line.

---

## 2. What builders should do

The current builder layer's contract is *transform structured Python
values into a valid TCKDB upload payload, fail loudly on local
inconsistency, and stay out of scientific judgement*.

**Builders may**:

- Validate obvious local type / shape errors (wrong type, missing
  required field, empty string where one is required).
- Validate the role vocabularies they own
  (`ThermoCalculationRole`, `StatmechCalculationRole`,
  `KineticsCalculationRole`, `TransportCalculationRole`).
- Validate **local reference closure** — every `Calculation`
  referenced by `source_calculations`, `depends_on`,
  `primary_calculation`, or a per-species mapping must also appear
  in the upload's bucket of calcs.
- Generate deterministic local keys for inter-object references
  (`KeyMinter`).
- Assemble the payload dict that the thin HTTP client posts.
- Plan artifact uploads — produce a `list[PlannedArtifactUpload]`
  once the server has returned calculation IDs.
- Surface emission diagnostics for data the builder accepted
  locally but the wire schema cannot carry today (stable diagnostic
  codes per [`builder_api_stability.md`](builder_api_stability.md)).

**Builders must not**:

- Parse Gaussian, ORCA, Arkane, Molpro, Q-Chem, Psi4, NWChem,
  Turbomole, or any other ESS input / output file.
- Infer `charge` or `multiplicity` from a log file's parsed contents.
- Infer SMILES from a geometry. The producer either provides a
  SMILES (the canonical identity input) or accepts a geometry-only
  record.
- Canonicalize chemical identity — no RDKit normalisation, no
  InChI re-derivation, no SMILES rewriting. Identity normalisation
  is a backend responsibility (and a curation responsibility above
  that).
- Run RDKit isomorphism, fingerprint, or similarity checks.
- Decide whether an ESS calculation is scientifically acceptable.
  ("Final energy looks too positive", "ZPE is suspiciously small",
  "this opt didn't actually converge" — none of these belong in the
  client.)
- Silently override or *patch* user-provided fields based on file
  contents. If a builder accepts a value, it ships that value. If
  it disagrees with reality, the *parser* layer should raise (§6) —
  not the builder.

The single rule that keeps the boundary clean: **builders see only
structured values, never bytes**. The closest builders come to
files is `Calculation.add_artifact(path, kind=…)`, which records
the path and kind on the builder but never opens the file. The
two-phase upload reads bytes only at phase-2 POST time, inside the
HTTP client — not inside the builder.

---

## 3. What a future parser / ingestion layer should do

A parser layer is **optional** future work. When it exists, it
transforms ESS files into the structured values the builder layer
already accepts:

```python
job = parse_gaussian_job("ethanol_opt.log")
# job is a structured intermediate that knows charge, mult, method,
# basis, final energy, frequencies, ZPE, software version, sha256,
# the parsed XYZ block, normal-termination status, …
species = job.to_species(smiles="CCO")
calc    = job.to_calculation(label="ethanol opt")
```

The parser package's surface is small and per-tool. Suggested
modules:

- `parse_gaussian_job(path)` — Gaussian `.log` / `.out`.
- `parse_orca_job(path)` — ORCA output.
- `parse_arkane_yaml(path)` — Arkane species `.py` / `.yml`.
- `parse_rmg_species(path)` — RMG species-dictionary records.

A parser **may**:

- Read Gaussian / ORCA / Arkane / RMG input and output files.
- Extract software identity (program, version, revision).
- Extract `charge`, `multiplicity`, `method`, `basis`,
  `final_energy`, frequencies, ZPE, imaginary modes, normal /
  abnormal termination, the converged XYZ, scan coordinates,
  optimisation history.
- Compute artifact `sha256` digests on demand (so producers can
  pre-compute hashes if they want; the HTTP client already does
  this at phase-2 upload time, so this is for *audit* not transport).
- Emit one of two output shapes:
  1. **Builder objects directly** — `to_species()`, `to_geometry()`,
     `to_calculation()` returning ready-to-pass builder instances
     for the most common case.
  2. **A structured intermediate record** — a typed dataclass with
     all extracted fields, for adapters that want to inspect or
     re-shape before passing to builders.

A parser **must not**:

- Modify the builder objects after construction.
- Reach into the HTTP client.
- Pull in RDKit, OpenBabel, or other heavy chemistry libraries on
  base parse. (If a parser-specific helper genuinely needs RDKit —
  e.g. "give me the SMILES of the geometry your log file converged
  on" — that helper lives behind its own optional extra, and is
  *not* implicit in the basic parse.)
- Decide policy questions (which conformer is best, which artifact
  is authoritative). Those belong to the adapter (§4).

### Naming

Three candidates, evaluated:

| Name                     | Verdict                                                                                                                                |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `tckdb_ingest`           | **Recommended.** Top-level package signals "this is not the client". Producers `pip install tckdb-ingest` only when they need it. Easy to version independently. |
| `tckdb_parsers`          | Acceptable. Slightly narrower scope hint — implies parsing without claiming responsibility for adapter-shaped concerns.                |
| `tckdb_client.parsers`   | **Avoid.** Living inside the `tckdb_client` import path encourages base-install dependencies and blurs the layering this document defines. |

Recommendation: ship parsers as a **separate distribution**
(`tckdb-ingest`), reusing the same repo if helpful but installed
on its own. Producers depending on the base client never pay the
parser cost; producers who want parsing add one line to
`requirements.txt`.

---

## 4. What workflow-tool adapters should do

A workflow-tool adapter is the layer that owns *workflow-specific*
knowledge. ARC's `TCKDBAdapter` is the canonical example; RMG and
KinBot will grow analogous adapters when those integrations land.

Adapters may:

- Know **where files live** for a given workflow run — directory
  layout, run identifiers, the convention by which species and
  reactions are named.
- Know **which job is opt / freq / sp** in the workflow's idiom,
  and how to combine them into one `Calculation`-per-role mapping.
- Know **which output is authoritative** when a workflow runs the
  same step multiple times (e.g. ARC's lowest-energy conformer
  after RDKit pruning).
- Know **which conformer / geometry the workflow stands behind**.
  Per [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  the producer ships the one structure they stand behind; the
  adapter is where "which one" is decided.
- Know **how workflow labels map to TCKDB role tokens** — ARC's
  `reactant_sp` ↔ `reactant_energy`, etc.
- Decide **which artifacts to attach** — the input deck, the
  output log, the optimised geometry, the scan results.

Adapters are allowed to use parsers (§3) and builders (§2). They
must not force workflow-specific assumptions into the generic
builder layer. Specifically:

- No adapter ships a `RMGDefaults` / `ARCDefaults` / `ArkaneDefaults`
  preset *that lives inside `tckdb-client`*. The
  [§10 of `source_calculation_ergonomics.md`](source_calculation_ergonomics.md)
  rule is the precedent: workflow-tool habits stay in adapters.
- No adapter mutates builder objects via private attributes.
  Builders are the public contract; if an adapter needs to express
  something the builder can't, that is a builder-layer feature
  request, not a private-attribute workaround.
- No adapter rewrites the wire payload after the builder produces it.
  If the adapter wants a different wire shape, it should change the
  values it passes to the builder, not the dict the builder returned.

In short: adapters can be opinionated. The base client cannot.

---

## 5. Backend authority

The TCKDB backend remains the only authoritative validator for:

- **Schema validation.** Pydantic models on the server are the
  authority; the builder enforces a *subset* for ergonomics but
  the server's 422 is the source of truth.
- **Permissions and authentication.** API keys, project scoping,
  upload-quota policy.
- **Deduplication.** Species, geometries, calculations,
  literature, software releases — the backend's dedup pipeline
  decides what is "the same" record. Clients never assume.
- **Moderation and curation.** Approved / pending / rejected
  status, curator review, conflict resolution.
- **Geometry validation.** If the server grows symmetry / bonding /
  consistency checks, those run server-side.
- **Chemical identity checks.** SMILES ↔ InChI ↔ geometry
  consistency, charge / multiplicity sanity against the SMILES.
- **Artifact storage and hash verification.** The server is the
  one that hashes the bytes it received, and the only consumer of
  that hash that matters.
- **Database IDs.** Every primary key is server-minted.

Client-side validation is **convenience only**. Its purpose is to
catch the obvious mistakes early, so the producer sees a clear
`TCKDBBuilderValidationError` before they pay the network round-trip
— not to replace the backend's authority. Two parallel rules apply:

1. **No client check is load-bearing.** If the backend doesn't
   enforce it, removing the client check is a UX regression at
   worst — never a data-integrity regression.
2. **No client check ever* relaxes* the server.** A client that
   accepts something the server rejects is harmless; a client that
   *rejects* something the server would accept is a bug.

---

## 6. Cross-checks and warnings

When a parser layer exists, mismatch between parsed file content
and builder-supplied values is *inevitable* — that's literally what
the parser is for.

Example:

```text
parse_gaussian_input("ethanol.gjf") sees `0 1` (charge 0, mult 1).
The producer constructs Species(smiles="CCO", charge=0, multiplicity=2).
```

The parser layer must:

- **Report a structured warning or error** through a dedicated
  exception / diagnostic type (e.g.
  `tckdb_ingest.ParserMismatchError`), with the offending field,
  the parsed value, the builder value, and the file the parser
  read.
- **Never silently modify** the builder's values. The builder is
  the producer's stated intent; the parser is reality on disk.
  Silent reconciliation hides the disagreement.
- **Let the caller decide** whether the mismatch is fatal.
  Producers running curated submissions may want
  `strict=True`-style hard fail. CI sanity-check pipelines may
  want `strict=False` and a structured report.

A reasonable default surface (illustrative, not prescriptive):

```python
report = parse_gaussian_job("ethanol.log").cross_check(species, calc)
if report.has_errors:
    raise report.first_error()
for warning in report.warnings:
    log.warning("%s", warning)
```

Mismatches that are particularly worth flagging:

| Field                     | Builder source              | Parser source                                | Action on mismatch        |
|---------------------------|-----------------------------|----------------------------------------------|---------------------------|
| `charge`                  | `Species.charge`            | Route card / `%chk` header / input deck      | Hard error by default     |
| `multiplicity`            | `Species.multiplicity`      | Route card / input deck                      | Hard error by default     |
| `method` / `basis`        | `LevelOfTheory`             | Route card / ORCA input                      | Warning; some workflows deliberately use different LoT for different stages |
| `software` / `version`    | `SoftwareRelease`           | Banner of the output file                    | Hard error by default     |
| `final_energy_hartree`    | `Calculation.opt(...)`      | Parsed SCF / Total energy                    | Warning with tolerance    |
| `n_imag`                  | `Calculation.freq(...)`     | Frequency block                              | Hard error by default     |
| Converged geometry XYZ    | `Geometry` value            | Last standard-orientation block              | Warning with tolerance    |

The parser layer chooses its own defaults; this table is a
suggestion for what counts as load-bearing.

---

## 7. Artifacts and auditability

Even with no parser shipped, **artifacts already give TCKDB a
durable audit trail**. The two-phase upload preserves the original
file bytes (with server-computed sha256) alongside the structured
record. This document does not change that contract — it
*depends* on it.

The artifact path matters here for two reasons:

- **Parsers can validate against artifacts.** Once an upload is
  durable, a future parser run can re-open the stored bytes and
  re-derive the structured values, cross-checking against what the
  producer originally typed. This is the same `cross_check` flow
  from §6, but run against curated data at audit time rather than
  against on-disk files at submission time.
- **Audit survives incomplete parser support.** A producer who
  uploads a Gaussian log today, before any parser exists, leaves
  a recoverable record. When a Gaussian parser ships in three
  months, that historical record is re-validatable. The shape of
  the boundary defended in this document is what makes this
  possible: the audit value of "the bytes on the server" is
  independent of whether the client ever learned to parse them.

This is the strongest argument against pulling parsers into the
client: **the audit story already works without them**.

---

## 8. Packaging recommendation

Three options:

### A. Keep parsers outside `tckdb-client` entirely

The base client stays parser-free; parser support ships as one or
more *separate distributions* (`tckdb-ingest`, `tckdb-ingest-gaussian`,
`tckdb-ingest-arc`, …). Producers `pip install tckdb-ingest` only
if they want it.

**Pros**:

- Cleanest boundary. Base install is small (`httpx` only).
- Parser releases are independent of client releases.
- No risk of an ESS dependency creeping into the base wheel.
- Mirrors the existing pattern: ARC has its own adapter; the
  client knows nothing about ARC.

**Cons**:

- Slightly higher install friction for producers who want both.
- Two packages to discover; needs README pointers.

### B. Optional extra `tckdb-client[parsers]`

Parsers live in the same repo and the same wheel as `tckdb-client`,
but only get imported / installed under an optional extra.

**Pros**:

- One package to remember.
- Producers opt in with `pip install tckdb-client[parsers]`.

**Cons**:

- Even with `extras_require`, the import surface lives inside
  `tckdb_client.parsers`, which encourages the wrong layering. The
  "is it OK to import this here?" question gets murky inside the
  same repo.
- Cross-release coupling. A bugfix to the Gaussian parser forces
  a `tckdb-client` release.
- Optional extras are easy to forget and easy to fail to install
  in CI.

### C. A separate `tckdb-ingest` package, repo-co-located

`tckdb-ingest` is its own distribution, shipped from the same repo
as `tckdb-client`. Both versioned and tested side-by-side, but the
wheels are independent.

**Pros**:

- Same boundary as (A), but easier to keep parser docs and tests
  next to the client they target.
- Single CI lane, shared fixtures.

**Cons**:

- Repository hygiene work: multi-package CI, multiple `pyproject.toml`,
  potential test-fixture cross-imports.

### Recommendation

**Keep the base `tckdb-client` parser-free** (option A or C). The
exact mechanism (separate repo vs co-located distributions) is a
later packaging decision; what matters for *this* document is the
**rule**:

- `pip install tckdb-client` must not pull in Gaussian / ORCA /
  Arkane / RDKit / OpenBabel.
- Parser support, when it exists, is a deliberate opt-in by the
  producer.
- The base client's import graph stays small enough that an HPC
  submit host or a CI runner can install it with no native
  dependencies beyond `httpx`.

Option B is **not** recommended; the in-tree optional extra
weakens the layering more than it helps.

---

## 9. Example future flow

Illustrative, not prescriptive. No code in this snippet exists today.

```python
from tckdb_client import TCKDBClient
from tckdb_client.builders import ComputedSpeciesUpload
from tckdb_ingest.gaussian import parse_gaussian_job  # hypothetical


# Parser-shaped layer — pure file reading; no network, no policy.
job = parse_gaussian_job("ethanol_opt.log")

# Builder layer — structured value-in, payload-out.
species = job.to_species(smiles="CCO")
calc    = job.to_calculation(label="ethanol opt")
calc.add_artifact("ethanol_opt.log", kind="output_log")

# Producer-controlled cross-check (§6). Hard-fail on charge or
# multiplicity mismatch; warn on energy / geometry drift.
report = job.cross_check(species, calc)
if report.has_errors:
    raise report.first_error()
for warning in report.warnings:
    print(f"[parser warning] {warning}")

upload = ComputedSpeciesUpload(
    species=species,
    calculations=[calc],
    primary_calculation=calc,
)

# Thin HTTP client; backend remains authoritative (§5).
with TCKDBClient(base_url, api_key=api_key) as client:
    result = client.upload(upload)
    client.upload_artifacts(upload.artifact_plan(result))
```

Notes on what this example deliberately does *not* show:

- No `parse_gaussian_job(...).upload(...)` shortcut. Uploading is
  the HTTP client's job; the parser does not own that path.
- No `from tckdb_client.parsers import …`. Parsers live in
  `tckdb_ingest`, *outside* the `tckdb_client` namespace.
- No automatic SMILES inference. `species = job.to_species(smiles="CCO")`
  requires the producer to supply the SMILES; the parser does not
  invent identity.
- No silent value substitution. If the producer types
  `multiplicity=2` and the log file says `multiplicity=1`, the
  cross-check raises — the builder does not get rewritten.

---

## 10. Non-goals

Reiterated for emphasis. None of the items below are deliverables
of this design, and several are explicitly forbidden by it.

- **No parser implementation in this task.** Design only.
- **No backend schema changes.** Layering is a client-side concern;
  the wire shape is unchanged.
- **No RDKit dependency in the base `tckdb-client` install.** Not
  now, not as a transitive import via `tckdb_client.parsers.*`.
- **No ESS-specific logic in core builders.** Gaussian, ORCA,
  Arkane, Molpro, Q-Chem, Psi4, NWChem, Turbomole — none of these
  live inside `tckdb_client.builders`.
- **No ARC-specific (or other workflow-tool-specific) defaults in
  the client.** ARC's `TCKDBAdapter` is the right home for ARC
  habits; same applies to every other workflow tool.
- **No automatic correction of user-provided builder fields by
  parsers.** Mismatch is a *report*, never a *patch*.
- **No "smart" inference paths.** No SMILES-from-geometry, no
  multiplicity-from-electron-count, no method-from-energy-range.
  The producer types what they mean; the client / parser layer
  flags disagreement.
- **No replacement of backend authority.** Client-side validation
  is convenience; the server is the source of truth.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the broader builder
  spec.
- [`builder_api_stability.md`](builder_api_stability.md) — the
  public-beta surface and deprecation policy; the
  diagnostic-code contract underpins how mismatches from §6 would
  be surfaced if they ever land in the builder layer.
- [`source_calculation_ergonomics.md`](source_calculation_ergonomics.md)
  — applies the same "no workflow-tool presets" rule to
  source-calculation roles; this document generalises the
  principle to parsers.
- [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  — applies the same "TCKDB is not a workflow scratchpad" rule to
  conformer search results.
- [`adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
  — the producer-facing quickstart that names this layering as one
  of the three boundary rules every adapter respects.
