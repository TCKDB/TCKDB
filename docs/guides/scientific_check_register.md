# The scientific check register

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py`.
Every `file:line` here is resolved from the live function object at generation
time, so it cannot go stale the way a hand-written table does.

## What this is

An enumeration of what TCKDB *guarantees about chemistry*, as opposed to what
it merely stores. TCKDB runs roughly 326 Pydantic validators and 225 database
check constraints; almost all of them enforce plumbing — string lengths, enum
membership, foreign-key existence, `multiplicity >= 1`. This register contains
only the checks that encode a **scientific decision a referee could disagree
with**.

The inclusion test is *could this check be wrong in an interesting way?*
Elemental balance is a position. `max_length=64` is not, and `multiplicity >= 1`
is arithmetic rather than chemistry. There is deliberately no "paper-worthy"
column: **membership in this register is the claim.** An entry that fails the
test dilutes every other entry.

## Relationship to `docs/reviews/validation_check_audit.md`

The two sit beside each other and answer different questions. That audit asks
*is each Pydantic validator in the right ADR 0008 tier* — it is a placement
review of one tier, pinned to a commit, and it is now stale by several codes.
This register asks *what does TCKDB guarantee about chemistry* — it spans the
service layer, the wire schemas and the database, it is generated rather than
transcribed, and it is guarded in CI. Neither supersedes the other. Where they
disagree about a line number, this one is right by construction.

## How to read a row

- **Asserts** — what the check claims, in one sentence a chemist would
  recognise. Not a description of the implementation.
- **Tier** — the ADR 0008 consequence, with the justification for *that* tier
  in the ADR's own terms.
- **Enforced at** — `file:line` for Python, or the constraint/trigger name for
  PostgreSQL. Database names are verified against live schema metadata by
  `backend/tests/db/test_scientific_check_register.py`, not trusted as strings.
- **Thresholds** — the numeric lines the check fires on, and *where each number
  comes from*. A constant is fixed in code and printed. A provenance-derived
  threshold is not a number at all: it is resolved per record from the
  execution provenance that record carries, so it is printed as the resolver,
  the parameter keys it reads, the per-protocol table, and what it falls back
  to when those keys are absent. ADR 0012's `tau` is the first of these, and
  the distinction is load-bearing — a register that printed it as "roughly
  50 cm-1" would be claiming the code holds a constant it does not have, and
  would hide the case a referee cares about most, which is the record whose
  protocol was never recorded.
- **Escape hatch** — how legitimate chemistry the check would otherwise refuse
  gets deposited instead. This column carries most of the weight. Charge
  conservation is only defensible as a blocking check *because* an electron can
  be declared as a participant; without that door the rule would be asserting
  "every participant was declared", which is an expectation about the depositor
  and ADR 0008 disqualifies an expectation from blocking.
- **Recorded divergence** — where a check's documentation and its behaviour
  disagree, or where a guarantee is narrower than its name suggests. Reported,
  never silently fixed.


## Tiers, and how many entries sit in each

| Tier | Entries | Meaning |
| --- | --- | --- |
| `block` | 14 | Refuses the payload. ADR 0008 permits this only for a definition or a contract — a record no correct calculation could produce. |
| `warn` | 8 | Accepts the payload and records a machine-readable warning. The tier for expectations (which could fire on a correct novel result) and for absences (an incomplete record is still a true one). |
| `review` | 0 | Referred to `machine_review` under a versioned rubric. ADR 0008 puts every cross-check against external reference data here. |
| `structural` | 5 | Not an ADR 0008 consequence tier. The position is enforced by the shape of the schema, so a record violating it cannot be represented. |
| **total** | **27** | |

## Recorded divergences

Where a check's documentation and its behaviour disagree, or where a guarantee is narrower than its name suggests. Every one of these is reported, never silently fixed — the register changes no check behaviour.

- **[3]** A saddle point is made of exactly the atoms of the reaction it is declared to sit in.
- **[4]** A saddle point carries the same total charge as the reactants it sits between.
- **[6]** The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.
- **[9]** An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.
- **[13]** A transition state's imaginary modes other than the reaction coordinate are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.
- **[25]** A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

## Entries

## Conservation across a reaction

### 1. The reactant and product sides of a reaction contain the same number of atoms of every element.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_mass_balance_failed` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. Mass balance is what makes a set of species a reaction rather than a list, so no correct calculation can produce an unbalanced one; the check cannot fire on a correct novel result.

**Enforced at.**

- `validate_reaction_elemental_balance` — `backend/app/services/reaction_resolution.py:131`
  *Called from `resolve_chem_reaction`, so it fires on every path that resolves a reaction, including the PDep bundle.*

**Escape hatch.** Declare a participant with `molecule_kind: pseudo`. A lumped or phenomenological construct has no atom-resolved composition, so one such participant suspends the law for the whole reaction. A declared electron does **not** exempt it — an electron contributes zero atoms and the reaction still has to balance.

