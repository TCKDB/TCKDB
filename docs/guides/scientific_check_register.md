# The scientific check register

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py`.
Every `file::name` here is resolved from the live function object at
generation time, so it cannot go stale the way a hand-written table does.
Anchors name the function rather than a line, so editing the code around a
check does not change this document.

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
disagree, this one is right by construction.

## How to read a row

- **Asserts** — what the check claims, in one sentence a chemist would
  recognise. Not a description of the implementation.
- **Tier** — the ADR 0008 consequence, with the justification for *that* tier
  in the ADR's own terms.
- **Code**, and **where it reaches a client** — these are two different facts
  and the register used to state only the first. A code is not a contract until
  something carries it somewhere a consumer can read it, and for a long time
  nothing did: six checks spelled their code inside an English sentence
  (`"... (reaction_mass_balance_failed)."`) that no parser matched, and the 422
  body's own `code` field said `validation_error` for every chemistry refusal
  in the system. Naming the channel is what makes the claim checkable —
  `backend/tests/db/test_scientific_check_register.py` holds each channel to
  its obligation, and an `error_envelope` code must both be raised through the
  typed path and be proved to arrive by an end-to-end HTTP test.
- **Enforced at** — `file::qualified_name` for Python, or the
  constraint/trigger name for PostgreSQL. Database names are verified against live schema metadata by
  `backend/tests/db/test_scientific_check_register.py`, not trusted as strings.
  Where a constraint names the 409 code it returns, that code is printed with
  it. A database-held position is the *strongest* guarantee here — no write
  path can bypass a check constraint — and it used to be the one a client was
  told least about, because every violation collapsed into its SQLSTATE bucket
  and arrived as `state_conflict`. The mapping is keyed on the constraint name
  PostgreSQL reports, never on the driver's message text, and each one is
  proved by `backend/tests/api/test_api_database_constraint_codes.py`, which
  provokes the real violation and reads the code out of the response body.
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
| `block` | 17 | Refuses the payload. ADR 0008 permits this only for a definition or a contract — a record no correct calculation could produce. |
| `warn` | 8 | Accepts the payload and records a machine-readable warning. The tier for expectations (which could fire on a correct novel result) and for absences (an incomplete record is still a true one). |
| `label` | 1 | Labels a stored record at read time without refusing anything — a `HardFailReason` in the trust evaluator. For facts TCKDB observes about a record after it was accepted, which no upload-time check could have refused because they did not exist yet. |
| `review` | 0 | Referred to `machine_review` under a versioned rubric. ADR 0008 puts every cross-check against external reference data here. |
| `structural` | 5 | Not an ADR 0008 consequence tier. The position is enforced by the shape of the schema, so a record violating it cannot be represented. |
| **total** | **31** | |

## Where a check's code reaches a client

| Channel | Entries | What a consumer can do with it |
| --- | --- | --- |
| `error_envelope` | 18 | the `code` field of the 422 error body — a client can branch on it |
| `upload_warning` | 9 | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| `trust_label` | 1 | a read-time trust label (`HardFailReason`), not any refusal |
| `database_constraint` | 1 | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |
| `none` | 2 | *nothing carries a code* |
| **total** | **31** | |

## Recorded divergences

Where a check's documentation and its behaviour disagree, or where a guarantee is narrower than its name suggests. Every one of these is reported, never silently fixed — the register changes no check behaviour.

- **[8]** The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.
- **[11]** An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.
- **[15]** A transition state's imaginary modes other than the reaction coordinate are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.
- **[28]** A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

## Entries

## Conservation across a reaction

### 1. The reactant and product sides of a reaction contain the same number of atoms of every element.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_mass_balance_failed` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. Mass balance is what makes a set of species a reaction rather than a list, so no correct calculation can produce an unbalanced one; the check cannot fire on a correct novel result.

**Enforced at.**

- `validate_reaction_elemental_balance` — `backend/app/services/reaction_resolution.py::validate_reaction_elemental_balance`
  *Called from `resolve_chem_reaction`, so it fires on every path that resolves a reaction, including the PDep bundle.*

**Escape hatch.** Declare a participant with `molecule_kind: pseudo`. A lumped or phenomenological construct has no atom-resolved composition, so one such participant suspends the law for the whole reaction. A declared electron does **not** exempt it — an electron contributes zero atoms and the reaction still has to balance.

### 2. The summed formal charge of a reaction's reactants equals that of its products.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_charge_not_conserved` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and only because the escape hatch exists. Electrons are neither created nor destroyed by rearranging bonds. Without a way to name a free electron the rule would in fact assert 'every participant was declared', which is an expectation about the depositor rather than a definition of a reaction, and ADR 0008 disqualifies an expectation from blocking.

**Enforced at.**

- `validate_reaction_charge_conservation` — `backend/app/services/reaction_resolution.py::validate_reaction_charge_conservation`
  *Sums `Species.charge`, which `canonical_species_identity` has already reconciled against the formal charge of each species' own SMILES.*

**Escape hatch.** Declare the free electron as a participant — `{"molecule_kind": "electron", "smiles": "[e-]", "charge": -1, "multiplicity": 2}` — which is how associative and dissociative attachment, photoionization and photodetachment are deposited. It contributes -1 to the side it sits on and zero atoms, so elemental balance still has to be satisfied separately. A `pseudo` participant suspends the law entirely, as it does for elemental balance. Conservation is not neutrality: any net charge is accepted as long as both sides carry the same one, so ion-molecule reactions are unaffected.

