# Chemistry / Quantum-Chemistry Rationale for the Schema Changes

A plain-language record of *why*, from a chemistry and quantum-chemistry
standpoint, each schema change in this remediation effort was made — the
companion to the engineering-focused `plan.md`. Written so a
computational chemist (not a database engineer) can follow the reasoning,
and reusable as raw material for the paper's §3 (scientific data model)
and §8 (limitations).

Each entry: **what the database could not do → why that matters to a
chemist → what we changed → what it now enables.**

---

## Phase 2 — Storing the Hessian (Cartesian second-derivative matrix)

**What was missing.** The database stored harmonic frequencies and
zero-point energy, but *not* the Hessian itself — the matrix of second
derivatives of the energy with respect to nuclear displacements
(∂²E/∂xᵢ∂xⱼ). Frequencies were kept; the object they are computed *from*
was not. The raw ESS Hessian files (Gaussian `.fchk` "Cartesian Force
Constants", ORCA `.hess`) could not even be attached as artifacts.

**Why it matters to a chemist.** The Hessian is the primitive of the
harmonic problem. From it (plus the geometry and atomic masses) you can
regenerate frequencies and normal modes, but the reverse is lossy —
stored frequencies alone cannot be re-projected onto a different set of
internal coordinates, cannot be recomputed for a different isotopologue
(different masses → different mass-weighted Hessian → different
frequencies), and cannot seed an anharmonic (VPT2) restart. For a
database whose purpose is *reproducibility*, discarding the Hessian means
statmech results are reproducible only "up to the harmonic numbers the
producer happened to print."

**What we changed (DR-0030).** Added a one-row-per-calculation
`calc_hessian` side table storing the packed lower triangle (with
diagonal) of the symmetric 3N×3N matrix in fixed units of hartree/bohr².
Crucially, the row carries a **mandatory foreign key to the exact
geometry** it was computed at — a Hessian is meaningless without its
atomic configuration, ordering, and orientation, so binding it to a
specific stored geometry is a scientific requirement, not a convenience.
We also allowlisted `.hess`/`.fchk` as artifact types so the byte-exact
sidecar can ride along as an audit trail.

**What it now enables.** Frequency/normal-mode regeneration; hindered-
rotor re-projection; isotopologue re-analysis with different masses;
VPT2/anharmonic restarts; and machine-readable force-constant data for
ML potentials. The parsed matrix is queryable; the raw file is the
verifiable source.

---

## Phase 3 — Species identity that respects spin state and tautomers

**What was wrong.** Species were deduplicated on the standard InChIKey
alone, and the upload path *rejected* any spin multiplicity that
disagreed with the radical count implied by the SMILES.

**Why it matters to a chemist.** Two failures, both routine in combustion
and radical chemistry:
- **Spin states.** Standard InChI does not encode electronic spin. So the
  singlet and triplet states of methylene (CH₂) — chemically distinct
  species with different geometries, energies, and reactivity, and both
  present in essentially every combustion mechanism — collapsed to one
  row, and the singlet was actually *rejected* because the SMILES
  `[CH2]` implies a triplet. The same problem blocks singlet vs triplet
  O₂ and open-shell singlet biradicals.
- **Tautomers.** The standard InChIKey's mobile-hydrogen layer merges
  certain tautomer pairs (the classic case is 2-pyridone /
  2-hydroxypyridine). These are different molecules with different
  connectivity and very different thermochemistry, yet they shared one
  identity row — and since keto/enol interconversion is itself a reaction
  of interest, this is a correctness bug, not a corner case.

**What we changed (DR-0031).** Identity is now **(canonical SMILES,
charge, multiplicity)**. Canonical SMILES preserves the hydrogen
placement / bond pattern that distinguishes tautomers (which the InChIKey
erases); multiplicity, carried explicitly, distinguishes spin states
(which SMILES cannot encode). The multiplicity implied by the SMILES is
now a default, not a hard constraint, so a producer can legitimately
declare singlet CH₂. The InChIKey is retained as a non-unique search
index for cross-notation lookup and interoperability with external
databases.

**What it now enables.** Correct, distinct storage of spin variants and
tautomers; a chemically honest identity that a combustion or radical
chemist can trust on day one. (Charge is still validated against the
SMILES, since charge *is* explicit in SMILES notation.)

---

## Phase 4A — Kinetics: tunneling model and pressure context

**What was wrong.** The tunneling correction was a free-text column, and
there was no way to state what a rate coefficient means with respect to
pressure.

**Why it matters to a chemist.**
- **Tunneling** (Wigner, Eckart, small-curvature, …) can change a
  computed rate by an order of magnitude at low temperature; recording it
  as free text ("Eckart", "eckart", "ECKART", typos) makes it unqueryable
  and inconsistent — you cannot reliably ask "give me all rates computed
  with an Eckart correction."
- **Pressure context.** A rate coefficient can be the high-pressure limit
  (k∞), an apparent rate at a specific pressure, or one point of a
  pressure-dependent surface. Mixing these silently is a classic,
  serious mechanism-assembly error — using an apparent 1-atm rate where a
  k∞ was needed (or vice versa) corrupts a model.

**What we changed (DR-0032, Part A).** `tunneling_model` is now an enum
(`none`/`wigner`/`eckart`/`sct`/`other`) with a tolerant, case-
insensitive normalizer that folds real producer strings (ARC emits
"Eckart") to the canonical token and folds anything unrecognized to
`other` rather than rejecting it. Added a `pressure_context` enum
(`high_p_limit` / `apparent_at_pressure` / `pressure_dependent`) and a
`pressure_bar` field, with a constraint that an apparent-at-pressure rate
must state its pressure.

**What it now enables.** Queryable, consistent tunneling provenance; an
explicit, machine-checkable k∞-vs-apparent distinction that removes a
common source of silent error in mechanism construction.

**Design note for the paper.** The tunneling normalizer is a small but
instructive example of a community-database principle: enums must be
*strict in what they store but lenient in what they accept*, because real
producer data uses varied conventions. Rejecting "Eckart" would have made
the database brittle against the very ARC output it is meant to ingest.

---

## Phase 4B — Pressure-dependent kinetics: falloff (Troe/Lindemann) and third bodies

**What was missing.** No representation of falloff behaviour or
collider-specific third-body efficiencies. The reaction-level kinetics
table held a single Arrhenius form only.

**Why it matters to a chemist.** Unimolecular decompositions and radical
recombinations are pressure-dependent: their rate transitions from a
low-pressure limit k₀ (where every activated molecule is stabilized by a
collision, effectively third-order) to a high-pressure limit k∞ (where
stabilization is fast, second-order). The Troe form parametrizes the
smooth transition through a broadening factor Fcent (α, T\*\*\*, T\*,
optional T\*\*); Lindemann is the no-broadening special case; SRI is an
alternative. Third-body efficiencies capture that different bath-gas
molecules stabilize collisions with different effectiveness (H₂O ≈ 6,
CO₂ ≈ 2, Ar ≈ 0.7 relative to a reference). **These Troe/falloff +
third-body forms are how the overwhelming majority of pressure-dependent
reactions are written in published CHEMKIN/Cantera/RMG mechanisms** — so
without them, a mechanism developer simply cannot deposit or retrieve
most literature pressure-dependent kinetics. This was ranked the #1
kinetics adoption blocker in the assessment.

**What we changed (DR-0032 Part B).** Extended `KineticsModelKind` with
`lindemann`/`troe`/`sri` (and `plog`/`chebyshev` for Part C). Added a
`kinetics_falloff` side table holding the **low-pressure** Arrhenius (k₀)
plus Troe and SRI broadening coefficients; the parent kinetics row's
Arrhenius is the k∞ form, and `model_kind` selects which broadening
columns are meaningful. Added a `kinetics_third_body_efficiency` table
keyed to a **collider species** (resolved from the uploaded collider
SMILES via the shared species-resolution seam) with a per-collider
efficiency factor (≥ 0, unique per collider). Wired the standalone
kinetics upload path (payload → workflow) to persist both.

**What it now enables.** A curator can deposit a literature Troe fit with
its low-pressure limit and collider table, or an ARC/RMG-produced falloff
reaction, and retrieve it in a form a mechanism generator can consume.
The k∞ vs k₀ split is explicit and tied to the same reaction identity.

**Scope note.** The standalone kinetics-upload path carries falloff now;
routing falloff through the ARC computed-reaction *bundle* payload is a
tracked follow-on (ARC bundles are TST-based; falloff typically arrives
via the dedicated kinetics upload or from literature).

## Phase 4C — Standalone PLOG / Chebyshev fits

**What was missing.** Pressure-dependent k(T,P) in PLOG or Chebyshev form
could only be stored bound to a full master-equation network + solve.

**Why it matters to a chemist.** A published mechanism very often reports
a PLOG table (Arrhenius parameters at several pressures, log-P
interpolated between them) or a Chebyshev k(T,P) surface as the *fitted
result*, without the master-equation inputs (wells, barriers, energy
transfer) that produced it. Previously TCKDB could only hold such a fit by
fabricating a skeleton network and a provenance-empty solve — dishonest
data. A curator digitizing a literature rate needs to store exactly what
the paper reports.

**What we changed (DR-0032 Part C).** Added reaction-level `kinetics_plog`
(per-pressure modified-Arrhenius entries) and `kinetics_chebyshev`
(n_T × n_P coefficient matrix + T/P validity domain) tables, mirroring the
network-level ones. A PLOG/Chebyshev rate can now be attached directly to
a reaction's `kinetics` record (model_kind `plog`/`chebyshev`) with no
network. The computed, master-equation-derived path stays where it was
(network-level) — the two homes are documented and distinct.

**What it now enables.** Literature pressure-dependent fits get an honest,
lossless home; the provenance stays truthful (a fit deposited as a fit,
not disguised as a computed network result).

## Phase 7 — Query accessibility: vocabulary discovery endpoints

**What was missing.** The scientific search endpoints filter by exact
strings (method, basis, reaction family, software), but a client had no
way to discover which values actually exist — you had to already know the
exact stored string to filter on it.

**Why it matters to a chemist (and modeler).** A kineticist assembling a
mechanism wants to ask "what levels of theory / reaction families /
software are in here?" before filtering. Exact-string filters are unusable
blind: is it "CCSD(T)" or "ccsd(t)"? "H_Abstraction" or "H-abstraction"?
Without discovery, the filter surface is effectively hidden.

**What we changed (Phase 7).** Added `GET /scientific/meta/{methods,
basis-sets, software, reaction-families}` returning the distinct stored
values with usage counts (reaction families list the seeded canonical
vocabulary with how many reactions use each). Read-only, no migration.

**What it now enables.** A client can enumerate the actual filter
vocabulary and usage before querying — the cheapest, highest-leverage
step toward the database being usable by the modeling community.

**Deferred follow-on.** Bulk export (curator-gated NDJSON streaming above
the row cap, then Chemkin/Cantera/RMG-importable formats) — the largest
single modeler-facing gap — is designed but not in this pass; it is a
substantial feature deserving its own effort.

## Phase 5 — Statmech completeness: optical isomers and electronic energy levels

**What was missing.** The statmech record captured symmetry number, point
group, rotor treatment, and frequency scaling — but not the optical-isomer
count nor the electronic energy levels.

**Why it matters to a chemist.** Two omissions that corrupt derived
thermochemistry:
- **Optical isomers.** A pair of enantiomers doubles the number of
  distinguishable configurations, adding R·ln(n) to the entropy — for a
  single chiral center (n=2) that is R·ln 2 ≈ 5.76 J/mol·K. That is
  *larger* than the S°298 uncertainties the database records, so if one
  contributor includes the term and another doesn't, their entropies are
  silently, significantly inconsistent. Every Arkane/RMG species
  declaration carries `opticalIsomers`; without a home for it, the number
  is lost and cannot round-trip.
- **Electronic energy levels.** The electronic partition function is
  q_elec = Σᵢ gᵢ·exp(−εᵢ/kT). For most closed-shell molecules the ground
  state dominates and q_elec ≈ g₀, but for open-shell atoms and radicals
  with low-lying electronic states it does not: the hydroxyl radical OH
  has a ²Π ground state spin-orbit-split by ~139 cm⁻¹ (both components
  doubly degenerate); atomic oxygen O(³P) has levels near 0, 158, and
  227 cm⁻¹; halogen atoms and NO are similar. At combustion temperatures
  these excited levels are thermally populated and materially change the
  partition function — and hence the thermochemistry. A `term_symbol`
  label alone cannot reconstruct them.

**What we changed (DR-0033).** Added `statmech.optical_isomers` (nullable
small integer ≥ 1; NULL = unspecified, 1 = achiral) and a
`statmech_electronic_level` child table of ordered (energy_cm1,
degeneracy) pairs relative to the ground state, with a uniqueness
constraint on the level ordering. Wired both through the standalone
statmech upload path (payload → workflow → resolution).

**What it now enables.** Entropies that are reproducible and comparable
across contributors (the optical-isomer term is explicit), and a
computable electronic partition function for exactly the open-shell
species where it matters — so OH, O(³P), and similar species round-trip
correctly instead of being silently mis-treated as single-level.

## Phase 6 — Level-of-theory identity: spin treatment (R / U / RO)

**What was missing.** The level of theory recorded method, basis,
dispersion, solvation, and auxiliary settings, but not the spin
treatment — restricted, unrestricted, or restricted-open-shell.

**Why it matters to a chemist.** For open-shell species the spin
treatment is not a formatting detail, it is a different calculation.
UCCSD(T) and ROCCSD(T) on the same geometry and basis give different
energies — the difference is driven by spin contamination in the
unrestricted reference and routinely reaches the kJ/mol scale for
radicals, which is exactly the accuracy regime combustion and atmospheric
thermochemistry cares about. Likewise UB3LYP vs ROB3LYP. If the spin
treatment is not part of the level-of-theory identity, two genuinely
different levels of theory collapse into one database row, and a value
computed with one silently stands in for the other.

**What we changed (DR-0034).** Added `level_of_theory.spin_treatment` as
an enum (restricted / unrestricted / restricted_open / unknown) and folded
it into the identity hash (`lot_hash`), with a missing value folding to
"unknown" so omission and explicit-unknown agree. The migration re-hashes
existing rows under the new formula so deduplication keeps working — a
later upload of an existing level of theory still matches rather than
duplicating.

**What it now enables.** Restricted / unrestricted / restricted-open
variants are distinct, queryable levels of theory. The dedup identity
became stricter (never wrongly merges on spin), never looser.

**Deliberately deferred (recorded here for the paper's limitations).**
The related gap — *string* normalization of the level of theory (so
"B3LYP" and "b3lyp", or "def2-TZVP" and "def2tzvp", resolve to one row) —
was NOT done in this pass. The reason is a chemistry-safety one: aggressive
normalization (case-folding, basis-set alias tables, splitting a
dispersion tag like "-D3(BJ)" out of the method string) risks the
*opposite* and worse error of merging two levels of theory that are
actually different. That belongs in a curated, reviewed follow-on with an
explicit alias table, not a quick automated pass. Spin treatment is a
clean categorical axis with no such ambiguity, so it was safe to do now.

## Phase 7 — (planned) Query accessibility for the modeling community

*To be filled in.* Chemistry motivation: a kineticist assembling a
mechanism needs bulk export and vocabulary discovery (which reaction
families / methods / basis sets exist), not entity-by-entity ID walking.