### 2. The summed formal charge of a reaction's reactants equals that of its products.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_charge_not_conserved` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and only because the escape hatch exists. Electrons are neither created nor destroyed by rearranging bonds. Without a way to name a free electron the rule would in fact assert 'every participant was declared', which is an expectation about the depositor rather than a definition of a reaction, and ADR 0008 disqualifies an expectation from blocking.

**Enforced at.**

- `validate_reaction_charge_conservation` — `backend/app/services/reaction_resolution.py:219`
  *Sums `Species.charge`, which `canonical_species_identity` has already reconciled against the formal charge of each species' own SMILES.*

**Escape hatch.** Declare the free electron as a participant — `{"molecule_kind": "electron", "smiles": "[e-]", "charge": -1, "multiplicity": 2}` — which is how associative and dissociative attachment, photoionization and photodetachment are deposited. It contributes -1 to the side it sits on and zero atoms, so elemental balance still has to be satisfied separately. A `pseudo` participant suspends the law entirely, as it does for elemental balance. Conservation is not neutrality: any net charge is accepted as long as both sides carry the same one, so ion-molecule reactions are unaffected.

### 3. A saddle point is made of exactly the atoms of the reaction it is declared to sit in.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_composition_mismatch` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A transition state is a stationary point on the potential energy surface *of those atoms*, so a saddle point with a different molecular formula cannot be that reaction's saddle point, whatever else is true of it.

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py:361`
  *Composition is read from the saddle-point geometry when there is one and from `unmapped_smiles` otherwise. The PDep path passes no SMILES, so it compares geometry only.*

**Escape hatch.** Declare the extra species as participants of the reaction. A `pseudo` reactant exempts the reaction. Absence does not block: no geometry and no parseable SMILES means nothing is compared, and an unparseable transition-state SMILES is treated as silence rather than as a contradiction, because a TS SMILES is a lossy label for a structure that is by construction not a stable molecule.

**Recorded divergence.** The docstring says pseudo-species exemption 'matches `validate_reaction_elemental_balance`'. It does not, quite: this function queries only `ReactionRole.reactant` and exempts only on a *reactant-side* pseudo participant, while `_load_participant_species` exempts the two conservation checks on a pseudo participant on **either** side. A reaction whose only pseudo species is a product is therefore exempt from elemental balance but still held to transition-state composition, and it is compared against a reactant side that carries no balance guarantee. Reported, not changed — this register alters no check behaviour.

### 4. A saddle point carries the same total charge as the reactants it sits between.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_charge_mismatch` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. Charge is conserved along a reaction coordinate, so a saddle point at a different charge is on a different potential energy surface — not a worse calculation of the same one.

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py:361`
  *Second, independent leg of the same function. Skipped entirely when the caller passes no `transition_state_charge`.*

**Escape hatch.** Omit the transition state's charge, which skips the comparison. Multiplicity is deliberately **not** checked here at all: spin is not conserved the way charge and atoms are — two doublets may react over a singlet or a triplet surface, and spin-forbidden reactions are real chemistry — so a multiplicity rule would fire on correct novel results.

**Recorded divergence.** The docstring says pseudo-species exemption 'matches `validate_reaction_elemental_balance`'. It does not, quite: this function queries only `ReactionRole.reactant` and exempts only on a *reactant-side* pseudo participant, while `_load_participant_species` exempts the two conservation checks on a pseudo participant on **either** side. A reaction whose only pseudo species is a product is therefore exempt from elemental balance but still held to transition-state composition, and it is compared against a reactant side that carries no balance guarantee. Reported, not changed — this register alters no check behaviour.

## A structure against its own label

### 5. A conformer geometry deposited under a species entry is made of the atoms that entry's own SMILES declares.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_geometry_composition_mismatch` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. No correct calculation produces a geometry that is not made of its own molecule's atoms, so a formula disagreement between a structure and its own identifier is a contradiction rather than an expectation — every energy, frequency and partition function downstream would describe a different molecule under the deposited label.

**Enforced at.**

- `assert_geometry_composition_matches_identity` — `backend/app/services/species_resolution.py:223`
  *Conformer geometries only, via `resolve_species_entry`: the computed-species bundle, `/uploads/conformers`, the computed-reaction bundle and the PDep bundle. **Calculation** input and output geometries are reached by no composition check on any path — benzene coordinates can still be attached as a calculation geometry under a `smiles: "C"` entry. Closing that for *output* geometries would be wrong (an optimisation that dissociated is science to record); for *input* geometries it is an open gap.*

**Escape hatch.** Declare `molecule_kind: pseudo`, which has no atom-resolved composition to agree with. A free electron is deliberately **not** exempt — its composition is not unknown but empty, so any geometry deposited under one is refused, which stops `electron` becoming a quieter way to smuggle a structure past the check. Absence does not block: no geometry, or a SMILES RDKit will not parse, is incompleteness.

### 6. The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The identity's isotope label and the geometry's per-atom masses describe the same nuclei; when they disagree one of them is wrong and there is no defensible way to pick a winner, so a CD3OH identity on an all-protium geometry would yield frequencies for a molecule nobody deposited.