### 3. A saddle point is made of exactly the atoms of the reaction it is declared to sit in.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_composition_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A transition state is a stationary point on the potential energy surface *of those atoms*, so a saddle point with a different molecular formula cannot be that reaction's saddle point, whatever else is true of it.

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py::validate_transition_state_composition`
  *Composition is read from the saddle-point geometry when there is one and from `unmapped_smiles` otherwise. The PDep path passes no SMILES, so it compares geometry only.*

**Escape hatch.** Declare the extra species as participants of the reaction. A `pseudo` *reactant* exempts the reaction; a pseudo product does not, and that is not an oversight — see below. Absence does not block: no geometry and no parseable SMILES means nothing is compared, and an unparseable transition-state SMILES is treated as silence rather than as a contradiction, because a TS SMILES is a lossy label for a structure that is by construction not a stable molecule. The pseudo-species exemption here is narrower than `_load_participant_species`'s, and deliberately so. That helper exempts elemental balance and charge conservation on a pseudo participant on **either** side, because both compare one side against the other and a lumped construct makes the side it sits on unknowable. This check compares the saddle point against the **reactant side only**, so only a *reactant* being pseudo can make it meaningless; a pseudo *product* leaves the reactant side fully atom-resolved and is not exempted. Aligning the two would discard a guarantee that is still well-defined, and would discard it exactly where it is worth most: a reaction with a pseudo product has already lost elemental balance and charge conservation, so this is the only atom-level statement left about its saddle point.

### 4. A saddle point carries the same total charge as the reactants it sits between.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_charge_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. Charge is conserved along a reaction coordinate, so a saddle point at a different charge is on a different potential energy surface — not a worse calculation of the same one.

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py::validate_transition_state_composition`
  *Second, independent leg of the same function. Skipped entirely when the caller passes no `transition_state_charge`.*

**Escape hatch.** Omit the transition state's charge, which skips the comparison. Multiplicity is deliberately **not** checked here at all: spin is not conserved the way charge and atoms are — two doublets may react over a singlet or a triplet surface, and spin-forbidden reactions are real chemistry — so a multiplicity rule would fire on correct novel results. The pseudo-species exemption here is narrower than `_load_participant_species`'s, and deliberately so. That helper exempts elemental balance and charge conservation on a pseudo participant on **either** side, because both compare one side against the other and a lumped construct makes the side it sits on unknowable. This check compares the saddle point against the **reactant side only**, so only a *reactant* being pseudo can make it meaningless; a pseudo *product* leaves the reactant side fully atom-resolved and is not exempted. Aligning the two would discard a guarantee that is still well-defined, and would discard it exactly where it is worth most: a reaction with a pseudo product has already lost elemental balance and charge conservation, so this is the only atom-level statement left about its saddle point.

### 5. The saddle-point atoms an IRC mapping assigns to a declared participant are that participant's own atoms, element for element.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_irc_mapping_element_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0011 |

**Why this tier.** Definitional. 'These saddle-point atoms become C2H4' while those atoms are C2O2H2 is a contradiction no correct calculation can produce — the same class of claim `CHECK_TRANSITION_STATE_COMPOSITION` already blocks, one level finer, per participant rather than per side. It is also what the register's own consistency requires: the identical assertion expressed as a `reaction_atom_map` is refused by `CHECK_ATOM_MAP_ELEMENT_CONSERVED`, at the wire boundary and again by a composite foreign key into `geometry_atom`. Two surfaces enforcing different standards on the same claim is not a defensible position for either — and the divergence was not theoretical: a well-formed partition handing ethene two oxygens and HO2 three hydrogens was accepted, under a fixture comment that correctly said 'C2H4 (six atoms)'.

**Enforced at.**

- `validate_ts_evidence_participant_composition` — `backend/app/services/reaction_resolution.py::validate_ts_evidence_participant_composition`
  *Called from `persist_transition_state_validation_evidence`, the single seam every deposit path that can carry a transition state already routes through, so the PDep bundle, the computed-reaction bundle and the standalone transition-state upload cannot enforce different standards. It is a service-layer check rather than a wire-boundary one because a participant's composition comes from its SMILES, and `tckdb_schemas` is chemistry-free — RDKit is not available where `validate_ts_evidence_set` runs. That function keeps the *shape* half of the rule: keys name every declared participant, indices partition the TS atoms exactly once, and a participant's list is empty exactly when the payload declares that participant to have no atoms. The last of those is the shape half of the same claim this check makes about composition: an empty list is refused for a molecule at the wire boundary from the declared `molecule_kind`, and refused again here against the resolved species, whose SMILES is what actually settles how many atoms it has.*

**Escape hatch.** Omit the participant mappings. They are optional on every path — evidence without them still deposits and still reads back as `irc: present` — so a depositor who cannot resolve the partition per atom is never forced to guess at one. Declaring a participant `molecule_kind: pseudo` skips that participant alone rather than the whole record, because the others' compositions are still well-defined. Isotopologues are safe by construction: both sides are compared through `resolve_element_symbol`, so a geometry written `D` counts as the hydrogen its SMILES spells `[2H]`. A free electron needs no hatch either: it is written as an empty atom list, which this check compares against the empty composition `MoleculeKind.electron` actually has, so a reaction that releases one is held to the same standard as every other rather than having to omit its mappings.

## A structure against its own label

### 6. A deposit's `molecule_kind` agrees with the kind already stored for the species identity it resolves to — one identity is one kind of thing.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_kind_conflict` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and load-bearing for two other blocking checks. Species identity is `(smiles, charge, multiplicity)` and does not include `kind`, so two deposits that disagree about kind address one row and at most one of them can be right — that is a contradiction, not incompleteness. It has to block rather than warn because `validate_reaction_elemental_balance` and `validate_reaction_charge_conservation` read `species.kind` from the stored row: silently inheriting `pseudo` would switch mass balance and charge conservation off for an ordinary molecule, on every future reaction containing it, with nothing on any surface recording that it had happened.

**Enforced at.**

- `assert_declared_kind_matches_stored` — `backend/app/services/species_resolution.py::assert_declared_kind_matches_stored`
  *Runs on every path through `resolve_species`, which is the sole creator of `species` rows on every upload route. A row written directly to the database — the only way a `pseudo` species exists today, since `canonical_species_identity` refuses that kind — bypasses this, as it bypasses every Python check; what the check guarantees is that no *upload* silently adopts a kind it did not declare.*

**Escape hatch.** None, and deliberately so: the escape hatch would be exactly the silent inheritance the check exists to remove. A depositor who genuinely means the stored kind declares it; one who means the other kind needs a different identity, because the two claims are about the same row.

### 7. A conformer geometry deposited under a species entry is made of the atoms that entry's own SMILES declares.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_geometry_composition_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. No correct calculation produces a geometry that is not made of its own molecule's atoms, so a formula disagreement between a structure and its own identifier is a contradiction rather than an expectation — every energy, frequency and partition function downstream would describe a different molecule under the deposited label.