**Enforced at.**

- `assert_geometry_isotopes_match_identity` — `backend/app/services/species_resolution.py:103`
  *Runs from `resolve_species_entry` only when a geometry is supplied. Raises prose with no machine-readable code, so no client can key off it — recorded here as a gap rather than papered over.*

**Escape hatch.** Deposit no geometry. An explicitly declared *standard* isotope is dropped before comparison, so `{1: 1}` on a hydrogen cannot fork an identity away from an unlabelled deposit of the same molecule.

**Recorded divergence.** Not a divergence but a documented false *acceptance*, restated here because a referee will ask: only the multiset is compared, so isotopomers are not distinguished. An identity of `[2H]OC` (CH3-OD) accepts a geometry labelling a methyl hydrogen instead (CH2D-OH) — different molecules with different zero-point energies. Closing it needs an atom-level SMILES-to-XYZ correspondence the repository does not have. Where the two disagree invisibly, the geometry is authoritative for masses and the SMILES only for identity.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 7. A species entry's declared charge equals the summed formal charge of its own SMILES.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and the anchor the reaction-level charge law stands on: `validate_reaction_charge_conservation` sums `Species.charge` and is only meaningful because each value has already been reconciled with the structure it labels. Per ADR 0008 the blocking tier owns the rule and the others cite it, which is why `assert_geometry_composition_matches_identity` deliberately does not re-check charge.

**Enforced at.**

- `canonical_species_identity` — `backend/app/chemistry/species.py:528`
  *Charge is compared against `formal_charge` of the sanitized identity molecule — the sum of RDKit per-atom formal charges, which is a notation convention rather than an electron count. A referee may object that formal-charge assignment on hypervalent, zwitterionic or dative-bonded SMILES is notation-dependent.*

**Escape hatch.** A free electron short-circuits before the comparison, returning a pinned identity pair. Multiplicity is deliberately **not** validated against the SMILES at all: standard SMILES does not encode spin state, so RDKit's inferred radical count is only a hint and the uploaded multiplicity is authoritative — which is what lets singlet CH2 (whose SMILES `[CH2]` implies a triplet) and the singlet and triplet states of O2 be represented.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 8. The charge and spin multiplicity a depositor declares match the ones the electronic-structure log says the calculation was actually run at.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `charge_mismatch`, `multiplicity_mismatch` |
| **Governing ADR** | 0008 |

**Why this tier.** **Placed against the ADR's own reasoning, deliberately.** ADR 0008 names both findings as direct contradictions between a declaration and the parsed evidence, therefore definitional, therefore belonging at the blocking tier — and then defers the promotion, because promoting a warning to a blocker rejects payloads that are accepted today. These checks have never fired on real data, so their false-positive rate is unknown and promoting them first would be unsafe. The register records the gap rather than hiding it: this is the clearest case in TCKDB of a check sitting one tier below where its own governing decision puts it.

**Enforced at.**

- `reconcile_charge_multiplicity` — `backend/app/services/charge_multiplicity_reconciliation.py:217`
  *Re-reads charge and multiplicity from the uploaded artifact using the wired Gaussian, ORCA, Psi4 and Molpro parsers.*

**Escape hatch.** Absence is not contradiction: if the producing program is not one of the wired parsers, the artifact is missing, the log is truncated, or the declarations inside a single log disagree with each other, the value is left unknown and **no** warning is emitted. Only a value genuinely read from the log may contradict a declaration — emitting a mismatch because parsing failed would fabricate a contradiction out of ignorance.

### 9. An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0008, 0002 |

**Why this tier.** An expectation, and correctly non-blocking. An optimisation that rearranged, dissociated or transferred a proton is science to record, not a payload to refuse, and connectivity perception from XYZ is unreliable for exactly the weak complexes, radicals, ions and stretched geometries where a genuine rearrangement would matter. The result is written as an evidence row that grades the record at read time; it never refuses an upload.

**Enforced at.**

- `validate_calculation_geometry` — `backend/app/services/geometry_validation.py:120`
  *Species-owned `opt` calculations only. Transition states are deliberately excluded, having no canonical SMILES to compare against. Best-effort by policy: a missing SMILES, a missing output geometry, unparseable coordinates or a raising chemistry layer all write nothing and let the upload continue. A Kabsch RMSD above 1.0 A against the input geometry is recorded as a separate suspicion signal.*

**Escape hatch.** The whole check is advisory, so there is nothing to escape. What a consumer must not do is read a `fail` row as 'this calculation is scientifically invalid'; it means only that the automated identity validator found a mismatch.

**Recorded divergence.** The stored column is named `is_isomorphic` and the surrounding policy is worded as graph isomorphism, but the code tests the **molecular formula** only. Atom mapping falls back to a SMILES-graph matcher whenever bond perception from XYZ fails, which is the common case for the radicals, ions and stretched geometries this service mostly sees, and that fallback rejects a candidate on one condition: the per-element atom counts disagree. Verified by direct call in the module docstring — ethanol declared with dimethyl ether deposited passes, and methane with one hydrogen pulled to 5 A passes. So the rearrangement, bond-breaking, dissociation and proton-transfer cases the module was written to catch are not caught. Already self-documented in the module docstring rather than discovered here; recorded because the field name is what a consumer sees and it still overstates the guarantee.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

## Stationary points

### 10. A transition state has at least one imaginary vibrational mode, exactly one of them is designated the reaction coordinate, and no undeclared mode is stiff enough to make that designation meaningless.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_no_imaginary_mode`, `transition_state_reaction_coordinate_not_designated`, `transition_state_reaction_coordinate_ambiguous` |
| **Governing ADR** | 0012, 0008 |

**Why this tier.** Definitional, and narrower than what it replaced. ADR 0008's worked example was `n_imag == 1`; ADR 0012 retired that, because 'exactly one negative eigenvalue' is a statement about the exact Hessian at the exact stationary point on a smooth surface and a deposit contains none of those. Two scientifically correct calculations of the same saddle point can return `n_imag == 1` and `n_imag == 3`, so a gate a depositor passes by switching to `Int=UltraFine` is not a gate on science — and its cheapest workaround is deleting a line from the frequency list, which turns visible ambiguity into invisible falsehood. What survives the translation into a database row is a *contract*: no imaginary mode at all means there is no reaction coordinate and the structure is not a transition state; more than one with no designation means the record cannot answer the question every transition-state-theory code asks it; and an undeclared mode at least as stiff as the designated one means the designation is an assertion the record does not support. None of the three can be produced by a correct calculation that has been honestly described.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:698`
  *A transition state carries no `stationary_point_kind` column — the entity *is* the claim — so the rule needs no kind argument. Upload schemas call `raise_for_blocking_findings` from a `model_validator`, so the contradiction becomes a 422 before the route body opens a submission. The designation is then persisted on `calc_freq_result.reaction_coordinate_mode_index`, which is what lets the read-time trust rubric cite this judgement instead of re-deriving it.*
- `_check_ts_reaction_coordinate_designated` and `_detect_transition_state_entry_hard_fail` (`backend/app/services/trust/rubrics.py`, `backend/app/services/trust/evaluator.py`) ask at read time whether the record carries the designation this blocking tier required of it — a question about persisted state, not a second opinion about physics. They cannot disagree with the verdict above, which is the collapse ADR 0008 section 9 asked for.

**Escape hatch.** Three, and they are the point of the rule. Deposit no frequency evidence: `n_imag=None` produces no findings at all, because absence is never contradiction. Or deposit the extra imaginary modes honestly — designate the reaction coordinate and give each other mode a disposition (`torsion`, `rigid_body_residue`, `intermolecular`, `ring_pucker`, `symmetry_breaking`, or an explicit `unassigned`) — and a genuine higher-order saddle is accepted with a warning and a structural flag. Or, if the extra mode really is the barrier, designate *it*. What has no door is refusing to say which mode is the reaction coordinate.

### 11. A species entry declared a minimum has no imaginary vibrational modes.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `n_imag_contradicts_minimum` |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A covalently bound minimum whose own frequency evidence reports an imaginary mode is mislabelled: the correct response is to re-optimise on a tighter integration grid or to declare it as something else, so refusing the deposit rejects no correct calculation.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:560`
  *One imaginary mode and two-or-more are folded into a single blocking message that names `n_imag_higher_order_saddle` for the higher-order case. A stationary-point kind the module has not been taught about produces no findings — adding an enum member must be a deliberate decision, not a default.*

**Escape hatch.** Declare `species_entry_kind='vdw_complex'`, which records the same mode with a warning instead, or deposit the structure through a transition-state payload if the single imaginary mode is real.

### 12. A van der Waals complex is formally a minimum, so an imaginary mode on one is recorded and flagged rather than refused — unless the mode is too stiff to be an intermolecular one, which suggests a genuine reaction coordinate.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `n_imag_contradicts_minimum`, `n_imag_higher_order_saddle`, `n_imag_suggests_transition_state` |
| **Governing ADR** | 0008 |

**Why this tier.** The carve-out is the scientific content. A van der Waals complex is held together by intermolecular forces and its stretch, bends and hindered internal rotations sit below roughly 50 cm-1 — the region where numerical noise in a finite-difference or quadrature-grid Hessian is comparable to the true curvature. A small imaginary mode there is usually a grid artifact, so refusing it would force an expensive re-run for a physically meaningless mode. This is what earns `vdw_complex` a separate enum member: it is the only place the two minimum kinds behave differently.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:560`
  *Same entry point as the blocking minimum rule; the declared kind decides the tier while the code names the finding. A mode at or above 100 cm-1 additionally raises `n_imag_suggests_transition_state`, because that is far too stiff to be intermolecular.*

**Escape hatch.** This *is* the escape hatch for the blocking minimum rule. Its own cost is that a genuinely mislabelled saddle point deposited as a van der Waals complex is accepted with a warning.

### 13. A transition state's imaginary modes other than the reaction coordinate are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_extra_imaginary_modes_below_tau`, `transition_state_extra_imaginary_mode_above_tau`, `transition_state_extra_imaginary_modes_not_assessable` |
| **Governing ADR** | 0012 |