**Enforced at.**

- `assert_geometry_composition_matches_identity` — `backend/app/services/species_resolution.py::assert_geometry_composition_matches_identity`
  *Conformer geometries only, via `resolve_species_entry`: the computed-species bundle, `/uploads/conformers`, the computed-reaction bundle and the PDep bundle. **Calculation** input and output geometries are reached by no composition check on any path — benzene coordinates can still be attached as a calculation geometry under a `smiles: "C"` entry. Closing that for *output* geometries would be wrong (an optimisation that dissociated is science to record); for *input* geometries it is an open gap.*

**Escape hatch.** Declare `molecule_kind: pseudo`, which has no atom-resolved composition to agree with. A free electron is deliberately **not** exempt — its composition is not unknown but empty, so any geometry deposited under one is refused, which stops `electron` becoming a quieter way to smuggle a structure past the check. Absence does not block: no geometry, or a SMILES RDKit will not parse, is incompleteness.

### 8. The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_geometry_isotope_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The identity's isotope label and the geometry's per-atom masses describe the same nuclei; when they disagree one of them is wrong and there is no defensible way to pick a winner, so a CD3OH identity on an all-protium geometry would yield frequencies for a molecule nobody deposited.

**Enforced at.**

- `assert_geometry_isotopes_match_identity` — `backend/app/services/species_resolution.py::assert_geometry_isotopes_match_identity`
  *Runs from `resolve_species_entry` only when a geometry is supplied. The refusal was prose-only until the code above was attached; a client could not tell it from any other 422, which is why the entry used to record the gap here.*

**Escape hatch.** Deposit no geometry. An explicitly declared *standard* isotope is dropped before comparison, so `{1: 1}` on a hydrogen cannot fork an identity away from an unlabelled deposit of the same molecule.

**Recorded divergence.** Not a divergence but a documented false *acceptance*, restated here because a referee will ask: only the multiset is compared, so isotopomers are not distinguished. An identity of `[2H]OC` (CH3-OD) accepts a geometry labelling a methyl hydrogen instead (CH2D-OH) — different molecules with different zero-point energies. Closing it needs an atom-level SMILES-to-XYZ correspondence the repository does not have. Where the two disagree invisibly, the geometry is authoritative for masses and the SMILES only for identity.

### 9. A species entry's declared charge equals the summed formal charge of its own SMILES.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_smiles_charge_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and the anchor the reaction-level charge law stands on: `validate_reaction_charge_conservation` sums `Species.charge` and is only meaningful because each value has already been reconciled with the structure it labels. Per ADR 0008 the blocking tier owns the rule and the others cite it, which is why `assert_geometry_composition_matches_identity` deliberately does not re-check charge.

**Enforced at.**

- `canonical_species_identity` — `backend/app/chemistry/species.py::canonical_species_identity`
  *Charge is compared against `formal_charge` of the sanitized identity molecule — the sum of RDKit per-atom formal charges, which is a notation convention rather than an electron count. A referee may object that formal-charge assignment on hypervalent, zwitterionic or dative-bonded SMILES is notation-dependent.*

**Escape hatch.** A free electron short-circuits before the comparison, returning a pinned identity pair. Multiplicity is deliberately **not** validated against the SMILES at all: standard SMILES does not encode spin state, so RDKit's inferred radical count is only a hint and the uploaded multiplicity is authoritative — which is what lets singlet CH2 (whose SMILES `[CH2]` implies a triplet) and the singlet and triplet states of O2 be represented.

### 10. The charge and spin multiplicity a depositor declares match the ones the electronic-structure log says the calculation was actually run at.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `charge_mismatch`, `multiplicity_mismatch` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0008 |

**Why this tier.** **Placed against the ADR's own reasoning, deliberately.** ADR 0008 names both findings as direct contradictions between a declaration and the parsed evidence, therefore definitional, therefore belonging at the blocking tier — and then defers the promotion, because promoting a warning to a blocker rejects payloads that are accepted today. These checks have never fired on real data, so their false-positive rate is unknown and promoting them first would be unsafe. The register records the gap rather than hiding it: this is the clearest case in TCKDB of a check sitting one tier below where its own governing decision puts it.

**Enforced at.**

- `reconcile_charge_multiplicity` — `backend/app/services/charge_multiplicity_reconciliation.py::reconcile_charge_multiplicity`
  *Re-reads charge and multiplicity from the uploaded artifact using the wired Gaussian, ORCA, Psi4 and Molpro parsers.*

**Escape hatch.** Absence is not contradiction: if the producing program is not one of the wired parsers, the artifact is missing, the log is truncated, or the declarations inside a single log disagree with each other, the value is left unknown and **no** warning is emitted. Only a value genuinely read from the log may contradict a declaration — emitting a mismatch because parsing failed would fabricate a contradiction out of ignorance.

### 11. An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008, 0002 |

**Why this tier.** An expectation, and correctly non-blocking. An optimisation that rearranged, dissociated or transferred a proton is science to record, not a payload to refuse, and connectivity perception from XYZ is unreliable for exactly the weak complexes, radicals, ions and stretched geometries where a genuine rearrangement would matter. The result is written as an evidence row that grades the record at read time; it never refuses an upload.

**Enforced at.**

- `validate_calculation_geometry` — `backend/app/services/geometry_validation.py::validate_calculation_geometry`
  *Species-owned `opt` calculations only. Transition states are deliberately excluded, having no canonical SMILES to compare against. Best-effort by policy: a missing SMILES, a missing output geometry, unparseable coordinates or a raising chemistry layer all write nothing and let the upload continue. A Kabsch RMSD above 1.0 A against the input geometry is recorded as a separate suspicion signal.*

**Escape hatch.** The whole check is advisory, so there is nothing to escape. What a consumer must not do is read a `fail` row as 'this calculation is scientifically invalid'; it means only that the automated identity validator found a mismatch.

**Recorded divergence.** The stored column is named `is_isomorphic` and the surrounding policy is worded as graph isomorphism, but the code tests the **molecular formula** only. Atom mapping falls back to a SMILES-graph matcher whenever bond perception from XYZ fails, which is the common case for the radicals, ions and stretched geometries this service mostly sees, and that fallback rejects a candidate on one condition: the per-element atom counts disagree. Verified by direct call in the module docstring — ethanol declared with dimethyl ether deposited passes, and methane with one hydrogen pulled to 5 A passes. So the rearrangement, bond-breaking, dissociation and proton-transfer cases the module was written to catch are not caught. Already self-documented in the module docstring rather than discovered here; recorded because the field name is what a consumer sees and it still overstates the guarantee. **Partly closed:** the read surfaces now publish the same boolean under its true name, `formula_matches`, and the stored `validation_reason` no longer says 'not graph-isomorphic'. `is_isomorphic` is kept beside it because it is the stored column and a published field — renaming it is a migration against a deployed table and a breaking API change, and buys nothing that publishing the true name alongside does not. What stays open is the check itself: connectivity is still not tested, and cannot be until there is bond perception trustworthy on the strained and radical structures where a rearrangement would matter.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

## Stationary points

### 12. A transition state has at least one imaginary vibrational mode, exactly one of them is designated the reaction coordinate, and no undeclared mode is stiff enough to make that designation meaningless.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_no_imaginary_mode`, `transition_state_reaction_coordinate_not_designated`, `transition_state_reaction_coordinate_ambiguous` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0012, 0008 |

**Why this tier.** Definitional, and narrower than what it replaced. ADR 0008's worked example was `n_imag == 1`; ADR 0012 retired that, because 'exactly one negative eigenvalue' is a statement about the exact Hessian at the exact stationary point on a smooth surface and a deposit contains none of those. Two scientifically correct calculations of the same saddle point can return `n_imag == 1` and `n_imag == 3`, so a gate a depositor passes by switching to `Int=UltraFine` is not a gate on science — and its cheapest workaround is deleting a line from the frequency list, which turns visible ambiguity into invisible falsehood. What survives the translation into a database row is a *contract*: no imaginary mode at all means there is no reaction coordinate and the structure is not a transition state; more than one with no designation means the record cannot answer the question every transition-state-theory code asks it; and an undeclared mode at least as stiff as the designated one means the designation is an assertion the record does not support. None of the three can be produced by a correct calculation that has been honestly described.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
  *A transition state carries no `stationary_point_kind` column — the entity *is* the claim — so the rule needs no kind argument. Upload schemas call `raise_for_blocking_findings` from a `model_validator`, so the contradiction becomes a 422 before the route body opens a submission. The designation is then persisted on `calc_freq_result.reaction_coordinate_mode_index`, which is what lets the read-time trust rubric cite this judgement instead of re-deriving it.*
- `_check_ts_reaction_coordinate_designated` and `_detect_transition_state_entry_hard_fail` (`backend/app/services/trust/rubrics.py`, `backend/app/services/trust/evaluator.py`) ask at read time whether the record carries the designation this blocking tier required of it — a question about persisted state, not a second opinion about physics. They cannot disagree with the verdict above, which is the collapse ADR 0008 section 9 asked for.

**Escape hatch.** Three, and they are the point of the rule. Deposit no frequency evidence: `n_imag=None` produces no findings at all, because absence is never contradiction. Or deposit the extra imaginary modes honestly — designate the reaction coordinate and give each other mode a disposition (`torsion`, `rigid_body_residue`, `intermolecular`, `ring_pucker`, `symmetry_breaking`, or an explicit `unassigned`) — and a genuine higher-order saddle is accepted with a warning and a structural flag. Or, if the extra mode really is the barrier, designate *it*. What has no door is refusing to say which mode is the reaction coordinate.

### 13. A species entry declared a minimum has no imaginary vibrational modes.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `n_imag_contradicts_minimum` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A covalently bound minimum whose own frequency evidence reports an imaginary mode is mislabelled: the correct response is to re-optimise on a tighter integration grid or to declare it as something else, so refusing the deposit rejects no correct calculation.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`
  *One imaginary mode and two-or-more are folded into a single blocking message that names `n_imag_higher_order_saddle` for the higher-order case. A stationary-point kind the module has not been taught about produces no findings — adding an enum member must be a deliberate decision, not a default.*

**Escape hatch.** Declare `species_entry_kind='vdw_complex'`, which records the same mode with a warning instead, or deposit the structure through a transition-state payload if the single imaginary mode is real.

### 14. A van der Waals complex is formally a minimum, so an imaginary mode on one is recorded and flagged rather than refused — unless the mode is too stiff to be an intermolecular one, which suggests a genuine reaction coordinate.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `n_imag_contradicts_minimum`, `n_imag_higher_order_saddle`, `n_imag_suggests_transition_state` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0008 |

**Why this tier.** The carve-out is the scientific content. A van der Waals complex is held together by intermolecular forces and its stretch, bends and hindered internal rotations sit below roughly 50 cm-1 — the region where numerical noise in a finite-difference or quadrature-grid Hessian is comparable to the true curvature. A small imaginary mode there is usually a grid artifact, so refusing it would force an expensive re-run for a physically meaningless mode. This is what earns `vdw_complex` a separate enum member: it is the only place the two minimum kinds behave differently.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`
  *Same entry point as the blocking minimum rule; the declared kind decides the tier while the code names the finding. A mode at or above 100 cm-1 additionally raises `n_imag_suggests_transition_state`, because that is far too stiff to be intermolecular.*

**Escape hatch.** This *is* the escape hatch for the blocking minimum rule. Its own cost is that a genuinely mislabelled saddle point deposited as a van der Waals complex is accepted with a warning.

### 15. A transition state's imaginary modes other than the reaction coordinate are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_extra_imaginary_modes_below_tau`, `transition_state_extra_imaginary_mode_above_tau`, `transition_state_extra_imaginary_modes_not_assessable` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0012 |

**Why this tier.** The extra modes cannot be classified from the frequency list, so refusing the deposit would assert a determination the deposit does not contain the information to support — an expectation about numerical quality wearing the costume of a definition. They can also be simply correct: a harmonic model is inapplicable to a torsion, a ring pucker or an intermolecular mode in a loose complex, and a transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck, in which case the extra negative eigenvalue is not an artefact but exactly right. Warning rather than blocking is also the only choice that preserves evidence: a record carrying all three frequencies and the full protocol can be reassessed under a better rule in five years, while a refused deposit leaves nothing behind.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
  *Above tau the record is flagged as well as warned: the flag is ADR 0012's answer to 'warnings get ignored', excluding the record from default transition-state consumption without creating the incentive to edit the frequency list that a hard block creates. Below tau it is warned only. The flag and the tau that decided it are persisted on `calc_freq_result` rather than recomputed, so a later parser improvement cannot silently re-label historical records.*

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
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