**Why this tier.** The extra modes cannot be classified from the frequency list, so refusing the deposit would assert a determination the deposit does not contain the information to support — an expectation about numerical quality wearing the costume of a definition. They can also be simply correct: a harmonic model is inapplicable to a torsion, a ring pucker or an intermolecular mode in a loose complex, and a transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck, in which case the extra negative eigenvalue is not an artefact but exactly right. Warning rather than blocking is also the only choice that preserves evidence: a record carrying all three frequencies and the full protocol can be reassessed under a better rule in five years, while a refused deposit leaves nothing behind.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:698`
  *Above tau the record is flagged as well as warned: the flag is ADR 0012's answer to 'warnings get ignored', excluding the record from default transition-state consumption without creating the incentive to edit the frequency list that a hard block creates. Below tau it is warned only. The flag and the tau that decided it are persisted on `calc_freq_result` rather than recomputed, so a later parser improvement cannot silently re-label historical records.*

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:223`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, not in omega, so the uncertainty in a frequency diverges as omega goes to zero: at 300 cm-1 the sign is never in doubt, at 20 cm-1 it is indeterminate. Where the crossover sits depends on how the second derivatives were built, on what integration grid, and how tightly the geometry was converged. The same -42 cm-1 is real negative curvature under an analytic Hessian on a tight grid and indistinguishable from zero under a numerical one on a default grid, so it cannot be classified from the frequency list at all — only against the protocol that produced it. A constant here would be a claim about physics that is false.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded | 50 |

  *When the provenance is missing.* When `freq.hessian_method` is absent — which is the common case, because most outputs do not say — tau is 50 cm-1 and the record stores `protocol_not_recorded` as the basis. The fallback is deliberately the *conservative* row rather than the analytic one: assuming the better case would flag genuine quadrature noise as a real higher-order saddle. Crucially, tau never decides between blocking and warning — every blocking rule here is a contract about what the record says, not about magnitude — so missing provenance changes how loudly a record is flagged and never whether it is accepted.

**Escape hatch.** None is needed — the check never refuses. Its cost runs the other way: a genuine higher-order saddle, a torsional maximum and a valley-ridge inflection are all accepted, and only the structural flag separates them from a clean first-order saddle for a consumer who reads it.

**Recorded divergence.** ADR 0012 recommends replacing the threshold with a determination — projecting each imaginary eigenvector onto the six rigid-body vectors (more than about 90 percent overlap means projection residue) and onto dihedral-rotation vectors (more than about 70 percent means a torsion) — and says those should be implemented *before* tau is tuned, because a determination beats a threshold wherever one is available. They are not implemented, and cannot be: `backend/app/db/models/transition_state.py` records that normal-mode displacement evidence is deliberately absent from TCKDB, 'a producer-side heuristic, not a database record'. So the disposition on each extra mode is *declared by the depositor* and TCKDB cannot check it. Recorded, not resolved: reversing that decision is a schema change and its own ADR.

### 14. A transition state's reaction coordinate should exceed roughly 100 cm-1 in magnitude.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_imaginary_frequency_too_small` |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** ADR 0008's worked counter-example, and the sharpest statement of the whole rule. A very soft imaginary mode is suspicious — often an under-converged geometry or a coarse integration grid — but it can be perfectly real, because flat barriers and variational transition states genuinely produce them. Magnitude is therefore a quality expectation, never a definition, and a check that could fire on a correct novel result must not block.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:698`
  *ADR 0012 changed what this fires *on* without changing what it fires at. It used to read the single imaginary mode of a record that had passed the `n_imag == 1` gate; it now reads the designated reaction coordinate of a record that may carry several. Both thresholds are declared above because both reach the same message: the warning quotes the protocol's tau alongside its own constant, so a reader who is told a reaction coordinate is soft can see immediately whether that calculation could resolve a small mode at all.*

**Thresholds.**

- `TS_IMAGINARY_FREQUENCY_MIN_CM1` = **100 cm-1** — a constant fixed in code.
  A starting point rather than a physical constant: reaction coordinates for hydrogen transfers run to thousands of cm-1, while genuinely flat barriers fall well under 100. Unlike tau it really is fixed in code, because it is a statement about chemistry — what a reaction coordinate looks like — rather than about numerics. It is also the scale that separates a van der Waals complex's soft intermolecular modes from a real reaction coordinate, and is reused for that judgement.
- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:223`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, not in omega, so the uncertainty in a frequency diverges as omega goes to zero: at 300 cm-1 the sign is never in doubt, at 20 cm-1 it is indeterminate. Where the crossover sits depends on how the second derivatives were built, on what integration grid, and how tightly the geometry was converged. The same -42 cm-1 is real negative curvature under an analytic Hessian on a tight grid and indistinguishable from zero under a numerical one on a default grid, so it cannot be classified from the frequency list at all — only against the protocol that produced it. A constant here would be a claim about physics that is false.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded | 50 |

  *When the provenance is missing.* When `freq.hessian_method` is absent — which is the common case, because most outputs do not say — tau is 50 cm-1 and the record stores `protocol_not_recorded` as the basis. The fallback is deliberately the *conservative* row rather than the analytic one: assuming the better case would flag genuine quadrature noise as a real higher-order saddle. Crucially, tau never decides between blocking and warning — every blocking rule here is a contract about what the record says, not about magnitude — so missing provenance changes how loudly a record is flagged and never whether it is accepted.

**Escape hatch.** None is needed — the check never refuses. A referee should read the threshold as a tunable reporting line, not a claim about physics.

### 15. A deposited saddle point should carry passing intrinsic-reaction-coordinate evidence that it connects the declared reactants and products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_missing_irc_evidence` |
| **Governing ADR** | 0008 |

**Why this tier.** Absence, not contradiction. Refusing a transition state without an IRC would lose the saddle point entirely, and a saddle point with no IRC is an incomplete record rather than a false one. The evidence is recommended, not required.

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py:33`
  *Every path that can carry a transition state routes through this seam — the PDep bundle, the computed-reaction bundle and the standalone transition-state upload — so all three write identical rows and report an identical gap. Before the seam existed only the PDep bundle could deposit the evidence, so a TS uploaded any other way always read back as `irc: absent` even when the depositor had run one.*

**Escape hatch.** None needed — the warning is the accommodation. Note the warning fires on absence of a *passing* record, so evidence that was run and failed is stored and still warns.

## Atom mapping across a reaction

### 16. An atom does not change element on the way across a reaction: carbon does not map onto nitrogen.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional. A map asserting that an element transmutes is a record that cannot be what it says it is, not an unusual result.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py:228`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `ck_reaction_atom_map_pair_element_matches` (check on `reaction_atom_map_pair`)
  `upper(element) = upper(ts_element)`

**Escape hatch.** Case is not load-bearing. The comparison is deliberately case-insensitive because the two ends quote two different geometries, and a geometry stores the symbol its depositor's XYZ wrote — carbon becoming nitrogen is a contradiction, while `Cl` becoming `CL` is one program shouting where another did not. Isotope mass number is deliberately *not* carried across the same way, because a NULL disables a MATCH SIMPLE foreign key; isotope consistency is checked in the service layer instead.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 17. One saddle-point atom is claimed by exactly one atom of each leg, and one participant atom maps to exactly one saddle-point atom.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional. A map is a bijection or it is not a map; an atom claimed twice describes no mechanism at all.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py:228`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `uq_reaction_atom_map_pair_ts_atom_index` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, side, ts_atom_index)`
- `uq_reaction_atom_map_pair_atom_map_id` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, structure_participant_id, atom_index)`

**Escape hatch.** Per leg, not globally: the reactant and product legs each claim the whole saddle point, which is the point of storing two maps both pointing at it. A `side` column exists on the pair row purely so this can be a unique constraint, because SQL cannot dereference the participant to find its role.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 18. Every atom index in a map is counted against a named geometry that the participant actually owns, and names an atom that geometry actually has.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional, and it is where ADR 0011's central choice is cashed out. Atom indices are not a property of a species — `geometry_atom.atom_index` is a property of a *geometry* — so 'reactant atom 3' is meaningless until the geometry being counted is named. An index counted against the wrong geometry silently means the wrong atom, which is the failure mode geometry-relative indexing was chosen to make impossible.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py:228`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `fk_reaction_atom_map_pair_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(geometry_id, atom_index, element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(transition_state_geometry_id, ts_atom_index, ts_element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_structure_participant` (foreign_key on `reaction_atom_map_pair`)
  `(structure_participant_id, side) -> reaction_entry_structure_participant(id, role)`

**Escape hatch.** None, and the cost is stated in ADR 0011: the map is welded to the geometries it names, so depositing a second conformer or re-optimising at another level of theory does not carry it across. Canonical-order-relative indexing would be portable, and was rejected because its failure mode is a map that looks fine and refers to a different atom order than the depositor intended. Portability can be added later as a derived view; correctness cannot be retrofitted onto records nobody can verify.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 19. When a map covers every declared participant of an atom-balanced reaction, both legs claim the same saddle-point atoms and no saddle-point atom is left unclaimed.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional, but only under a precondition that has to be checked first. A reactant atom unaccounted for in the products is a contradiction *when no species is missing*. When the declared reaction is not atom-balanced a species genuinely is missing, and the same discrepancy is incompleteness rather than contradiction — so the rule is gated on the map being complete over every participant and on the reaction balancing, and warns instead otherwise.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py:228`
  *Wire boundary only. This one has no database counterpart: it is a statement about a whole map rather than about one pair row, and a per-row constraint cannot see it.*

**Escape hatch.** Leave the map incomplete, or deposit an unbalanced reaction — either drops the rule to the warning tier by design rather than by accident.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

### 20. A reaction that has a transition state should say which atom of the reactants is which atom of the saddle point and of the products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_absent` |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence, not contradiction. An unmapped reaction is an incomplete record rather than a false one — the rate constant is still the rate constant and what is missing is the mechanistic detail. Blocking would reject correct science over evidence the depositor may not have, and would make every reaction already in the database undepositable.