**Recorded divergence.** ADR 0012 recommends replacing the threshold with a determination — projecting each imaginary eigenvector onto the six rigid-body vectors (more than about 90 percent overlap means projection residue) and onto dihedral-rotation vectors (more than about 70 percent means a torsion) — and says those should be implemented *before* tau is tuned, because a determination beats a threshold wherever one is available. Tau shipped first, so that ordering was not followed; the projections themselves now exist. They are computed at *read* time from `calc_hessian` -- whose eigenvectors, mass-weighted by the per-atom masses `geometry_atom` carries, are the displacement vectors ADR 0012 wanted -- and nothing about them is stored, so no schema change was involved and no historical record was re-decided. See `include=imaginary_mode_projections` on the scientific calculation read, and ADR 0013 as amended. The disposition on each extra mode remains *declared by the depositor*; the determination is reported beside it and a disagreement is surfaced as one, never silently resolved. Under ADR 0008 a projection is an expectation about a record, so it informs this check and does not gate it. Where no Hessian is stored the projection reads 'not determinable', which is not the same answer as 'no residue found'.

### 16. A transition state's reaction coordinate should exceed roughly 100 cm-1 in magnitude.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_imaginary_frequency_too_small` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** ADR 0008's worked counter-example, and the sharpest statement of the whole rule. A very soft imaginary mode is suspicious — often an under-converged geometry or a coarse integration grid — but it can be perfectly real, because flat barriers and variational transition states genuinely produce them. Magnitude is therefore a quality expectation, never a definition, and a check that could fire on a correct novel result must not block.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
  *ADR 0012 changed what this fires *on* without changing what it fires at. It used to read the single imaginary mode of a record that had passed the `n_imag == 1` gate; it now reads the designated reaction coordinate of a record that may carry several. Both thresholds are declared above because both reach the same message: the warning quotes the protocol's tau alongside its own constant, so a reader who is told a reaction coordinate is soft can see immediately whether that calculation could resolve a small mode at all.*

**Thresholds.**

- `TS_IMAGINARY_FREQUENCY_MIN_CM1` = **100 cm-1** — a constant fixed in code.
  A starting point rather than a physical constant: reaction coordinates for hydrogen transfers run to thousands of cm-1, while genuinely flat barriers fall well under 100. Unlike tau it really is fixed in code, because it is a statement about chemistry — what a reaction coordinate looks like — rather than about numerics. It is also the scale that separates a van der Waals complex's soft intermolecular modes from a real reaction coordinate, and is reused for that judgement.
- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
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

### 17. A deposited saddle point should carry passing intrinsic-reaction-coordinate evidence that it connects the declared reactants and products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_missing_irc_evidence` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0008 |

**Why this tier.** Absence, not contradiction. Refusing a transition state without an IRC would lose the saddle point entirely, and a saddle point with no IRC is an incomplete record rather than a false one. The evidence is recommended, not required.

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
  *Every path that can carry a transition state routes through this seam — the PDep bundle, the computed-reaction bundle and the standalone transition-state upload — so all three write identical rows and report an identical gap. Before the seam existed only the PDep bundle could deposit the evidence, so a TS uploaded any other way always read back as `irc: absent` even when the depositor had run one.*

**Escape hatch.** None needed — the warning is the accommodation. Note the warning fires on absence of a *passing* record, so evidence that was run and failed is stored and still warns.

## Atom mapping across a reaction

### 18. An atom does not change element on the way across a reaction: carbon does not map onto nitrogen.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_element_not_conserved` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional. A map asserting that an element transmutes is a record that cannot be what it says it is, not an unusual result.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `ck_reaction_atom_map_pair_element_matches` (check on `reaction_atom_map_pair`)
  `upper(element) = upper(ts_element)`
  Violating this returns **409 `atom_map_element_not_conserved`** — An atom map pairs two atoms of different elements. An atom does not change element on the way across a reaction.

**Escape hatch.** Case is not load-bearing. The comparison is deliberately case-insensitive because the two ends quote two different geometries and nothing guarantees they spell an element the same way — carbon becoming nitrogen is a contradiction, while `Cl` becoming `CL` is one program shouting where another did not. `b4e7c1d20f83` canonicalises the symbol on the way into `geometry_atom.element`, which makes disagreement rare on rows written through the API; it is a convention rather than a constraint, so both the database check and the Python check still normalise instead of assuming it. Isotope mass number is deliberately *not* carried across the same way, because a NULL disables a MATCH SIMPLE foreign key; isotope consistency is checked in the service layer instead.

### 19. One saddle-point atom is claimed by exactly one atom of each leg, and one participant atom maps to exactly one saddle-point atom.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_not_a_bijection` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional. A map is a bijection or it is not a map; an atom claimed twice describes no mechanism at all.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `uq_reaction_atom_map_pair_ts_atom_index` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, side, ts_atom_index)`
  Violating this returns **409 `atom_map_not_a_bijection`** — Two atoms of one leg claim the same saddle-point atom. A map is a bijection or it is not a map.
- `uq_reaction_atom_map_pair_atom_map_id` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, structure_participant_id, atom_index)`
  Violating this returns **409 `atom_map_not_a_bijection`** — One participant atom is mapped more than once. A map is a bijection or it is not a map.

**Escape hatch.** Per leg, not globally: the reactant and product legs each claim the whole saddle point, which is the point of storing two maps both pointing at it. A `side` column exists on the pair row purely so this can be a unique constraint, because SQL cannot dereference the participant to find its role.

### 20. Every atom index in a map is counted against a named geometry that the participant actually owns, and names an atom that geometry actually has.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_indices_not_geometry_relative` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional, and it is where ADR 0011's central choice is cashed out. Atom indices are not a property of a species — `geometry_atom.atom_index` is a property of a *geometry* — so 'reactant atom 3' is meaningless until the geometry being counted is named. An index counted against the wrong geometry silently means the wrong atom, which is the failure mode geometry-relative indexing was chosen to make impossible.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
  *Stated twice on purpose: once at the wire boundary, where the payload already holds every XYZ block the rule needs so the refusal arrives as a clean 422 before anything is written, and once as a database constraint, where a second write path cannot get around it.*
- `fk_reaction_atom_map_pair_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(geometry_id, atom_index, element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(transition_state_geometry_id, ts_atom_index, ts_element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_structure_participant` (foreign_key on `reaction_atom_map_pair`)
  `(structure_participant_id, side) -> reaction_entry_structure_participant(id, role)`

**Escape hatch.** None, and the cost is stated in ADR 0011: the map is welded to the geometries it names, so depositing a second conformer or re-optimising at another level of theory does not carry it across. Canonical-order-relative indexing would be portable, and was rejected because its failure mode is a map that looks fine and refers to a different atom order than the depositor intended. Portability can be added later as a derived view; correctness cannot be retrofitted onto records nobody can verify.

### 21. When a map covers every declared participant of an atom-balanced reaction, both legs claim the same saddle-point atoms and no saddle-point atom is left unclaimed.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_atoms_unaccounted_for` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional, but only under a precondition that has to be checked first. A reactant atom unaccounted for in the products is a contradiction *when no species is missing*. When the declared reaction is not atom-balanced a species genuinely is missing, and the same discrepancy is incompleteness rather than contradiction — so the rule is gated on the map being complete over every participant and on the reaction balancing, and warns instead otherwise.

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
  *Wire boundary only. This one has no database counterpart: it is a statement about a whole map rather than about one pair row, and a per-row constraint cannot see it.*

**Escape hatch.** Leave the map incomplete, or deposit an unbalanced reaction — either drops the rule to the warning tier by design rather than by accident.

### 22. Where a deposit carries both an atom map and an IRC participant mapping for the same saddle point, they agree about which saddle-point atoms each participant is made of.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_contradicts_irc_mapping` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Definitional, because the two surfaces are one claim at two resolutions rather than two claims. An IRC participant mapping partitions the saddle-point atoms among the declared participants; an atom map is, in this schema's own words, 'the refinement of that partition into a bijection'. A refinement that contradicts what it refines is not extra detail, it is a record asserting that one saddle point is two incompatible things at once, and no correct calculation produces both halves of it — the depositor followed one intrinsic reaction coordinate, not two. This is the same class of claim as `CHECK_TS_IRC_MAPPING_ELEMENTS` and `CHECK_ATOM_MAP_ELEMENT_CONSERVED`, which already block, and leaving it advisory would let the pair assert together what neither is allowed to assert alone.

**Enforced at.**

- `validate_atom_map_agrees_with_irc_evidence` — `backend/app/services/reaction_atom_map.py::validate_atom_map_agrees_with_irc_evidence`
  *Called from *both* seams — `persist_reaction_atom_map` and `persist_transition_state_validation_evidence` — and reads both surfaces from the database rather than from either caller's payload, so whichever a deposit writes second delivers the same verdict. Today the second is always the atom map: the computed-reaction bundle is the only payload with an `atom_map` field and writes it after the evidence, and no path can attach a map to a saddle point deposited earlier because every transition-state entry is created fresh by the deposit that writes it. Both are incidental orderings, so neither is relied on.*

**Escape hatch.** Omit one surface, or correct whichever is wrong — the mappings are optional on every path and a partial atom map is always accepted. Three absences are deliberately *not* disagreements: an atom map that omits a participant or leaves atoms unmapped is compared only over what it does claim, a transition state with no passing IRC mapping is not compared at all, and a barrierless channel has neither surface. Two participants on one side that are the same species entry are interchangeable, so a disagreement a permutation within that group would resolve is treated as arbitrary labelling rather than contradiction. A participant with no atoms is not an absence: both surfaces can say it has none -- an empty atom list -- so a reaction releasing a free electron carries a complete partition on both and is compared like any other, with the electron contributing no atoms to either side of the comparison.

### 23. A reaction that has a transition state should say which atom of the reactants is which atom of the saddle point and of the products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_absent` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence, not contradiction. An unmapped reaction is an incomplete record rather than a false one — the rate constant is still the rate constant and what is missing is the mechanistic detail. Blocking would reject correct science over evidence the depositor may not have, and would make every reaction already in the database undepositable.

**Enforced at.**

- `_warn_absent` — `backend/app/services/reaction_atom_map.py::_warn_absent`
  *A reaction with no transition state is not warned about: both legs of a map run toward the saddle point, so a barrierless channel has nothing to map onto and a warning it could never satisfy would train depositors to ignore the one that matters. The PDep bundle has no `atom_map` field yet, so on that path the warning carries a different remedy sentence rather than naming a field that does not exist.*

**Escape hatch.** None is needed — the warning *is* the accommodation. TCKDB deliberately will not infer a map: several chemically distinct maps are usually consistent with the same reactants and products, so choosing one by algorithm would manufacture provenance.

### 24. A supplied atom map should cover every declared participant molecule, every atom of each mapped participant, and every atom of the saddle point.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_participants_incomplete`, `reaction_atom_map_atoms_incomplete` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence again, at finer grain. A partial map is a true-but-partial record; only a map that contradicts *itself* is refused, and that is handled at the blocking tier by `validate_reaction_atom_map` and by the constraints on `reaction_atom_map_pair`.

**Enforced at.**

- `_warn_incomplete` — `backend/app/services/reaction_atom_map.py::_warn_incomplete`
  *Two codes from one seam: `reaction_atom_map_participants_incomplete` when a declared molecule is missing from the map entirely, `reaction_atom_map_atoms_incomplete` when a mapped participant leaves its own atoms unmapped or a leg leaves saddle-point atoms claimed by nobody.*

**Escape hatch.** None.

### 25. An atom map records whether a human asserted it or an algorithm produced it, an inferred map names the algorithm, and neither attribution can be relabelled afterwards.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `atom_map_inferred_requires_note` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0011 |