**Enforced at.**

- `_warn_absent` — `backend/app/services/reaction_atom_map.py:309`
  *A reaction with no transition state is not warned about: both legs of a map run toward the saddle point, so a barrierless channel has nothing to map onto and a warning it could never satisfy would train depositors to ignore the one that matters. The PDep bundle has no `atom_map` field yet, so on that path the warning carries a different remedy sentence rather than naming a field that does not exist.*

**Escape hatch.** None is needed — the warning *is* the accommodation. TCKDB deliberately will not infer a map: several chemically distinct maps are usually consistent with the same reactants and products, so choosing one by algorithm would manufacture provenance.

### 21. A supplied atom map should cover every declared participant molecule, every atom of each mapped participant, and every atom of the saddle point.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_participants_incomplete`, `reaction_atom_map_atoms_incomplete` |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence again, at finer grain. A partial map is a true-but-partial record; only a map that contradicts *itself* is refused, and that is handled at the blocking tier by `validate_reaction_atom_map` and by the constraints on `reaction_atom_map_pair`.

**Enforced at.**

- `_warn_incomplete` — `backend/app/services/reaction_atom_map.py:340`
  *Two codes from one seam: `reaction_atom_map_participants_incomplete` when a declared molecule is missing from the map entirely, `reaction_atom_map_atoms_incomplete` when a mapped participant leaves its own atoms unmapped or a leg leaves saddle-point atoms claimed by nobody.*

**Escape hatch.** None.

### 22. An atom map records whether a human asserted it or an algorithm produced it, an inferred map names the algorithm, and neither attribution can be relabelled afterwards.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0011 |

**Why this tier.** Not a runtime check but a shape. ADR 0011 permits inference only as a labelled and separable thing, because an atom map is a scientific claim about a mechanism and picking one by algorithm and storing it unlabelled would manufacture provenance — the same failure ADR 0009 identified when a network-wide energy-transfer value was duplicated across wells. The immutability trigger closes the laundering path a check constraint cannot see: `UPDATE reaction_atom_map SET source='declared', note=NULL` satisfies both existing constraints, and a CHECK cannot read `OLD`.

**Enforced at.**

- `ck_reaction_atom_map_inferred_requires_note` (check on `reaction_atom_map`)
  `source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')`
- `trg_reaction_atom_map_source_immutable` (trigger on `reaction_atom_map`)
  `BEFORE UPDATE FOR EACH ROW: refuse any change to the source column`

**Escape hatch.** `note` and `equivalent_map_count` stay mutable on purpose, so a depositor can correct a description or record newly counted symmetry-equivalent maps without touching the attribution. Symmetry means a valid map is often not unique; ADR 0011 declines to canonicalise among equivalent maps and leaves reaction-path degeneracy to a later decision.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

## Rate coefficients

### 23. An Arrhenius pre-exponential factor carries units of the dimensionality its reaction order requires — per-second for unimolecular, concentration^-1 time^-1 for bimolecular, concentration^-2 time^-1 for termolecular.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The dimensionality of A follows from the rate law, so an A in cm3/mol/s on a unimolecular reaction is not an unusual result but a number that cannot mean what it says. A mis-declared unit is also silently catastrophic downstream, since nothing later in the stack can recover the intended order from the value alone.

**Enforced at.**

- `validate_a_units_for_molecularity` — `backend/app/chemistry/units.py:49`
  *Called from the kinetics upload schema, so it refuses at the wire boundary. The order is not simply `len(reactants)`: a simple `+M` third-body reaction carries a `[M]` term on the main line and validates one order higher, while a falloff reaction's main line is the high-pressure limit k-infinity and keeps `len(reactants)`, its low-pressure limit k0 being validated separately one order up.*

**Escape hatch.** None, and the refinements are the reason it can block without firing on correct science: PLOG and Chebyshev are refused the `is_third_body` flag outright, because both already encode the full pressure dependence and the flag would otherwise inflate the expected order by one — rejecting a PLOG entry carrying the *correct* units and accepting one carrying the units of the next order up.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

## Statistical mechanics

### 24. A partition function belongs to exactly one subject — a species entry or a transition-state entry, never both and never neither.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0008 |

**Why this tier.** A modelling position rather than an arithmetic bound. Canonical transition state theory needs the saddle point's own partition function, so a transition state has to be a first-class subject of a statmech row. The alternative — encoding a transition state as a pseudo-species — would make every partition function's subject ambiguous and would put saddle points into a kind reserved for lumped and phenomenological constructs that the conservation laws deliberately exempt.

**Enforced at.**

- `ck_statmech_statmech_exactly_one_subject` (check on `statmech`)
  `(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)`

**Escape hatch.** None.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