**Why this tier.** Not a runtime check but a shape. ADR 0011 permits inference only as a labelled and separable thing, because an atom map is a scientific claim about a mechanism and picking one by algorithm and storing it unlabelled would manufacture provenance — the same failure ADR 0009 identified when a network-wide energy-transfer value was duplicated across wells. The immutability trigger closes the laundering path a check constraint cannot see: `UPDATE reaction_atom_map SET source='declared', note=NULL` satisfies both existing constraints, and a CHECK cannot read `OLD`.

**Enforced at.**

- `ReactionAtomMapIn.validate_inferred_names_its_algorithm` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::ReactionAtomMapIn.validate_inferred_names_its_algorithm`
  *The wire half of the rule, and it was missing from this entry until the codes were audited: the register listed only the two schema objects, so it read as enforced by the database alone when in fact an inferred map with no note is refused at the payload boundary first. Only the note half is stated here — the immutability of `source` is a statement about an UPDATE, which no payload validator can see.*
- `ck_reaction_atom_map_inferred_requires_note` (check on `reaction_atom_map`)
  `source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')`
- `trg_reaction_atom_map_source_immutable` (trigger on `reaction_atom_map`)
  `BEFORE UPDATE FOR EACH ROW: refuse any change to the source column`

**Escape hatch.** `note` and `equivalent_map_count` stay mutable on purpose, so a depositor can correct a description or record newly counted symmetry-equivalent maps without touching the attribution. Symmetry means a valid map is often not unique; ADR 0011 declines to canonicalise among equivalent maps and leaves reaction-path degeneracy to a later decision.

## Rate coefficients

### 26. An Arrhenius pre-exponential factor carries units of the dimensionality its reaction order requires — per-second for unimolecular, concentration^-1 time^-1 for bimolecular, concentration^-2 time^-1 for termolecular.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `arrhenius_a_units_molecularity_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The dimensionality of A follows from the rate law, so an A in cm3/mol/s on a unimolecular reaction is not an unusual result but a number that cannot mean what it says. A mis-declared unit is also silently catastrophic downstream, since nothing later in the stack can recover the intended order from the value alone.

**Enforced at.**

- `validate_a_units_for_molecularity` — `backend/app/chemistry/units.py::validate_a_units_for_molecularity`
  *Called from the kinetics upload schema, so it refuses at the wire boundary. The order is not simply `len(reactants)`: a simple `+M` third-body reaction carries a `[M]` term on the main line and validates one order higher, while a falloff reaction's main line is the high-pressure limit k-infinity and keeps `len(reactants)`, its low-pressure limit k0 being validated separately one order up.*

**Escape hatch.** None, and the refinements are the reason it can block without firing on correct science: PLOG and Chebyshev are refused the `is_third_body` flag outright, because both already encode the full pressure dependence and the flag would otherwise inflate the expected order by one — rejecting a PLOG entry carrying the *correct* units and accepting one carrying the units of the next order up.

## Statistical mechanics

### 27. A partition function belongs to exactly one subject — a species entry or a transition-state entry, never both and never neither.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `statmech_subject_not_exactly_one` |
| **Code reaches a client via** | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |
| **Governing ADR** | 0008 |

**Why this tier.** A modelling position rather than an arithmetic bound. Canonical transition state theory needs the saddle point's own partition function, so a transition state has to be a first-class subject of a statmech row. The alternative — encoding a transition state as a pseudo-species — would make every partition function's subject ambiguous and would put saddle points into a kind reserved for lumped and phenomenological constructs that the conservation laws deliberately exempt.

**Enforced at.**

- `ck_statmech_statmech_exactly_one_subject` (check on `statmech`)
  `(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)`
  Violating this returns **409 `statmech_subject_not_exactly_one`** — A partition function names both a species entry and a transition-state entry, or neither. It belongs to exactly one subject.

**Escape hatch.** None.

## Pressure-dependent networks

### 28. A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `reported_network_solve` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0010, 0008 |

**Why this tier.** The blocking half is definitional: a computed solve with *zero* state energies contradicts its own kind, and a reported solve with no literature would assert rates carrying neither a derivation nor a source. The accepting half is why the token exists at all — published PLOG and Chebyshev fits are correct, common, citable science, so the coverage rules that are right for a solve run here could fire on a correct result and must not block. They warn instead, on every read path that reaches a rate.

**Enforced at.**

- `ck_network_solve_reported_requires_literature` (check on `network_solve`)
  `kind <> 'reported' OR literature_id IS NOT NULL`
  Violating this returns **409 `network_solve_reported_requires_literature`** — A network solve declared as 'reported' cites no literature. Transcribed rates must name the publication they came from, because nothing else in the deposit accounts for them.
- `ct_network_solve_computed_evidence` (trigger on `network_solve`)
  `deferred constraint trigger, at COMMIT: a computed solve must hold at least one state energy; at least one energy-transfer model if its network declares a well; at least one channel barrier if its network declares a saddle-point path`

**Escape hatch.** Declare `kind='reported'` and cite the literature. That relaxes the state-energy, channel-barrier and energy-transfer coverage rules a computed solve is held to, which is what makes a paper's supplementary table depositable at all — before the token existed such rates could not be deposited, so they went into somebody's private mechanism file, uncited and unversioned.

**Recorded divergence.** Existence, not coverage — and the trigger must not be read as the whole contract. The database guarantees a computed solve carries nonzero evidence of each applicable class; the three coverage rules (one energy per state, one energy-transfer model per (well, collider) pair or a network-wide declaration, one barrier per saddle-point path) remain properties of the single wired upload path. A computed solve with four energies out of five passes the database and fails the validator. ADR 0010's amendment states this deliberately: a computed solve with *zero* energies is a contradiction and may block, while an incomplete one is a true record to be graded by the trust and reproducibility layers. Separately, `kind` cannot surface in CHEMKIN export, which has no provenance field; a tripwire test guards the moment network kinetics first reach mechanism output.

### 29. A collisional energy-transfer model records whether its ⟨ΔE⟩down was determined per (well, collider) pair or declared once for the whole network.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `network_wide_energy_transfer_scope` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload |
| **Governing ADR** | 0009, 0008 |