## Pressure-dependent networks

### 25. A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `reported_network_solve` |
| **Governing ADR** | 0010, 0008 |

**Why this tier.** The blocking half is definitional: a computed solve with *zero* state energies contradicts its own kind, and a reported solve with no literature would assert rates carrying neither a derivation nor a source. The accepting half is why the token exists at all — published PLOG and Chebyshev fits are correct, common, citable science, so the coverage rules that are right for a solve run here could fire on a correct result and must not block. They warn instead, on every read path that reaches a rate.

**Enforced at.**

- `ck_network_solve_reported_requires_literature` (check on `network_solve`)
  `kind <> 'reported' OR literature_id IS NOT NULL`
- `ct_network_solve_computed_evidence` (trigger on `network_solve`)
  `deferred constraint trigger, at COMMIT: a computed solve must hold at least one state energy; at least one energy-transfer model if its network declares a well; at least one channel barrier if its network declares a saddle-point path`

**Escape hatch.** Declare `kind='reported'` and cite the literature. That relaxes the state-energy, channel-barrier and energy-transfer coverage rules a computed solve is held to, which is what makes a paper's supplementary table depositable at all — before the token existed such rates could not be deposited, so they went into somebody's private mechanism file, uncited and unversioned.

**Recorded divergence.** Existence, not coverage — and the trigger must not be read as the whole contract. The database guarantees a computed solve carries nonzero evidence of each applicable class; the three coverage rules (one energy per state, one energy-transfer model per (well, collider) pair or a network-wide declaration, one barrier per saddle-point path) remain properties of the single wired upload path. A computed solve with four energies out of five passes the database and fails the validator. ADR 0010's amendment states this deliberately: a computed solve with *zero* energies is a contradiction and may block, while an incomplete one is a true record to be graded by the trust and reproducibility layers. Separately, `kind` cannot surface in CHEMKIN export, which has no provenance field; a tripwire test guards the moment network kinetics first reach mechanism output.

### 26. A collisional energy-transfer model records whether its ⟨ΔE⟩down was determined per (well, collider) pair or declared once for the whole network.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `network_wide_energy_transfer_scope` |
| **Governing ADR** | 0009, 0008 |

**Why this tier.** A network-wide ⟨ΔE⟩down is correct, common, published science — Arkane, RMG and MESS inputs routinely specify a single `SingleExponentialDown` applied network-wide — so a check demanding one entry per (well x collider) pair could fire on a correct result and must not block. It is an expectation about *resolution*, not a definition. What stays definitional still blocks: a `per_well` entry naming no well contradicts itself, a `network_wide` entry naming one contradicts itself, and a payload mixing the two is genuinely ambiguous.

**Enforced at.**

- `ck_network_solve_energy_transfer_scope_columns_agree` (check on `network_solve_energy_transfer`)
  `(scope='per_well' AND state_id IS NOT NULL AND collider_species_entry_id IS NOT NULL) OR (scope='network_wide' AND state_id IS NULL AND collider_species_entry_id IS NULL)`

**Escape hatch.** Declare `scope='network_wide'`. The physics behind the old per-well rule was never in dispute — ⟨ΔE⟩down depends on the density of states of the excited well and on the collider's ability to accept internal energy, so argon and helium do not relax the same well identically. The rule was wrong in practice because it confused what the quantity *is* with what a calculation *determined*: the only way to satisfy it was to paste one number once per well, and the repository's own Arkane ingester did exactly that. Those rows are indistinguishable from independently determined values — a provenance loss manufactured by the validation itself, worse than the gap it closed, because an absent value is honest while a duplicated one is a false positive every consumer will faithfully propagate.

## Reproducibility

### 27. Whether a record's preserved evidence is sufficient to understand, audit or repeat it is assessed separately from how far its evidence is trusted and from whether a curator approved it, and the three may disagree.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | *(none — prose only)* |
| **Governing ADR** | 0002, 0005 |

**Why this tier.** Not a check that fires but a position about what may never be collapsed into a single verdict. Reproducibility is graded under append-only, rubric-versioned assessments rather than as a field on a scientific row or an alias for review status, so a rubric can be revised without rewriting history and an old judgement stays interpretable. This is the same reasoning that made ADR 0005 record execution environments rather than grade them, and it is what lets the warning tiers elsewhere in this register be defensible: an incomplete record is accepted precisely because a separate layer exists to say how incomplete it is.

**Enforced at.**

- `reproducibility_assessment` rows carry `described` / `auditable` / `rerunnable` under a versioned rubric, append-only, independent of `record_review` and of the trust evaluator (`app/services/trust/`)

**Escape hatch.** None.

*(This check raises prose with no machine-readable code. Recorded as a gap rather than invented, because a code that appears in no message is a code no client can match on.)*

---

Declarations live beside the checks they describe; see `backend/app/scientific_checks/__init__.py` for how to add one, and `backend/app/scientific_checks/declarations.py` for the two populations that cannot self-declare (PostgreSQL objects, and wire schemas that are forbidden from importing the backend).