**Why this tier.** A network-wide ⟨ΔE⟩down is correct, common, published science — Arkane, RMG and MESS inputs routinely specify a single `SingleExponentialDown` applied network-wide — so a check demanding one entry per (well x collider) pair could fire on a correct result and must not block. It is an expectation about *resolution*, not a definition. What stays definitional still blocks: a `per_well` entry naming no well contradicts itself, a `network_wide` entry naming one contradicts itself, and a payload mixing the two is genuinely ambiguous.

**Enforced at.**

- `ck_network_solve_energy_transfer_scope_columns_agree` (check on `network_solve_energy_transfer`)
  `(scope='per_well' AND state_id IS NOT NULL AND collider_species_entry_id IS NOT NULL) OR (scope='network_wide' AND state_id IS NULL AND collider_species_entry_id IS NULL)`
  Violating this returns **409 `energy_transfer_scope_columns_disagree`** — A collisional energy-transfer model declares a scope its own columns contradict: a 'per_well' model must name the well and the collider it was determined for, and a 'network_wide' one must name neither.

**Escape hatch.** Declare `scope='network_wide'`. The physics behind the old per-well rule was never in dispute — ⟨ΔE⟩down depends on the density of states of the excited well and on the collider's ability to accept internal energy, so argon and helium do not relax the same well identically. The rule was wrong in practice because it confused what the quantity *is* with what a calculation *determined*: the only way to satisfy it was to paste one number once per well, and the repository's own Arkane ingester did exactly that. Those rows are indistinguishable from independently determined values — a provenance loss manufactured by the validation itself, worse than the gap it closed, because an absent value is honest while a duplicated one is a false positive every consumer will faithfully propagate.

## Custody of the evidence

### 30. The bytes TCKDB serves for a stored artifact are the bytes it stored, and a record whose evidence is known not to be is labelled as such at read time rather than graded as if it were intact.

| Field | Value |
| --- | --- |
| **Tier** | `label` |
| **Code** | `artifact_integrity_failed` |
| **Code reaches a client via** | a read-time trust label (`HardFailReason`), not any refusal |
| **Governing ADR** | 0014, 0004, 0002 |

**Why this tier.** It cannot block, because the fact does not exist at upload: the artifact hashed correctly when it was accepted, and the break happened afterwards, in TCKDB's own custody. It cannot be a warning either, because a warning annotates the payload for whoever deposited it, while the party who needs to know here is every future reader of a record the depositor got right. So the consequence is a read-time label. It is a *hard* fail rather than an advisory one because the alternative is to keep publishing an evidence-completeness score computed over bytes TCKDB cannot produce — and note what this reason does not say: unlike every other hard fail, it does not claim the record is wrong, only that the evidence behind it can no longer be shown. The label fires on recorded detections only, so its absence is never a verification claim; an artifact nobody has read has been checked by nothing.

**Enforced at.**

- `load_artifact_bytes` — `backend/app/services/artifact_storage.py::load_artifact_bytes`
  *Recomputes SHA-256 over every retrieval and compares it against the content-addressed key, in constant time. This is the detection; it is reached by the approved-byte download, the ESS-parameter backfill, archive streaming, the reproducibility rubric's re-read of a graded output log, and the operator verification pass, and by nothing else — an object none of those touch is never checked.*
- `record_integrity_observation` — `backend/app/services/artifact_integrity.py::record_integrity_observation`
  *Turns a detection into an append-only `artifact_integrity_event` row, in its own transaction so the record survives the request that discovered it. Carries expected-versus-observed digest and size plus the store's own `LastModified` / `ETag` / `ContentLength`, which is what lets an operator separate 'the object was modified after write' from 'we never stored what we said we did' from 'the store returned wrong bytes on this read'.*
- `_detect_calculation_hard_fail` — `backend/app/services/trust/evaluator.py::_detect_calculation_hard_fail`
  *Applies the label. Any calculation with a recorded break on any of its artifacts hard-fails, ahead of the geometry-validation verdict, and the existing `source_calculation_hard_failed_for_required_role` propagates that to every product naming the calculation as a required source. Reads database rows only — the trust rubric's `artifacts_present` deliberately does not verify bytes, so that a storage outage can never be reported as a depositor who failed to upload a log.*
- `ck_artifact_integrity_event_observed_digest_present_iff_read` (check on `artifact_integrity_event`)
  `(finding = 'object_missing' AND observed_sha256 IS NULL) OR (finding <> 'object_missing' AND observed_sha256 IS NOT NULL)`
- `ck_artifact_integrity_event_verified_requires_matching_digest` (check on `artifact_integrity_event`)
  `finding <> 'verified' OR observed_sha256 = sha256`

**Escape hatch.** Restore the object. There is no legitimate deposit this refuses — it refuses nothing — but a label that could never be cleared would be a trap, so the door is a later observation rather than an edit: the corrupt object is never deleted or overwritten by TCKDB, and re-uploading the original bytes under the same digest lets the verification pass record a `verified` observation that supersedes the break. A check constraint requires that row to carry a digest matching the key, so the hard fail is cleared by bytes that actually hash correctly and never by an operator asserting that they do. The break row stays as the account of what happened.

## Reproducibility

### 31. Whether a record's preserved evidence is sufficient to understand, audit or repeat it is assessed separately from how far its evidence is trusted and from whether a curator approved it, and the three may disagree.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0002, 0005 |

**Why this tier.** Not a check that fires but a position about what may never be collapsed into a single verdict. Reproducibility is graded under append-only, rubric-versioned assessments rather than as a field on a scientific row or an alias for review status, so a rubric can be revised without rewriting history and an old judgement stays interpretable. This is the same reasoning that made ADR 0005 record execution environments rather than grade them, and it is what lets the warning tiers elsewhere in this register be defensible: an incomplete record is accepted precisely because a separate layer exists to say how incomplete it is.

**Enforced at.**

- `reproducibility_assessment` rows carry `described` / `auditable` / `rerunnable` under a versioned rubric, append-only, independent of `record_review` and of the trust evaluator (`app/services/trust/`)

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

---

Declarations live beside the checks they describe; see `backend/app/scientific_checks/__init__.py` for how to add one, and `backend/app/scientific_checks/declarations.py` for the two populations that cannot self-declare (PostgreSQL objects, and wire schemas that are forbidden from importing the backend).
