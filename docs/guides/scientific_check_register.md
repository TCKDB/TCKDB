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
| `block` | 43 | Refuses the payload. ADR 0008 permits this only for a definition or a contract — a record no correct calculation could produce. |
| `warn` | 24 | Accepts the payload and records a machine-readable warning. The tier for expectations (which could fire on a correct novel result) and for absences (an incomplete record is still a true one). |
| `label` | 1 | Labels a stored record at read time without refusing anything — a `HardFailReason` in the trust evaluator. For facts TCKDB observes about a record after it was accepted, which no upload-time check could have refused because they did not exist yet. |
| `review` | 8 | Referred to `machine_review` under a versioned rubric. ADR 0008 puts every cross-check against external reference data here. |
| `structural` | 6 | Not an ADR 0008 consequence tier. The position is enforced by the shape of the schema, so a record violating it cannot be represented. |
| **total** | **82** | |

## Where a check's code reaches a client

| Channel | Entries | What a consumer can do with it |
| --- | --- | --- |
| `error_envelope` | 44 | the `code` field of the 422 error body — a client can branch on it |
| `upload_warning` | 25 | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| `trust_label` | 1 | a read-time trust label (`HardFailReason`), not any refusal |
| `database_constraint` | 2 | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |
| `none` | 10 | *nothing carries a code* |
| **total** | **82** | |

## Recorded divergences

Where a check's documentation and its behaviour disagree, or where a guarantee is narrower than its name suggests. Every one of these is reported, never silently fixed — the register changes no check behaviour.

- **[9]** The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.
- **[10]** Every geometry linked to a calculation is made of the atoms of the subject that calculation is filed under -- the species entry's own formula, or, for a transition state, the sum of its reaction's reactants -- and of the same isotopes of them.
- **[13]** An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.
- **[17]** A transition state's imaginary modes other than the reaction coordinate, and every imaginary mode on a species entry declared a minimum, are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.
- **[37]** A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

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

### 6. A reaction entry whose reactants and products are the same species entries carries evidence that something happens: a transition state or a kinetics record.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_entry_null_reaction` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0038, 0008 |

**Why this tier.** Definitional. A reaction that leaves every participant exactly as it was, with no saddle point and no rate, is not a reaction, so no correct calculation produces one. Two entries of one species (E and Z 2-butene) are different participants, and a degenerate exchange (H + H2) deposited with its transition state or rate is real chemistry; neither is refused.

**Enforced at.**

- `refuse_null_reaction_entry` — `backend/app/services/reaction_resolution.py::refuse_null_reaction_entry`
  *Called before `resolve_chem_reaction` on every path that creates a reaction entry (the reaction upload, which kinetics, networks and transition states reuse, and the computed-reaction bundle's canonical and kinetics reactions), told whether the deposit carries a transition state or kinetics.*

**Escape hatch.** Deposit the evidence with the reaction: a transition state (transition-state upload, or `transition_state` in a computed-reaction bundle) or a kinetics record (kinetics upload, or `kinetics` in a bundle). For a stereo isomerisation, deposit the two isomers as different entries (stereo marks in the SMILES, a stereo label, or a geometry per side).

## A structure against its own label

### 7. A deposit's `molecule_kind` agrees with the kind already stored for the species identity it resolves to — one identity is one kind of thing.

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

### 8. A conformer geometry deposited under a species entry is made of the atoms that entry's own SMILES declares.

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

### 9. The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.

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

### 10. Every geometry linked to a calculation is made of the atoms of the subject that calculation is filed under -- the species entry's own formula, or, for a transition state, the sum of its reaction's reactants -- and of the same isotopes of them.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `calculation_geometry_composition_mismatch`, `calculation_geometry_isotope_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A calculation's numbers are computed from its geometry and attributed to its subject; a geometry made of different atoms than the subject makes that attribution false rather than weak. No correct calculation can produce one, because no electronic-structure program adds or removes a nucleus -- which is also why this holds for output geometries and not only input ones: dissociation, isomerisation and proton transfer all conserve element counts.

**Enforced at.**

- `assert_calculation_geometry_composition` — `backend/app/services/calculation_geometry_composition.py::assert_calculation_geometry_composition`
  *Called from every site that attaches a geometry to a calculation: the `calculation_input_geometry` and `calculation_output_geometry` inserts, on both the producer-explicit branch and the `geometry_key`/fallback branch, and the geometry-bearing children -- a Hessian's geometry (upload and best-effort artifact hook), scan points and every IRC point (#680). A guard test fails if a construction is unchecked or a site disappears. `calc_isotopes.assert_isotopes` extends the claim to isotopes (own code, by count). The best-effort artifact hook is the one site that does not refuse: it never fails an upload, so a refusal there is the warning `hessian_not_stored_geometry_mismatch`, which carries the block code as `context.refusal_code` and in its sentence (#744, #840).*
- `assert_isotopes` — `backend/app/services/calc_isotopes.py::assert_isotopes`
  *The isotope half of the claim, with its own code. It counts the nuclide each atom of the geometry declares (`D`, `T` and `[2H]` are 2H and 3H, #672) and compares the count of each substituted element with the subject's; which atom carries the label is not checked. Called beside the composition check at every site that attaches a geometry to a calculation.*

**Escape hatch.** Declare the structure's real composition: give the calculation the geometry it was run on, or file it under the subject that geometry belongs to. Absence does not block -- a `pseudo` owner, a transition state whose reaction records no reactants or a pseudo reactant, and an unparseable stored SMILES are all left unjudged. Only atom counts are compared, so a scan or IRC point at stretched or rearranged coordinates passes -- scan and IRC points are themselves checked, and a point of a different molecule is refused -- as do constitutional isomers and dissociated optimisations. Isotopologues pass the element count; the isotope half (`calculation_geometry_isotope_mismatch`) judges them by the count of each substituted element. Declare the same substitution on the species and on the geometry, or file the calculation under the isotopologue it was run on.

**Recorded divergence.** A documented false *acceptance*, restated because a referee will ask: counts alone cannot tell a constitutional isomer from its partner, so an ethanol calculation carrying dimethyl ether coordinates passes. Catching that needs bond perception from XYZ, which fails silently on exactly the strained, radical and stretched structures where it would matter -- the same reasoning that keeps `species_geometry_composition_mismatch` to counts. It also refuses a geometry carrying ghost or dummy centres (counterpoise/BSSE), because `Bq` appears in no SMILES; that refusal is correct while `geometry_atom` has no way to say 'no nucleus here', since such a row is already miscounted by `natoms` and every degrees-of-freedom read downstream.

### 11. A species entry's declared charge equals the summed formal charge of its own SMILES.

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

### 12. The charge and spin multiplicity a depositor declares agree with the electronic-structure log of the calculation; the ground-or-excited state and term symbol they declare agree with what the calculation's declared method, multiplicity and statmech point group and linearity imply; and a term symbol agrees with the spin multiplicity declared beside it.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `charge_mismatch`, `multiplicity_mismatch`, `term_symbol_mismatch`, `electronic_state_contradicts_method`, `term_symbol_contradicts_multiplicity` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** **Placed against the ADR's own reasoning, deliberately.** ADR 0008 names both findings as direct contradictions between a declaration and the parsed evidence, therefore definitional, therefore belonging at the blocking tier — and then defers the promotion, because promoting a warning to a blocker rejects payloads that are accepted today. These checks have never fired on real data, so their false-positive rate is unknown and promoting them first would be unsafe. The register records the gap rather than hiding it: this is the clearest case in TCKDB of a check sitting one tier below where its own governing decision puts it. **The two deduction codes are warnings for a different reason.** Charge and multiplicity are read from the log, so a disagreement is a contradiction between a declaration and a parsed statement. The other two compare one declaration with another: no log is read; the method and multiplicity come from the payload itself and the point group and linearity from its statmech block. An EOM, CIS or spin-flip TDDFT method (the `EXCITED_STATE_METHODS` list) deduces an excited state definitively (that rule covers the `EXCITED_STATE_METHODS` names only); on this path job types are calculation types (opt, freq, sp ...) and say nothing, so plain TDDFT is not on the list and reads as the ground default, which is only a default for a single-reference method (confidence heuristic). Only the method names casscf, caspt2, nevpt2, mrci, dlpno-nevpt2 and dlpno-steom-ccsd, compared without regard to case (so CASSCF counts), deduce nothing. Any other name (for example MRCI+Davidson, or Molpro's RS2) is read as single-reference and gets the ground default, so a ground state declared for such a calculation can read as a contradiction. A term symbol is compared with the one derived from the multiplicity (and the point group and linearity where there are any), a derivation and not a reading. A heuristic default cannot block. **The two term-symbol codes are declaration against declaration too.** The leading digit of a term symbol is 2S+1, so `3P` beside `multiplicity=2` is a contradiction no correct deposit can contain, which the ADR would put at the blocking tier; it warns here for the reason above (a species-entry payload is shared by every upload route, and promoting a warning is a migration). A one-atom entry's term against the NIST ground term is entry 79's.

**Enforced at.**

- `reconcile_charge_multiplicity` — `backend/app/services/charge_multiplicity_reconciliation.py::reconcile_charge_multiplicity`
  *Re-reads charge and multiplicity from the uploaded artifact using the wired Gaussian, ORCA, Psi4 and Molpro parsers.*
- `term_symbol_warnings` — `backend/app/services/upload_reconciliation.py::term_symbol_warnings`
  *The term symbol against the multiplicity beside it (`term_symbol_contradicts_multiplicity`), read only from symbols that start with the multiplicity digit. No log is read.*
- `_reconcile_deduction` — `backend/app/services/upload_reconciliation.py::_reconcile_deduction`
  *The deduction path, for `term_symbol_mismatch` and `electronic_state_contradicts_method`: the declared term symbol and ground-or-excited state are compared with what `deduce_all` derives from the calculation's own declared method and spin multiplicity and, from the statmech block, the point group and linearity; no log is read. **Only the ground-state default is a heuristic**: an EOM, CIS or spin-flip TDDFT method (`EXCITED_STATE_METHODS`) deduces an excited state definitively, while any other method, plain TDDFT included, deduces the ground state by default; only the method names casscf, caspt2, nevpt2, mrci, dlpno-nevpt2 and dlpno-steom-ccsd, compared without regard to case (so CASSCF counts), deduce nothing; any other name (for example MRCI+Davidson, or Molpro's RS2) is read as single-reference, so a legitimately excited state computed another way reads as a contradiction. The deduction carries its own confidence and the evidence it rests on in the warning's context, and a stationary-point-kind deduction is not compared here at all (Layer 1's n_imag warnings own it).*

**Escape hatch.** Absence is not contradiction: if the producing program is not one of the wired parsers, the artifact is missing, the log is truncated, or the declarations inside a single log disagree with each other, the value is left unknown and **no** warning is emitted. Only a value genuinely read from the log may contradict a declaration — emitting a mismatch because parsing failed would fabricate a contradiction out of ignorance.

### 13. An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008, 0002 |

**Why this tier.** An expectation, and correctly non-blocking. An optimisation that rearranged, dissociated or transferred a proton is science to record, not a payload to refuse, and connectivity perception from XYZ is unreliable for exactly the weak complexes, radicals, ions and stretched geometries where a genuine rearrangement would matter. The result is written as an evidence row that grades the record at read time; it never refuses an upload. Since #143 the tier is also the only one left to it: the *composition* half of what this row reports is owned by `calculation_geometry_composition_mismatch`, which blocks, so what this row can still say on its own is the RMSD suspicion — a correct-formula structure that moved further than expected, which is an expectation by construction. **Evidence-only by decision, with no deposit warning.** Its read path is the trust rubric's geometry-validation check (`_check_geometry_validation_passed_or_warning` in `app/services/trust/rubrics.py`): a `fail` or `warning` row grades the record as a zero-weight warning, visible at read time. No upload warning is emitted because the only live verdict is the RMSD above 1.0 A, and that would fire routinely on flexible molecules optimised from rough starting structures, where a large move is the optimisation doing its job; a warning that fires on correct deposits teaches depositors to ignore warnings.

**Enforced at.**

- `validate_calculation_geometry` — `backend/app/services/geometry_validation.py::validate_calculation_geometry`
  *Species-owned `opt` calculations only. Transition states are deliberately excluded, having no canonical SMILES to compare against. Best-effort by policy: a missing SMILES, a missing output geometry, unparseable coordinates or a raising chemistry layer all write nothing and let the upload continue. A Kabsch RMSD above 1.0 A against the input geometry is recorded as a separate suspicion signal. Its `fail` outcome is no longer reachable through an upload path: `is_isomorphic=False` fires only on an element-count mismatch, no calculation can change an element count, and `assert_calculation_geometry_composition` refuses such a deposit before this runs. The pure seam keeps the verdict and is pinned directly by `tests/workflows/test_geometry_validation_wiring.py`.*

**Escape hatch.** The whole check is advisory, so there is nothing to escape. What a consumer must not do is read a `fail` row as 'this calculation is scientifically invalid'; it means only that the automated identity validator found a mismatch.

**Recorded divergence.** The stored column is named `is_isomorphic` and the surrounding policy is worded as graph isomorphism, but the code tests the **molecular formula** only. Atom mapping falls back to a SMILES-graph matcher whenever bond perception from XYZ fails, which is the common case for the radicals, ions and stretched geometries this service mostly sees, and that fallback rejects a candidate on one condition: the per-element atom counts disagree. Verified by direct call in the module docstring — ethanol declared with dimethyl ether deposited passes, and methane with one hydrogen pulled to 5 A passes. So the rearrangement, bond-breaking, dissociation and proton-transfer cases the module was written to catch are not caught. Already self-documented in the module docstring rather than discovered here; recorded because the field name is what a consumer sees and it still overstates the guarantee. **Partly closed:** the read surfaces now publish the same boolean under its true name, `formula_matches`, and the stored `validation_reason` no longer says 'not graph-isomorphic'. `is_isomorphic` is kept beside it because it is the stored column and a published field — renaming it is a migration against a deployed table and a breaking API change, and buys nothing that publishing the true name alongside does not. What stays open is the check itself: connectivity is still not tested, and cannot be until there is bond perception trustworthy on the strained and radical structures where a rearrangement would matter.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

## Stationary points

### 14. A transition state has at least one imaginary vibrational mode, exactly one of them is designated the reaction coordinate, and no undeclared mode is stiff enough to make that designation meaningless.

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

### 15. A species entry declared a minimum has no imaginary vibrational mode at or above the tolerance of the protocol that produced it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `n_imag_contradicts_minimum` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** Definitional above the protocol's tolerance. A covalently bound minimum whose own frequency evidence reports an imaginary mode at or above tau is mislabelled: at that protocol the negative curvature is real, so the correct response is to re-optimise or to declare the entry as something else, and refusing the deposit rejects no correct calculation. Below tau the protocol cannot tell the sign of the curvature from zero, so refusing would reject correct published calculations; that case is the warning declared next, not a refusal.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`
  *One imaginary mode and two-or-more are folded into a single blocking message that names `n_imag_higher_order_saddle` for the higher-order case. The refusal stands when any deposited mode is at or above tau, and when the magnitudes of the modes were not deposited (a mode nobody measured is never assumed soft). The same code also refuses a reaction-coordinate designation on a minimum, because a minimum has no reaction coordinate. A stationary-point kind the module has not been taught about produces no findings — adding an enum member must be a deliberate decision, not a default.*

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, not in omega, so the uncertainty in a frequency diverges as omega goes to zero: at 300 cm-1 the sign is never in doubt, at 20 cm-1 it is indeterminate. Where the crossover sits depends on how the second derivatives were built, on what integration grid, and how tightly the geometry was converged. The same -42 cm-1 is real negative curvature under an analytic Hessian on a tight grid and indistinguishable from zero under a numerical one on a default grid, so it cannot be classified from the frequency list at all — only against the protocol that produced it. A constant here would be a claim about physics that is false.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is resolved in a fixed order (`resolve_tau_with_assumption`, since #864): (1) the method an attached output log reports the frequency job ran; else (2) the method the input echoed in that log requests (what the job read outranks what was recorded about it); else (3) the recorded `freq.hessian_method` or the method the calculation's own input deck states; else (4) the producing program's assumed default for the level of theory's method family; else (5) 50 cm-1. A reported method outranks a recorded one because it is what ran; a request only says what was asked for, and the input a log echoes is what the job actually read, so it outranks a method recorded or typed about the job. When no log reports a method, no echoed input requests one and nothing is recorded or stated in a deck — which is the common case, because most outputs do not say — validation resolves tau from the producing program's documented default for the level of theory's method family (ADR 0012's 2026-09-04 amendment, applied at validation since 2026-10-31; the table is `tckdb_schemas.hessian_method_assumption`). Before assuming anything, validation reads the frequency directive of an inline input deck attached to the calculation (Gaussian `Freq=Numer` / `EnOnly`, ORCA `NumFreq` / `AnFreq`, where `NumFreq` with `NumGrad` is a Hessian from energies): a method the deposit states beats an assumed one (a recorded value that contradicts the deck is its own refusal, `hessian_method_contradicts_input_deck`, declared in register entry 50). A deck that carries no such directive says nothing. Failing that, Gaussian HF, DFT (double-hybrid functionals excepted: only B2PLYP, B2PLYPD, B2PLYPD3, mPW2PLYP and mPW2PLYPD, which the Gaussian manual documents as analytic, are assumed; nothing is assumed for the others), MP2, CIS and CASSCF, and ORCA HF and DFT, resolve to the analytic default (30 cm-1). ORCA releases before version 5 have no analytic Hessian for meta-GGA functionals and are assumed numerical (50 cm-1); ORCA double-hybrid and VV10 (non-local) functionals are assumed numerical on any version, because the manuals document numerical frequencies for them; an ORCA meta-GGA with an unknown version gets no assumption. Gaussian MP3, MP4(DQ), MP4(SDQ), QCISD, CCSD, CISD and CID, and ORCA MP2 (closed-shell DLPNO-MP2 included) and, from ORCA 6, the `AUTOCI-` CCSD, CCSD(T), MP2, MP3 and MP4 methods when the level of theory states no frozen core and no RI (otherwise nothing is assumed for them), resolve to finite differences of gradients (50 cm-1). Gaussian CCSD(T), QCISD(T), MP4(SDTQ) (the bare MP4 keyword) and MP5, and ORCA CCSD, CCSD(T), their F12 variants (F12-MP2 included), every DLPNO coupled-cluster method, and open-shell DLPNO-MP2 and ROHF MP2 (read from the stated spin treatment or a UHF/ROHF prefix), which have no analytic gradient, resolve to finite differences of energies (80 cm-1), which is looser than the conservative row: a minimum at 50 to 80 cm-1 that was refused without a recorded method is now accepted with a warning, and a transition-state extra mode in that band is no longer flagged. Other DLPNO methods (DLPNO-CEPA and the like) assume nothing. The record stores the matching `assumed_*` basis so an assumption is never confusable with a recorded statement. An assumption never replaces a statement (a recorded finite difference of gradients on an ORCA `NumFreq` deck for a method with no analytic gradient is refined to finite differences of energies and `hessian_method_refined` says so): a `freq.hessian_method` that is present but not recognised is kept at the conservative row rather than replaced by an assumption. Only where the software or the method family is not in that table, or the payload names no software, is tau 50 cm-1 with `protocol_not_recorded` as the basis; that fallback is deliberately the *conservative* row, because assuming the better case would flag genuine quadrature noise as a real higher-order saddle. For a transition state tau never decides between blocking and warning — every blocking rule there is a contract about what the record says, not about magnitude — so the resolution changes how loudly a record is flagged and never whether it is accepted. For a declared minimum tau is the line between the warning and the refusal, and it is the same tau that is stored with the record.

**Escape hatch.** Declare `species_entry_kind='vdw_complex'`, which records the same mode with a warning instead, or deposit the structure through a transition-state payload if the single imaginary mode is real. A mode below tau needs no escape: it is accepted with a warning.

### 16. A van der Waals complex is formally a minimum, so an imaginary mode on one is recorded and flagged rather than refused — unless the mode is too stiff to be an intermolecular one, which suggests a genuine reaction coordinate.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `n_imag_contradicts_minimum`, `n_imag_higher_order_saddle`, `n_imag_suggests_transition_state` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** The carve-out is the scientific content. A van der Waals complex is held together by intermolecular forces and its stretch, bends and hindered internal rotations sit below roughly 50 cm-1 — the region where numerical noise in a finite-difference or quadrature-grid Hessian is comparable to the true curvature. A small imaginary mode there is usually a grid artifact, so refusing it would force an expensive re-run for a physically meaningless mode. This is what earns `vdw_complex` a separate enum member: it is the only place the two minimum kinds behave differently.

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`
  *Same entry point as the blocking minimum rule; the declared kind decides the tier while the code names the finding. A mode at or above 100 cm-1 additionally raises `n_imag_suggests_transition_state`, because that is far too stiff to be intermolecular.*

**Escape hatch.** This *is* the escape hatch for the blocking minimum rule. Its own cost is that a genuinely mislabelled saddle point deposited as a van der Waals complex is accepted with a warning.

### 17. A transition state's imaginary modes other than the reaction coordinate, and every imaginary mode on a species entry declared a minimum, are judged by magnitude against a tolerance read from the protocol that produced them, not by counting them.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `minimum_imaginary_modes_below_tau`, `transition_state_extra_imaginary_modes_below_tau`, `transition_state_extra_imaginary_mode_above_tau`, `transition_state_extra_imaginary_modes_not_assessable` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0012 |

**Why this tier.** The extra modes cannot be classified from the frequency list, so refusing the deposit would assert a determination the deposit does not contain the information to support — an expectation about numerical quality wearing the costume of a definition. They can also be simply correct: a harmonic model is inapplicable to a torsion, a ring pucker or an intermolecular mode in a loose complex, and a transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck, in which case the extra negative eigenvalue is not an artefact but exactly right. Warning rather than blocking is also the only choice that preserves evidence: a record carrying all three frequencies and the full protocol can be reassessed under a better rule in five years, while a refused deposit leaves nothing behind.

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
  *Above tau the record is flagged as well as warned: the flag is ADR 0012's answer to 'warnings get ignored', excluding the record from default transition-state consumption without creating the incentive to edit the frequency list that a hard block creates. Below tau it is warned only. The flag and the tau that decided it are persisted on `calc_freq_result` rather than recomputed, so a later parser improvement cannot silently re-label historical records.*
- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`
  *The minimum case of the same judgement, added 2026-10-29. When every imaginary mode of a declared minimum is below tau the record is accepted with `minimum_imaginary_modes_below_tau`; at or above tau the blocking minimum rule above still refuses it. The tau that decided it is persisted on `calc_freq_result` (`imaginary_mode_tau_cm1` and its basis) with the structural flag stored as false, meaning judged and not flagged, rather than recomputed later. The read-time imaginary-mode projections report beside the warning and never gate it.*

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, not in omega, so the uncertainty in a frequency diverges as omega goes to zero: at 300 cm-1 the sign is never in doubt, at 20 cm-1 it is indeterminate. Where the crossover sits depends on how the second derivatives were built, on what integration grid, and how tightly the geometry was converged. The same -42 cm-1 is real negative curvature under an analytic Hessian on a tight grid and indistinguishable from zero under a numerical one on a default grid, so it cannot be classified from the frequency list at all — only against the protocol that produced it. A constant here would be a claim about physics that is false.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is resolved in a fixed order (`resolve_tau_with_assumption`, since #864): (1) the method an attached output log reports the frequency job ran; else (2) the method the input echoed in that log requests (what the job read outranks what was recorded about it); else (3) the recorded `freq.hessian_method` or the method the calculation's own input deck states; else (4) the producing program's assumed default for the level of theory's method family; else (5) 50 cm-1. A reported method outranks a recorded one because it is what ran; a request only says what was asked for, and the input a log echoes is what the job actually read, so it outranks a method recorded or typed about the job. When no log reports a method, no echoed input requests one and nothing is recorded or stated in a deck — which is the common case, because most outputs do not say — validation resolves tau from the producing program's documented default for the level of theory's method family (ADR 0012's 2026-09-04 amendment, applied at validation since 2026-10-31; the table is `tckdb_schemas.hessian_method_assumption`). Before assuming anything, validation reads the frequency directive of an inline input deck attached to the calculation (Gaussian `Freq=Numer` / `EnOnly`, ORCA `NumFreq` / `AnFreq`, where `NumFreq` with `NumGrad` is a Hessian from energies): a method the deposit states beats an assumed one (a recorded value that contradicts the deck is its own refusal, `hessian_method_contradicts_input_deck`, declared in register entry 50). A deck that carries no such directive says nothing. Failing that, Gaussian HF, DFT (double-hybrid functionals excepted: only B2PLYP, B2PLYPD, B2PLYPD3, mPW2PLYP and mPW2PLYPD, which the Gaussian manual documents as analytic, are assumed; nothing is assumed for the others), MP2, CIS and CASSCF, and ORCA HF and DFT, resolve to the analytic default (30 cm-1). ORCA releases before version 5 have no analytic Hessian for meta-GGA functionals and are assumed numerical (50 cm-1); ORCA double-hybrid and VV10 (non-local) functionals are assumed numerical on any version, because the manuals document numerical frequencies for them; an ORCA meta-GGA with an unknown version gets no assumption. Gaussian MP3, MP4(DQ), MP4(SDQ), QCISD, CCSD, CISD and CID, and ORCA MP2 (closed-shell DLPNO-MP2 included) and, from ORCA 6, the `AUTOCI-` CCSD, CCSD(T), MP2, MP3 and MP4 methods when the level of theory states no frozen core and no RI (otherwise nothing is assumed for them), resolve to finite differences of gradients (50 cm-1). Gaussian CCSD(T), QCISD(T), MP4(SDTQ) (the bare MP4 keyword) and MP5, and ORCA CCSD, CCSD(T), their F12 variants (F12-MP2 included), every DLPNO coupled-cluster method, and open-shell DLPNO-MP2 and ROHF MP2 (read from the stated spin treatment or a UHF/ROHF prefix), which have no analytic gradient, resolve to finite differences of energies (80 cm-1), which is looser than the conservative row: a minimum at 50 to 80 cm-1 that was refused without a recorded method is now accepted with a warning, and a transition-state extra mode in that band is no longer flagged. Other DLPNO methods (DLPNO-CEPA and the like) assume nothing. The record stores the matching `assumed_*` basis so an assumption is never confusable with a recorded statement. An assumption never replaces a statement (a recorded finite difference of gradients on an ORCA `NumFreq` deck for a method with no analytic gradient is refined to finite differences of energies and `hessian_method_refined` says so): a `freq.hessian_method` that is present but not recognised is kept at the conservative row rather than replaced by an assumption. Only where the software or the method family is not in that table, or the payload names no software, is tau 50 cm-1 with `protocol_not_recorded` as the basis; that fallback is deliberately the *conservative* row, because assuming the better case would flag genuine quadrature noise as a real higher-order saddle. For a transition state tau never decides between blocking and warning — every blocking rule there is a contract about what the record says, not about magnitude — so the resolution changes how loudly a record is flagged and never whether it is accepted. For a declared minimum tau is the line between the warning and the refusal, and it is the same tau that is stored with the record.

**Escape hatch.** None is needed — the check never refuses. Its cost runs the other way: a genuine higher-order saddle, a torsional maximum and a valley-ridge inflection are all accepted, and only the structural flag separates them from a clean first-order saddle for a consumer who reads it.

**Recorded divergence.** ADR 0012 recommends replacing the threshold with a determination — projecting each imaginary eigenvector onto the six rigid-body vectors (more than about 90 percent overlap means projection residue) and onto dihedral-rotation vectors (more than about 70 percent means a torsion) — and says those should be implemented *before* tau is tuned, because a determination beats a threshold wherever one is available. Tau shipped first, so that ordering was not followed; the projections themselves now exist. They are computed at *read* time from `calc_hessian` -- whose eigenvectors, mass-weighted by the per-atom masses `geometry_atom` carries, are the displacement vectors ADR 0012 wanted -- and nothing about them is stored, so no schema change was involved and no historical record was re-decided. See `include=imaginary_mode_projections` on the scientific calculation read, and ADR 0013 as amended. The disposition on each extra mode remains *declared by the depositor*; the determination is reported beside it and a disagreement is surfaced as one, never silently resolved. Under ADR 0008 a projection is an expectation about a record, so it informs this check and does not gate it. Where no Hessian is stored the projection reads 'not determinable', which is not the same answer as 'no residue found' — and is not a shrug either: that block carries this check's own stored judgement beside the refusal, being the tau applied, the row of the protocol table it came from, the recorded parameters that chose that row, the structural flag, and every imaginary mode ranked by magnitude against the tau. It reports the comparison and stops there. Magnitude cannot separate a spurious mode from a real one that is not the reaction coordinate — a transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck — so no mode in that block is labelled, and the only assignment on it is the one the depositor declared.

### 18. A transition state's reaction coordinate should exceed roughly 100 cm-1 in magnitude.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_imaginary_frequency_too_small` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
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
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is resolved in a fixed order (`resolve_tau_with_assumption`, since #864): (1) the method an attached output log reports the frequency job ran; else (2) the method the input echoed in that log requests (what the job read outranks what was recorded about it); else (3) the recorded `freq.hessian_method` or the method the calculation's own input deck states; else (4) the producing program's assumed default for the level of theory's method family; else (5) 50 cm-1. A reported method outranks a recorded one because it is what ran; a request only says what was asked for, and the input a log echoes is what the job actually read, so it outranks a method recorded or typed about the job. When no log reports a method, no echoed input requests one and nothing is recorded or stated in a deck — which is the common case, because most outputs do not say — validation resolves tau from the producing program's documented default for the level of theory's method family (ADR 0012's 2026-09-04 amendment, applied at validation since 2026-10-31; the table is `tckdb_schemas.hessian_method_assumption`). Before assuming anything, validation reads the frequency directive of an inline input deck attached to the calculation (Gaussian `Freq=Numer` / `EnOnly`, ORCA `NumFreq` / `AnFreq`, where `NumFreq` with `NumGrad` is a Hessian from energies): a method the deposit states beats an assumed one (a recorded value that contradicts the deck is its own refusal, `hessian_method_contradicts_input_deck`, declared in register entry 50). A deck that carries no such directive says nothing. Failing that, Gaussian HF, DFT (double-hybrid functionals excepted: only B2PLYP, B2PLYPD, B2PLYPD3, mPW2PLYP and mPW2PLYPD, which the Gaussian manual documents as analytic, are assumed; nothing is assumed for the others), MP2, CIS and CASSCF, and ORCA HF and DFT, resolve to the analytic default (30 cm-1). ORCA releases before version 5 have no analytic Hessian for meta-GGA functionals and are assumed numerical (50 cm-1); ORCA double-hybrid and VV10 (non-local) functionals are assumed numerical on any version, because the manuals document numerical frequencies for them; an ORCA meta-GGA with an unknown version gets no assumption. Gaussian MP3, MP4(DQ), MP4(SDQ), QCISD, CCSD, CISD and CID, and ORCA MP2 (closed-shell DLPNO-MP2 included) and, from ORCA 6, the `AUTOCI-` CCSD, CCSD(T), MP2, MP3 and MP4 methods when the level of theory states no frozen core and no RI (otherwise nothing is assumed for them), resolve to finite differences of gradients (50 cm-1). Gaussian CCSD(T), QCISD(T), MP4(SDTQ) (the bare MP4 keyword) and MP5, and ORCA CCSD, CCSD(T), their F12 variants (F12-MP2 included), every DLPNO coupled-cluster method, and open-shell DLPNO-MP2 and ROHF MP2 (read from the stated spin treatment or a UHF/ROHF prefix), which have no analytic gradient, resolve to finite differences of energies (80 cm-1), which is looser than the conservative row: a minimum at 50 to 80 cm-1 that was refused without a recorded method is now accepted with a warning, and a transition-state extra mode in that band is no longer flagged. Other DLPNO methods (DLPNO-CEPA and the like) assume nothing. The record stores the matching `assumed_*` basis so an assumption is never confusable with a recorded statement. An assumption never replaces a statement (a recorded finite difference of gradients on an ORCA `NumFreq` deck for a method with no analytic gradient is refined to finite differences of energies and `hessian_method_refined` says so): a `freq.hessian_method` that is present but not recognised is kept at the conservative row rather than replaced by an assumption. Only where the software or the method family is not in that table, or the payload names no software, is tau 50 cm-1 with `protocol_not_recorded` as the basis; that fallback is deliberately the *conservative* row, because assuming the better case would flag genuine quadrature noise as a real higher-order saddle. For a transition state tau never decides between blocking and warning — every blocking rule there is a contract about what the record says, not about magnitude — so the resolution changes how loudly a record is flagged and never whether it is accepted. For a declared minimum tau is the line between the warning and the refusal, and it is the same tau that is stored with the record.

**Escape hatch.** None is needed — the check never refuses. A referee should read the threshold as a tunable reporting line, not a claim about physics.

### 19. A deposited saddle point should carry passing intrinsic-reaction-coordinate evidence that it connects the declared reactants and products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_missing_irc_evidence` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** Absence, not contradiction. Refusing a transition state without an IRC would lose the saddle point entirely, and a saddle point with no IRC is an incomplete record rather than a false one. The evidence is recommended, not required.

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
  *Every path that can carry a transition state routes through this seam — the PDep bundle, the computed-reaction bundle and the standalone transition-state upload — so all three write identical rows and report an identical gap. Before the seam existed only the PDep bundle could deposit the evidence, so a TS uploaded any other way always read back as `irc: absent` even when the depositor had run one.*

**Escape hatch.** None needed — the warning is the accommodation. Note the warning fires on absence of a *passing* `irc` record, so an IRC that was run and failed is stored and still warns, and so does a deposit whose only evidence is an energy ordering or an imaginary mode: neither shows the saddle point connects the declared endpoints.

### 20. A frequency result that deposits both a scalar imaginary-mode count and a frequency list agrees with itself about how many imaginary modes it has.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `freq_n_imag_disagrees_with_modes` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** A contract between two fields of one record, not an expectation about a result — which is the narrow thing ADR 0008 reserves the blocking tier for. Every other entry in this group judges chemistry and had to argue that no correct calculation could produce the record it refuses; this one does not need that argument, because it makes no claim about the potential energy surface at all. It says only that `calc_freq_result.n_imag` and the `calc_freq_mode` rows beside it must answer the same question the same way. A deposit that says three and shows one is accepted today, after which the cheap summary and the evidence table disagree permanently and neither reader is told the other exists — the summary-reading consumer filters it as a higher-order saddle, the mode-reading consumer sees a clean first-order one, and both are quoting the same row. No re-run and no better parser can decide which was meant, because the payload itself no longer knows.

**Enforced at.**

- `FreqResultPayload.validate_modes_consistency` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py::FreqResultPayload.validate_modes_consistency`
  ***Absence is deliberately not disagreement.** The rule engages only where a frequency list was deposited: `modes = null` with any `n_imag` is accepted, because an incomplete record is still a true record and ADR 0012's read surface already reports `n_imag_at_or_above_tau = null` rather than `0` for exactly that state. An *empty* list is a different claim — the depositor handing over a frequency list with nothing imaginary in it — and is judged like any other list, which is why `n_imag = 3` with `modes = []` is refused while `n_imag = 3` with `modes = null` is not. **Not also held in PostgreSQL, and that is a limit rather than a choice.** The rule counts rows in `calc_freq_mode` and compares the total against a column of `calc_freq_result`, which is a cross-table aggregate: a CHECK constraint may not contain a subquery, so no constraint expresses it and only a deferred constraint trigger could. That was not added, for the reason already written on `calc_freq_result`'s tau-basis CHECK — a trigger does not fire under `session_replication_role = replica`, which is what bulk loaders and restore paths run under, so it would be absent from precisely the write paths that do not reach this validator. A second guard that holds only where the first one already holds is not depth.*

**Escape hatch.** Omit `modes`. A record that deposits only the scalar is incomplete, warns nowhere and is refused nowhere — ADR 0012 asks for the complete signed frequency list, and asking is as far as this tier goes. What has no door is depositing both and letting them contradict each other.

### 21. A frequency list carries no more modes than the geometry it is attached to has degrees of freedom: at most `3N` for `N` atoms, the six rigid-body modes included.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `freq_list_exceeds_geometry_degrees_of_freedom` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0012 |

**Why this tier.** Definitional, and — unusually in this group — arithmetically so. `3N` is the dimension of the nuclear coordinate space, so it is the total number of eigenvalues the mass-weighted Hessian of an `N`-atom geometry has: not the vibrational count, which is `3N - 6` or `3N - 5`, but every Cartesian degree of freedom including the six translations and rotations ADR 0012 asks a record to carry. A longer list is therefore not a short spectrum — it is not a spectrum of that geometry at all, under any harmonic treatment, integration grid, coordinate system or level of theory. That is what separates it from the `n_imag == 1` gate ADR 0012 retired: there, two scientifically correct calculations of one structure legitimately returned different answers and the tier was an expectation about numerical quality wearing the costume of a definition. Here no protocol takes part, so no correct deposit is refused. The contrast with the *floor* is the argument, and the floor is correctly a warning for precisely the reasons this is not. First, the ambiguity a short list carries is one-sided: a partial-Hessian job, a frozen-atom or ONIOM Hessian and a lumped `pseudo` participant all *remove* modes and are honest records of what was computed, TCKDB carries no field saying which of them a short list is, and nothing whatsoever filters modes *in*. Second, `modes = null` is accepted and must stay accepted, so the cheapest way past a completeness *block* on the floor would be deleting the frequency list — converting visible ambiguity into invisible falsehood, which ADR 0012 §"Why not refuse, when refusing is cheaper" names as worse than no rule. Neither reaches the ceiling. Deleting an over-long list loses a spectrum the check has already found is not this geometry's, which is the repair rather than the evasion; and accepting it is the destructive option, because stored it reads as this geometry's spectrum and a consumer recomputing a partition function from the pair gets a number rather than an error. No threshold is declared because there is none to declare: `3N` is counted from the deposited geometry, not tuned.

**Enforced at.**

- `evaluate_frequency_list_completeness` — `schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py::evaluate_frequency_list_completeness`
  *Owns the arithmetic and knows no payload shapes; the geometry each frequency list is measured against is resolved by `evaluate_deposited_frequency_list` in the same module — the calculation's own first input geometry where the producer named one, and otherwise the enclosing conformer's or transition state's reference geometry, which is the fallback the persistence workflow itself applies for `freq`. Written once so the payload shapes that call it cannot drift into counting three different sets of atoms. **The same function also reports the floor**, `freq_list_incomplete_for_geometry`, at the warning tier and with a structural flag; that finding is not this entry because it is an expectation rather than a definition, for the reasons set out above. It is registered under entry 22, beside the linearity codes, which make the same kind of claim about the same list. This entry is deliberately about one of the two codes the function emits.*
- `raise_for_blocking_findings` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::raise_for_blocking_findings`
  *Where the refusal actually happens, and named because the deciding module cannot do it. `tckdb_schemas.frequency_completeness` returns findings and never raises, so the code reaches the 422 envelope only through this shared raiser, which every published upload request model calls from a `model_validator` before any route body opens a submission. It reports whichever code its blocking findings agreed on — a payload carrying two different contradictions falls back to the generic code and names both in the message, because naming either one would tell the client the other is not there.*

**Escape hatch.** **None, and that absence is the argument for blocking rather than a gap in it.** Every other entry in this group needs a door because a correct record can trip it: a genuine higher-order saddle is deposited by designating its reaction coordinate, a van der Waals complex by declaring `molecule_kind`, a contradictory `n_imag` by omitting `modes`. There is no counterpart here because there is nothing to let through — no calculation, honest or otherwise, produces more modes than the geometry has degrees of freedom, so a hatch would exist only to admit a record whose own two halves disagree. What looks like a hatch and is not: omitting `modes` still avoids the refusal, but it is not a door onto legitimate chemistry the check would have refused — it deposits a different, weaker record, and the thing it drops is a frequency list this check has already determined does not belong to the geometry beside it. The repair is to attach the calculation to the geometry it ran on, or to deposit that geometry.

### 22. A frequency list's mode count should match the shape of the geometry it is attached to: `3N - 5` vibrations for a collinear molecule and `3N - 6` for a non-linear one, so a list carrying one count on a geometry of the other shape is one mode wrong in whichever direction it leans.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `freq_list_linear_mode_count_for_bent_geometry`, `freq_list_bent_mode_count_for_linear_geometry`, `freq_list_incomplete_for_geometry` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An expectation, not a definition, and ADR 0008 is explicit that only the latter may block. The verdict rests on a *tolerance* — on where the line between collinear and bent is drawn — and a rule whose answer moves when a constant moves is an expectation by construction. Unlike the `3N` ceiling, which no correct deposit can trip, both of these can: a genuinely quasi-linear molecule sits on no side of the line; a producer who deposited `3N - 6` vibrations plus one rigid-body eigenvalue on purpose deposited something true; and a partial or frozen-atom Hessian on a linear molecule produces `3N - 6` modes honestly. So the check declines to answer in the ambiguous band and annotates rather than refuses outside it. It is also the *second* judgement on one list: the wire package's `freq_list_incomplete_for_geometry` floor keeps exactly the strength an atom count can prove, and this adds only the strength coordinates can. That is also why the short-list direction is a code of its own rather than an extension of that floor — `3N - 6` *is* the floor's threshold and it warns strictly below, so the wire package cannot reach this case at all, and emitting its code from here would put a coordinate-dependent trigger behind a string whose contract is that an atom count proves it.

**Enforced at.**

- `evaluate_frequency_list_completeness` — `schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py::evaluate_frequency_list_completeness`
  *The floor, `freq_list_incomplete_for_geometry`: fewer modes than an atom count alone proves a harmonic analysis of the geometry produces (below `3N - 6`, with a structural flag for the unambiguous case). It is the same claim as the two linearity codes, the list's mode count against the geometry's shape, at the strength an atom count can prove. The ceiling (entry 21) is a different claim: `3N` is a definition, the floor is an expectation. Partial, frozen-atom and ONIOM Hessians remove modes honestly, which is why the floor warns and never refuses.*
- `evaluate_frequency_list_linearity` — `backend/app/services/frequency_geometry_linearity.py::evaluate_frequency_list_linearity`
  *Owns the arithmetic and the verdict; the payload walks that resolve which geometry each frequency list is measured against live in the same module, one per upload shape, and restate the wire-side rule — the calculation's own first input geometry where the producer named one, otherwise the enclosing conformer's or transition state's reference geometry. The two checks must count the same atoms to be talking about the same record.*
- The collinearity tolerance stays backend-side. `tckdb_schemas` is forbidden from importing `app` by an AST scan and a runtime `sys.modules` check, and it carries no numerics; moving a tolerance into it to share one constant would put a numerical judgement inside a package whose contract is that it holds none. So the wire package keeps the bound it can prove from an atom count and the backend adds the sharper one where the coordinates are.
- `LINEARITY_SINGULAR_VALUE_TOLERANCE` in `app.chemistry.normal_modes` is *not* reused, though it answers the same question. It is an exact-rank test at 1e-6 relative, set that tight because admitting a nearly-zero direction into a projection basis corrupts the projection. Measured on real coordinates, CO2 bent by one thousandth of a degree — ordinary optimiser noise — comes out at 4.5e-6 and classifies as *not linear*, so reusing it would have fired this warning on correct linear molecules.

**Thresholds.**

- `COLLINEAR_MAX_TRANSVERSE_RATIO` = **0.001 dimensionless (sigma_2 / sigma_1 of the centred coordinates)** — a constant fixed in code.
  At or below this the geometry is treated as linear and nothing is reported. Roughly 0.2 degrees of bend for a symmetric triatomic — three orders of magnitude looser than optimiser convergence noise, so a real linear molecule cannot be flagged. Loosening it would silence the check for genuinely bent molecules; tightening it risks warning about correct ones, which is the failure an advisory check must not have.
- `BENT_MIN_TRANSVERSE_RATIO` = **0.03 dimensionless (sigma_2 / sigma_1 of the centred coordinates)** — a constant fixed in code.
  At or above this the geometry is treated as bent and the warning may fire. Roughly 6 degrees of bend for a symmetric triatomic; water sits fifteen times over it. Between the two thresholds the check answers 'undetermined' and says nothing, which is what keeps a quasi-linear molecule from receiving a confident claim two degrees cannot support.

**Escape hatch.** None needed — the warning is the accommodation, and the record is stored either way. A depositor whose molecule is genuinely quasi-linear will usually not see the warning at all, because the band between the two thresholds is deliberately silent.

### 23. The energies an energy-ordering record compares should be taken at one level of theory per energy kind.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_energy_ordering_mixed_levels` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An expectation, not a definition. An ordering across levels is usually a mistake, but a deliberate one (a literature well against a computed saddle point) is a legitimate record, so refusing it would lose correct science; the warning names the gap instead (ADR 0008).

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
  *Runs in the shared evidence seam, where each energy's source calculation has already been resolved, so all three deposit paths report it alike.*

**Escape hatch.** None needed: the warning is the accommodation. Take every energy of a kind at one level, or accept the warning.

### 24. A stated energy should be the energy TCKDB stores for the calculation(s) it cites. An energy-ordering energy is the single point's (or optimisation's) energy, or for an E0 the paired electronic energy plus the cited frequency's ZPE, scaled by the stated `zpe_scale_factor`; an E0 with no stated factor is never refused. A network state energy that cites one calculation per participant is the stoichiometric sum of their stored energies (on a zero shared by the solve's states, the difference between states).

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `ts_energy_ordering_stated_energy_mismatch`, `network_state_energy_sum_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The record names a calculation and a number; a number that is not that calculation's stored one is a factual inconsistency between two things the same deposit asserts, not an expectation. Without it the ordering was checked against the depositor's own numbers only, and a mistyped or copied value could make a record pass that the stored energies fail. The tolerance is the shared printed-precision one, so printed rounding is never refused; a network state energy stated in kJ/mol is further allowed half a rounding unit (stated, else 1 kcal/mol) plus the spread of hartree-to-kJ/mol constants, and a value inside that allowance but beyond printed precision is stored as not compared, never refused. A comparison that cannot be made is not a contradiction and does not block (`CHECK_TS_ENERGY_ORDERING_NOT_COMPARED`).

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
  *Runs in the shared evidence seam, so the PDep bundle, the computed-reaction bundle and the standalone upload enforce it alike, and it holds for a payload that bypassed the wire schemas. Wire-level checks cannot do it: the stored energies are in the database.*
- `compare_state_energy_sums` — `backend/app/services/network_energy_sources.py::compare_state_energy_sums`
  *The network route: reads the stored energies off the persisted calculations, so it holds for a payload that bypassed the wire schema. Run once per solve, after every state energy is resolved, because the shared-zero conventions compare states with each other and blame the outlier by majority.*

**Escape hatch.** State the stored energy, or cite the calculation the number came from. An E0 built with a scaled ZPE states `zpe_scale_factor`. A network state energy states unrounded kJ/mol derived from hartree (x 2625.499639), or `energy_precision_kj_mol`; sources are optional.

### 25. Every energy an energy-ordering record or a network state energy states should be comparable with the energy TCKDB stores for the calculation(s) it cites.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_energy_ordering_not_compared`, `network_state_energy_sum_not_compared`, `network_energy_zpe_source_absent`, `network_state_energy_sources_partial` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** Absence, not contradiction. A stored energy or zero-point energy that is not stated, an E0 with no electronic energy to pair, or a pairing TCKDB cannot establish leaves nothing to contradict; refusing would lose a record that may be right. The energy is stored as not compared with its reason, and the warning names it (ADR 0008).

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
  *The outcome of every comparison is stored on the compared energy (`stored_energy_comparison` / `not_compared_reason`), so a reader can tell an energy that agrees with the stored one from one that was never held against it.*
- `collect_state_energy_source_warnings` — `backend/app/services/network_energy_sources.py::collect_state_energy_source_warnings`
  *The network route: the outcome is stored on the state energy (`source_sum_comparison` / `source_sum_not_compared_reason`). Reasons include a convention with no stored per-source terms, a zero no other state shares, a source with no stored energy of the needed kind, a state alone on its zero and `stated_precision_unknown` (beyond printed precision, inside honest rounding of the stated kJ/mol). Sources on only some participants warn with `network_state_energy_sources_partial`: the cited sources are one summand of several, so the stated energy cannot be held against the sum of every participant's stored energy, which is the same absence by another route.*
- `collect_zpe_source_absent_warnings` — `backend/app/services/network_energy_sources.py::collect_zpe_source_absent_warnings`
  *A state or barrier energy on a composed convention (`electronic_plus_zpe`, `atom_and_bond_corrected`, `thermal_enthalpy_298k`) that cites only an `sp` or `opt` when the upload carries no `freq` for that species or transition state: `network_energy_zpe_source_absent` (#669). An absence, so a warning; a `composite` and a single atom are not flagged.*

**Escape hatch.** None needed: the warning is the accommodation. Deposit the cited energy (and the ZPE, at the electronic energy's geometry) to compare; for a network state energy, cite every participant with an sp, opt or composite calculation and state `energy_precision_kj_mol`.

### 26. A calculation cited as the source of a stated energy should be of a type that carries that kind of energy: an electronic energy from an `sp`, `opt` or `composite`, an E0 from a `freq` or `composite`, never from an IRC point, scan, path search or conformer search; a network `well_freq` or `barrier_freq` source must be a `freq`; and a thermo or statmech source link gives a calculation a role its type can take.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `ts_energy_ordering_source_type_mismatch`, `network_energy_source_type_mismatch`, `thermo_source_role_type_mismatch`, `statmech_source_role_type_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. An energy taken from an IRC point, or from a bare single point labelled as an E0, is not the quantity it is labelled as, and an ordering or a master equation built on it compares unlike things; the master equation reads the modes of its frequency roles, which a `composite` (a ZPE only) does not store. The table of types is one table shared by the transition-state and network rules (`app.services.energy_source_types`), so the two cannot disagree about what carries an energy; it is a position a referee could dispute, which is why it is registered. A network energy on a composed convention (`electronic_plus_zpe` and the like) is held only to the floor of stationary-point types, because its one source slot cites the electronic part and the frequency goes in the frequency role: tightening it would refuse every producer and fixture in the repository. The thermo and statmech role tables (`opt`, `freq`, `sp` and, for statmech, `composite`) are the same rule for the products' own links.

**Enforced at.**

- `_assert_energy_sources_are_comparable` — `backend/app/services/transition_state_validation.py::_assert_energy_sources_are_comparable`
  *The transition-state `energy_ordering` evidence, at the shared evidence seam, so all three deposit paths refuse alike.*
- `assert_network_energy_source_type` — `backend/app/services/network_energy_sources.py::assert_network_energy_source_type`
  *Network state and barrier energy sources, read off the persisted row.*
- `assert_network_source_role_type` — `backend/app/services/network_energy_sources.py::assert_network_source_role_type`
  *The `well_energy` / `barrier_energy` and the frequency roles of the solve's `source_calculations`.*
- `assert_thermo_role_matches_calculation_type` — `backend/app/workflows/thermo.py::assert_thermo_role_matches_calculation_type`
- `assert_statmech_role_compatible` — `backend/app/services/statmech_resolution.py::assert_statmech_role_compatible`

**Escape hatch.** Cite the calculation that carries the energy: the `sp`, `opt` or `composite` for an electronic energy, the `freq` (or a `composite`) for an E0, and put the `freq` in the `well_freq` / `barrier_freq` role.

## Atom mapping across a reaction

### 27. An atom does not change element on the way across a reaction: carbon does not map onto nitrogen.

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
  `element = ts_element`
  Violating this returns **409 `atom_map_element_not_conserved`** — An atom map pairs two atoms of different elements. An atom does not change element on the way across a reaction.

**Escape hatch.** Case is not load-bearing, and no longer needs a special provision to stop it becoming so. The comparison used to be case-insensitive on both sides, because the two ends quote two different geometries and nothing guaranteed they spelled an element the same way — carbon becoming nitrogen is a contradiction, while `Cl` becoming `CL` is one program shouting where another did not, and refusing the second would have refused correct chemistry. `b4e7c1d20f83` canonicalised the symbol on the way into `geometry_atom.element` and `c5a1f8e3d074` made that a CHECK, so one element now has one spelling in every state the database can be in and both the constraint and the service compare the stored values directly. Isotope mass number is deliberately *not* carried across the same way, because a NULL disables a MATCH SIMPLE foreign key; isotope consistency is checked in the service layer instead.

### 28. One saddle-point atom is claimed by exactly one atom of each leg, and one participant atom maps to exactly one saddle-point atom.

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

### 29. Every atom index in a map is counted against a named geometry that the participant actually owns, and names an atom that geometry actually has; and a map belongs to a reaction that has a saddle point and names only participants that reaction declares.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_indices_not_geometry_relative`, `atom_map_participant_not_declared`, `atom_map_without_transition_state` |
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

### 30. When a map covers every declared participant of an atom-balanced reaction, both legs claim the same saddle-point atoms and no saddle-point atom is left unclaimed.

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

### 31. Where a deposit carries both an atom map and an IRC participant mapping for the same saddle point, they agree about which saddle-point atoms each participant is made of.

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

### 32. A reaction that has a transition state should say which atom of the reactants is which atom of the saddle point and of the products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_absent` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence, not contradiction. An unmapped reaction is an incomplete record rather than a false one — the rate constant is still the rate constant and what is missing is the mechanistic detail. Blocking would reject correct science over evidence the depositor may not have, and would make every reaction already in the database undepositable.

**Enforced at.**

- `_warn_absent` — `backend/app/services/reaction_atom_map.py::_warn_absent`
  *A reaction with no transition state is not warned about: both legs of a map run toward the saddle point, so a barrierless channel has nothing to map onto and a warning it could never satisfy would train depositors to ignore the one that matters. The PDep bundle has no `atom_map` field yet, so on that path the warning carries a different remedy sentence rather than naming a field that does not exist.*

**Escape hatch.** None is needed — the warning *is* the accommodation. TCKDB deliberately will not infer a map: several chemically distinct maps are usually consistent with the same reactants and products, so choosing one by algorithm would manufacture provenance.

### 33. A supplied atom map should cover every declared participant molecule, every atom of each mapped participant, and every atom of the saddle point.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_participants_incomplete`, `reaction_atom_map_atoms_incomplete` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0011, 0008 |

**Why this tier.** Absence again, at finer grain. A partial map is a true-but-partial record; only a map that contradicts *itself* is refused, and that is handled at the blocking tier by `validate_reaction_atom_map` and by the constraints on `reaction_atom_map_pair`.

**Enforced at.**

- `_warn_incomplete` — `backend/app/services/reaction_atom_map.py::_warn_incomplete`
  *Two codes from one seam: `reaction_atom_map_participants_incomplete` when a declared molecule is missing from the map entirely, `reaction_atom_map_atoms_incomplete` when a mapped participant leaves its own atoms unmapped or a leg leaves saddle-point atoms claimed by nobody.*

**Escape hatch.** None.

### 34. An atom map records whether a human asserted it or an algorithm produced it, an inferred map names the algorithm, and neither attribution can be relabelled afterwards.

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

### 35. An Arrhenius pre-exponential factor carries units of the dimensionality its reaction order requires — per-second for unimolecular, concentration^-1 time^-1 for bimolecular, concentration^-2 time^-1 for termolecular — and a coefficient of any other order has no units to check and is refused.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `arrhenius_a_units_molecularity_mismatch`, `unsupported_reaction_molecularity` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. The dimensionality of A follows from the rate law, so an A in cm3/mol/s on a unimolecular reaction is not an unusual result but a number that cannot mean what it says. A mis-declared unit is also silently catastrophic downstream, since nothing later in the stack can recover the intended order from the value alone.

**Enforced at.**

- `validate_a_units_for_molecularity` — `backend/app/chemistry/units.py::validate_a_units_for_molecularity`
  *Called from the kinetics upload schema, so it refuses at the wire boundary. The order is not simply `len(reactants)`: it counts the side the coefficient is a rate of (reactants for a forward record, products for a reverse one; a `net` record is not checked). A record that states no direction is checked against both sides and refused only when the units match neither (`validate_a_units_for_unstated_direction`), so no direction is assumed. A simple `+M` third-body reaction carries a `[M]` term on the main line and validates one order higher, while a falloff reaction's main line is the high-pressure limit k-infinity, whose order is the side's count, its low-pressure limit k0 being validated separately one order up.*

**Escape hatch.** None, and the refinements are the reason it can block without firing on correct science: PLOG and Chebyshev are refused the `is_third_body` flag outright, because both already encode the full pressure dependence and the flag would otherwise inflate the expected order by one — rejecting a PLOG entry carrying the *correct* units and accepting one carrying the units of the next order up. A record whose sides differ in size and which states no direction is accepted with units of either side's order; stating `direction` makes the check exact. A coefficient whose order is not 1, 2 or 3 (a four-body reaction) is refused with its own code, `unsupported_reaction_molecularity`, because no units of that dimensionality are accepted.

## Statistical mechanics

### 36. A partition function belongs to exactly one subject — a species entry or a transition-state entry, never both and never neither.

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

### 37. A set of phenomenological k(T,P) declares whether this database holds the master-equation derivation behind it; a `computed` solve must actually carry master-equation evidence, and a `reported` one must cite the publication it was transcribed from.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `reported_network_solve` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
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

### 38. A collisional energy-transfer model records whether its ⟨ΔE⟩down was determined per (well, collider) pair or declared once for the whole network.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `network_wide_energy_transfer_scope` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0009, 0008 |

**Why this tier.** A network-wide ⟨ΔE⟩down is correct, common, published science — Arkane, RMG and MESS inputs routinely specify a single `SingleExponentialDown` applied network-wide — so a check demanding one entry per (well x collider) pair could fire on a correct result and must not block. It is an expectation about *resolution*, not a definition. What stays definitional still blocks: a `per_well` entry naming no well contradicts itself, a `network_wide` entry naming one contradicts itself, and a payload mixing the two is genuinely ambiguous.

**Enforced at.**

- `ck_network_solve_energy_transfer_scope_columns_agree` (check on `network_solve_energy_transfer`)
  `(scope='per_well' AND state_id IS NOT NULL AND collider_species_entry_id IS NOT NULL) OR (scope='network_wide' AND state_id IS NULL AND collider_species_entry_id IS NULL)`
  Violating this returns **409 `energy_transfer_scope_columns_disagree`** — A collisional energy-transfer model declares a scope its own columns contradict: a 'per_well' model must name the well and the collider it was determined for, and a 'network_wide' one must name neither.

**Escape hatch.** Declare `scope='network_wide'`. The physics behind the old per-well rule was never in dispute — ⟨ΔE⟩down depends on the density of states of the excited well and on the collider's ability to accept internal energy, so argon and helium do not relax the same well identically. The rule was wrong in practice because it confused what the quantity *is* with what a calculation *determined*: the only way to satisfy it was to paste one number once per well, and the repository's own Arkane ingester did exactly that. Those rows are indistinguishable from independently determined values — a provenance loss manufactured by the validation itself, worse than the gap it closed, because an absent value is honest while a duplicated one is a false positive every consumer will faithfully propagate.

## Custody of the evidence

### 39. The bytes TCKDB serves for a stored artifact are the bytes it stored, and a record whose evidence is known not to be is labelled as such at read time rather than graded as if it were intact.

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

### 40. Whether a record's preserved evidence is sufficient to understand, audit or repeat it is assessed separately from how far its evidence is trusted and from whether a curator approved it, and the three may disagree.

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

## Advisory consistency

### 41. Compare every supplied NASA Cp/entropy fit with exact points, s298 and explicitly named neighbours.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.

**Enforced at.**

- `compare_thermo` — `backend/app/services/consistency/thermo.py::compare_thermo`
  *Explicit Phase D CLI/service invocation only. Scope is gas phase only (app.services.consistency.engine.SUPPORTED_PHASES, decided 2026-09-23): a record whose phase is recorded as something else, or was never recorded at all, is reported unavailable, not refused and not assumed gas.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 42. Compare explicitly supplied opposite elementary rates with equilibrium from explicitly mapped NASA thermo.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.

**Enforced at.**

- `compare_kinetics` — `backend/app/services/consistency/kinetics.py::compare_kinetics`
  *Explicit Phase D CLI/service invocation only. Scope is gas phase only (app.services.consistency.engine.SUPPORTED_PHASES, decided 2026-09-23): a thermo record whose phase is recorded as something else, or was never recorded at all, is reported unavailable, not refused and not assumed gas.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 43. Compare every stored Gibbs value of one thermo record with H(T) - T*S(T) taken from each of that record's own representations, H and S always from the same one.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** Explicit advisory comparison records residuals or unavailable reasons; the only flag is float precision on the same row's own identity, and there is no threshold or approval effect.

**Enforced at.**

- `compare_gibbs` — `backend/app/services/consistency/gibbs.py::compare_gibbs`
  *Explicit Phase D CLI/service invocation only (--check gibbs-self). A record whose enthalpy_reference_kind was never recorded is excluded, never assumed formation_298k; a G on a declared record that follows another convention is reported as its raw residual, never relabelled. Reference pressure does not gate this check (g, h and s of one record share one state); gas phase only, as the other Phase D checks.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 44. Compare a thermo record's supplied enthalpies (h298, exact points, NASA-7, NASA-9) at shared temperatures, compare each enthalpy change with the exact interval-local Cp integral of a fit, and report each fit's own boundary jumps -- residuals only.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.

**Enforced at.**

- `compare_kirchhoff` — `backend/app/services/consistency/kirchhoff.py::compare_kirchhoff`
  *Explicit Phase D CLI/service invocation only (check 'kirchhoff'). Only records that declare enthalpy_reference_kind take part; an undeclared record is reported enthalpy_reference_unrecorded, never given an inferred basis. Reference pressure never gates enthalpy. Cross-record anchors state that the element-reference compilation is not recorded and that its cancellation is assumed. Gas phase only, as for D1. Explicit temperatures (--temperature) are ADDED to the default grid {298.15} + temperatures of points carrying h -- unlike D1's thermo check, where they replace it. A record is never its own neighbour.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 45. Compare a kinetics record's stated reaction energy (its tunneling row, forward, separated-species zero) with the Hess sum of explicitly mapped formation enthalpies at the matching temperature.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** Explicit advisory comparison records residuals or unavailable reasons; no threshold or approval effect.

**Enforced at.**

- `compare_hess` — `backend/app/services/consistency/hess.py::compare_hess`
  *Explicit Phase D CLI/service invocation only (--check hess). thermal_enthalpy_298k pairs with formation enthalpies at 298.15 K under one declared enthalpy_reference_kind; electronic_plus_zpe and atom_and_bond_corrected pair with enthalpy_formation_0k_kj_mol. The element-reference compilation is not recorded, so cancellation across terms is assumed and every finding says so; so is the endpoint identity (separated species), which only a separated_reactants row's exactly-zero reactant energy partly checks. Gas phase only; ions and isotopologues out of scope; pressure never gates enthalpy.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 46. Find, on a protium species entry, calculation geometries that declare a non-standard nuclide of any element -- a D or T element symbol deposited before 2026-10-03, or a stored mass number -- and record each as a finding.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008, 0022 |

**Why this tier.** Rows stored before #672 (docs/adr/0022) hold D/T with no mass number, were accepted at the time and cannot be rewritten (trg_as_geometry_atom), so the contradiction is observed after acceptance. It cannot block; it is one append-only record_machine_review row a curator may read, with no status, selection, trust or approval effect.

**Enforced at.**

- `compare_isotope_identity` — `backend/app/services/consistency/isotope_identity.py::compare_isotope_identity`
  *Explicit CLI/service invocation only (--check isotope-identity, a spe_ ref). Judges only an entry whose isotope_key is NULL; a labelled entry is reported as not judged. Reads each atom's own stored mass number, or for a legacy row the one its own symbol names. The Pi held no D/T rows when measured on 2026-10-03; self-hosted instances are unknown.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

### 47. Report a conformer group whose converged optimised structures are linked end to end but are not all within the matching tolerance of each other (a wide basin), with the largest torsion difference between two of them.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008, 0005 |

**Why this tier.** A basin is the connected component of its optimised structures, so a chain can be wider than the tolerance. Splitting it automatically would put back an arbitrary threshold or a dependence on the order of deposition, so it is reported for curation instead: one append-only record_machine_review row a curator may read, with no status, selection, trust or approval effect.

**Enforced at.**

- `compare_wide_basin` — `backend/app/services/consistency/wide_basin.py::compare_wide_basin`
  *Explicit CLI/service invocation only (--check wide-basin, a cg_ ref). Reads the group's stored basin_is_clique and basin_span_deg, which the upload path and the reassignment derive from its defining members; a group with no defining member is reported as not judged. The reassignment logs a WARNING for each wide basin it writes.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

## External reference comparison

### 48. A computed thermo record's heat capacity, evaluated at each temperature an independent external observation reports, is compared against that observation and the residual is recorded -- never judged against a threshold and never fed back into the record's trust or review state.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |
| **Governing ADR** | 0008 |

**Why this tier.** ADR 0008 reserves the review tier for exactly this shape of claim: a comparison against reference data TCKDB did not itself compute and has no authority to grade. It cannot block -- the upload that produced the computed thermo record is long since accepted, and an external observation may itself be wrong, superseded, or reported on a real-gas basis the computed record was never fit to reproduce. It cannot warn either, because a warning annotates the depositor's payload at upload time and this check runs later, against data the depositor never supplied. So the consequence is the review tier: one append-only, rubric-versioned `record_machine_review` row (rubric `external_cp_comparison`, version 2) that a curator may read and act on, with no accuracy threshold authorized anywhere in this check -- picking one is out of scope for this change and is recorded as a hold point in the C4 plan.

**Enforced at.**

- `compare_thermo_with_cp_observations` — `backend/app/services/external_comparison/cp.py::compare_thermo_with_cp_observations`
  *Runs only from the admin/CLI trigger (app.services.external_comparison.cp.run_and_record via backend/scripts/run_external_cp_comparison.py), never from the upload path. Scope is gas phase only (app.services.consistency.engine.SUPPORTED_PHASES, decided 2026-09-23): a record whose phase is recorded as something else, or was never recorded at all, is reported unavailable, not refused and not assumed gas.*

**Escape hatch.** None.

*(No machine-readable code reaches anybody for this one. Recorded as a gap rather than invented, because a code nothing carries is a code no client can match on. See the enforcement sites above for why: a position held by schema shape, or by a stored evidence row, never surfaces as a refusal at all. A position held by a database constraint no longer belongs here — such a constraint can declare a rejection code and be named in its 409.)*

## Scan coordinates

### 49. A scan point's stored coordinate_value is the internal coordinate at that point's own sampled geometry, in that coordinate's own unit (ADR 0020) -- never a displacement, and never compared against start_value as an anchor.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `scan_point_coordinate_disagrees_with_geometry` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0020, 0008 |

**Why this tier.** ADR 0020 is explicit that a disagreement here cannot say which of two things is wrong: a mis-stated axis and a mis-attached geometry present identically to a comparison that only sees the stored number and the recomputed one. Refusing the deposit would discard correct energies over an ambiguity this check cannot resolve on its own, which is the ADR 0008 argument for warn rather than block. It also cannot be a blocking definition for a second reason: ADR 0008 reserves block for a record no correct calculation could produce, and the producers that deposited the superseded relative-sweep convention (ADR 0019) did so in good faith. It was held back as a standalone report until the corrective migration (`a4f7c2e9d651`, #302) had converted the stored series whose own geometry proves the conversion; with that done, a new deposit written in the superseded convention is exactly what the warning targets, so it is wired into the write path of every route that persists a scan, once per disagreeing coordinate series, and placed on the scan calculation's own record.

**Enforced at.**

- `collect_scan_coordinate_warnings` — `backend/app/services/scan_coordinate_conformance.py::collect_scan_coordinate_warnings`
  *The write path: run once a request's calculations are flushed, over every calculation the request persisted, by every workflow whose calculations can carry a scan result (conformer, transition state, computed species, computed reaction, thermo, statmech, transport). One warning per coordinate series with at least one point judged `deviates`; its context carries the scan's number in the request, the series' pattern (`classify_series`), the number of points that disagree, the largest residual and the tolerance. The tolerance is widened to half a step of the decimals the stored values were written at, so rounded measurements are not a disagreement, unless the stored values are an evenly spaced grid (gaps allowed): grid values are labels, not rounded measurements, and are judged at the tight tolerance. Declare step_size for a grid whose step is finer than 5 units of its last decimal; otherwise it is judged as rounded values. The warning targets output written in the superseded convention.*
- `evaluate_point_coordinate_conformance` — `backend/app/services/scan_coordinate_conformance.py::evaluate_point_coordinate_conformance`
  *Recomputes the coordinate from the point's own stored geometry only -- bond distance, bond angle, or the standard four-atom dihedral formula -- and never reads calc_scan_coordinate.start_value/end_value, which ADR 0020 fixes as grid metadata, not an anchor. improper is reported not_applicable rather than guessed at: ADR 0020 records that TCKDB has no field distinguishing an out-of-plane-angle convention from a proper-style torsion, so assuming either would be exactly the inference ADR 0011 refuses to make for atom maps, applied one level up. A near-collinear dihedral quartet (min(sin(theta_123), sin(theta_234)) < 0.05) is reported not_checkable rather than compared, mirroring what the producing tools themselves do. classify_series in the same module turns a series of per-point disagreements into the specific pattern the follow-up corrective migration needs: a constant residual across every point, a linear ramp against point index, a residual equal to one grid step, or a lone outlier.*

**Thresholds.**

- `dihedral_not_checkable_min_sin_theta` = **0.05 dimensionless (sin of a flanking bond angle)** — a constant fixed in code.
  ADR 0020: 'where a quartet is near-collinear the dihedral is not a usable coordinate at all'. Below this, 1/(r * sin(theta)) conditioning makes the recomputed dihedral's uncertainty diverge, so comparing it would manufacture a false disagreement rather than find a real one.
- `tolerance_floor_degrees` = **0.001 degree** — a constant fixed in code.
  Never let ten times the precision-derived sigma collapse below what a 6-decimal-place deposit can distinguish. Explicitly not the 0.5 or 1.0 degree floor rejected as roughly four orders of magnitude too loose -- correctly stored data reproduces to about 1.5e-4 degrees (ADR 0020), and this floor still catches an injected 0.01 degree error with two orders of magnitude to spare. This is the tolerance every point in the current corpus is actually compared against: at the corpus's real 6 decimal-place precision the precision-derived term is far below this floor, so the floor -- not the derived term -- is what binds on real data today. The derived term only starts to matter at coarser precision than the corpus has ever used; see `tolerance_ceiling_degrees` for what happens if it grows too large to trust.
- `tolerance_ceiling_degrees` = **1 degree** — a constant fixed in code.
  Without an upper bound, the precision-derived tolerance grows without limit as deposit precision degrades -- measured to reach 184.9 degrees at 0 decimal places, silently passing an injected 150 degree error, because the tolerance then exceeds the entire physically meaningful range of a wrapped residual. Above this ceiling the point is reported not_checkable instead of a pass under a window too wide to mean anything -- the detector failing loudly rather than reporting a false clean bill of health. Fixed at a flat number rather than derived from the coordinate's own step_size because step_size is optional metadata (nullable) and a fraction-of-step ceiling would need the same flat fallback for every coordinate that never declared one. Chosen to land between 3 decimal places (~0.213 degrees, comfortably checkable) and 2 decimal places (derives above this ceiling, correctly not_checkable) on the measured precision ladder -- not the floor's rejected 0.5/1.0 degree number reused: the floor asks how tight well-behaved data can be trusted to be, this asks how loose a window can get before it stops meaning anything, and those are different questions.
- `tolerance_ceiling_angstrom` = **0.1 Angstrom** — a constant fixed in code.
  Same role as tolerance_ceiling_degrees, for bond. 0.1 Angstrom is roughly 5-10 percent of a typical single bond length (about 1-1.5 Angstrom); a derived tolerance that wide could not tell a genuine bond-length error from deposit noise.

**Escape hatch.** None needed -- the check never refuses, at any tier. Send each point's coordinate_value as the internal coordinate measured on that point's own geometry (not as an offset from the scan's start), or attach the geometry the value was measured on. An `improper` coordinate is never compared, and a near-collinear dihedral quartet or a deposit too coarse to judge is reported as not checkable rather than as a disagreement.

## Vibrational frequency provenance

### 50. A calculation that records how its frequency job built the Hessian (`freq.hessian_method`) and also attaches the input deck that ran it, or an output log that echoes that input, states the same method in both.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `hessian_method_contradicts_input_deck` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0012 |

**Why this tier.** A contract between two statements in one deposit, not an expectation about a result, which is the narrow thing ADR 0008 reserves the blocking tier for. Tau, the line between a noise-level imaginary mode and a real one, is read from the Hessian method, and two statements of it that differ give two different taus (50 against 80 for the two numerical methods, 30 or less for analytic). Judging a mode against either would be a guess, and the record would carry both statements forever with nothing saying which was meant. The check makes no claim about chemistry and refuses no calculation: it refuses a deposit that cannot say what it did. Only a method the input deck (or the input an output log echoes) states with an explicit directive counts (Gaussian `Freq=Numer` or `EnOnly`, ORCA `NumFreq` or `AnFreq`); a deck with no such directive says nothing and contradicts nothing, and a recorded value TCKDB cannot read is not compared. One pair is not a contradiction: ORCA's `NumFreq` always differences gradients, and for a method with no analytic gradient (coupled cluster, F12, open-shell DLPNO-MP2, ROHF MP2) those gradients are themselves numerical, so a recorded `finite_difference_gradient` against such a deck is accepted, stored as `finite_difference_energy` and reported with the warning `hessian_method_refined`; a recorded `analytic` against `NumFreq` is still refused.

**Enforced at.**

- `check_recorded_method_agrees_with_deck` — `schemas/python/tckdb-schemas/tckdb_schemas/hessian_method_detection.py::check_recorded_method_agrees_with_deck`
  *Runs on the bundle calculation payloads that carry an inline `input` artifact, or an inline `output_log` whose echoed input states a method, with `context.stated_in` saying which (`input` or `output_log`) and the message naming the log, never a deck, when it was the log (computed-species, computed-reaction and network bundles). The standalone calculation, conformer and transition-state payloads have no `artifacts` field, and a deck uploaded later through the artifact route is not seen at validation; that case earns the warning `hessian_method_disagrees_with_stored_tau_basis` on the calculation instead, and the stored tolerance is never relabelled. The message and `context` carry only TCKDB's method tokens (`analytic`, `finite_difference_gradient`, `finite_difference_energy`), never the recorded string or deck text.*

**Escape hatch.** Correct whichever of the two is wrong, or omit the recorded `freq.hessian_method` and let the deck (or the log) speak. A deposit that sends only a deck or a log is never refused by this check; both are read before the deposit is judged.

## Stereochemistry of a species entry

### 51. A species entry states one stereo configuration: where its linked geometries, its SMILES marks and a supplied stereo label each state the same stereo element (R/S, E/Z or an allene's axis), they agree, compared up to the molecule's symmetry.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_stereo_contradiction` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | DR-0038, 0008 |

**Why this tier.** Definitional, once the species key is the constitution. A species entry is where one configuration is stated, and it is read from up to three sources: every linked geometry, the marks in the SMILES, and a supplied label. Two of them stating different configurations for the same element is a record that cannot describe one molecule; resolving it in either direction would invent a provenance, so the deposit is refused. Each source is compared with the others up to the molecule's symmetry (an alignment over the constitution's automorphisms), so a relabelled but identical configuration is never a contradiction. **The position a referee could dispute is the reading of coordinates:** deciding R/S, E/Z and an allene's axis from a geometry needs torsion windows, an alignment over automorphisms and an enumeration cap, none of which an input-format conflict (#672: D is 2H) needs. The windows below are deliberately conservative -- a geometry that falls outside them is *not read*, which states nothing and so contradicts nothing -- and exceeding a cap costs expressiveness (`species_entry_stereo_not_represented`, symmetric elements stated as `?`) and switches the comparison to the conservative multiset form. Entries 8 and 9 compare a geometry with its SMILES by element and isotope, never by configuration, so they do not cover this. Two neighbouring codes are not entries of their own: `species_stereo_label_invalid` (a malformed label, and the chemical branch of it, a label no stereoisomer of the molecule has) is input-shape validation, and `species_entry_stereo_not_represented` is the warning that reports the automorphism cap's cost.

**Enforced at.**

- `resolve_stereo` — `backend/app/chemistry/stereo.py::resolve_stereo`
  *Reads the entry's stereo from every geometry, then the SMILES marks, then a supplied label, and merges them in that order. Geometries of one entry are torsional conformers of one configuration, so they must agree with one another too; one that does not is a contradiction.*
- `merge_sources` — `backend/app/chemistry/stereo.py::merge_sources`
  *The comparison: the incoming source is aligned onto the current one over every automorphism and must agree wherever both state an element. A position that is ambiguous inside a symmetry class is refused rather than guessed.*
- `_refuse_disagreement_past_the_cap` — `backend/app/chemistry/stereo.py::_refuse_disagreement_past_the_cap`
  *Past the automorphism cap no alignment is available, so symmetry classes are compared as multisets before they are collapsed to `?`. A real disagreement (`R,R` marks against an `S,S` label) is still refused instead of being hidden by the cap.*

**Thresholds.**

- `allene_torsion_window` = **10 degree (lower bound; the upper bound is 170)** — a constant fixed in code.
  An allene's end-to-end torsion between the CIP-higher substituents is read only inside [10, 170] degrees (ideal 90). A flattened axis outside it is not read, which states nothing. Widening it reads more geometries and makes a contradiction from a distorted one more likely.
- `odd_cumulene_planar_tolerance` = **30 degree** — a constant fixed in code.
  An odd cumulene (a butatriene and longer) is Z within this torsion of planar, E within it of anti-planar, and otherwise twisted and unread.
- `automorphism_cap` = **100000 automorphisms examined** — a constant fixed in code.
  Most automorphisms of the constitution examined before the symmetric elements are stated as `?`. Past it the sources are compared as multisets instead; the cost is `species_entry_stereo_not_represented`.
- `enumeration_cap` = **8 potential stereo elements** — a constant fixed in code.
  Above this many potential elements the stereoisomer enumeration is skipped: `-` is not deduced, a supplied label is not tested for realisability and `stereo_smiles` is left unset.

**Escape hatch.** State `?` for the element, or omit the label: an unknown element contradicts nothing. If two geometries really are different stereoisomers, deposit the other isomer as its own species entry; one entry states one configuration.

## Wavefunction reliability diagnostics

### 52. A wavefunction reliability diagnostic that a calculation carries is reported when it is past its conventional threshold, with that threshold's source named and without judging what it means: the T1 diagnostic of a closed-shell coupled-cluster calculation above 0.02 (Lee and Taylor, 1989), and a stored <S^2> further than 0.1 from S(S+1) of the entry's declared multiplicity (a convention of the field, not a finding). A program's printed ideal <S^2> that is not S(S+1) of the declared multiplicity is reported separately. The result is stored as deposited; nothing about the value, the record's review status or any selection changes.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `t1_diagnostic_above_closed_shell_threshold`, `spin_contamination_above_conventional_threshold`, `spin_expected_contradicts_multiplicity` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** A closed-shell CCSD(T) energy whose reference is a poor starting point could be wrong in a scientifically interesting way, and a closed-shell transition state with bond breaking can cross 0.02. But the diagnostic is a contested rule of thumb: in a 50-molecule atomization-energy test at least 75% of the molecules T1 or D1 flagged had no large error (Sprague and Irikura 2014), and T1 is argued to measure orbital relaxation rather than multireference character. So it can only be an expectation (ADR 0008): a notable property of a correct result, reported once and worded as 'above the rule of thumb', never as 'the system is multireference'. This reverses the earlier position that T1 and D1 are 'deliberately not enforced or labelled' (scientific_calculation_reads.md); the reasons it is now acceptable are that the scope is closed shell only, the threshold has a primary source, and the sentence does not judge. The scope is the calculation: a thermo or kinetics record citing it earns no warning of its own. The spin-contamination criterion is weaker still: no primary paper recommends |<S^2> - S(S+1)| > 0.1. It is a convention that the literature states in passing ('Typically, a UHF wavefunction with Delta<S^2> over 0.1 [Williams et al., Chem. Phys. Lett. 2011, 504, 88, doi:10.1016/j.cplett.2011.01.020] or Delta<S^2>% over 10 % [Rettig et al., J. Chem. Theory Comput. 2020, 16, 7473, doi:10.1021/acs.jctc.0c00986] is considered severely spin-contaminated', arXiv:2307.10734). Rettig et al. use the relative form operationally, to split a benchmark: 'An energy is classified as NSC if all of the participating species have HF <S^2> values that deviate by no more than 10% from the exact, spin-pure <S^2> value.' So the sentence names it as the conventional criterion, not a finding about the system, and the absolute form is used because it does not grow with S. The comparison between the program's printed ideal <S^2> and the entry's multiplicity needs no threshold source: it compares two declared quantities, to a tolerance of 1e-3 that is far above print noise and far below the gap between spin states. That one is a declaration contradicting what the program ran, which ADR 0008 would put at the block tier; it is warn-tier here for the same recorded reason as entry 12's multiplicity_mismatch: such checks have not yet been run on real data, so their false-positive rate is unknown and promoting them first would be unsafe. It is grouped in this entry, not entry 12, because the entry is organised by the data it reads (the stored spin diagnostic and wavefunction diagnostic), not by the kind of claim.

**Enforced at.**

- `collect_wavefunction_reliability_warnings` — `backend/app/services/wavefunction_reliability_warnings.py::collect_wavefunction_reliability_warnings`
  *The write path: run once a request's calculations are flushed, over every calculation the request persisted, by every workflow that does (conformer, transition state, computed species, computed reaction, thermo, statmech, transport, network). The diagnostics enter only through the producer payload and nothing writes one later, so there is no late path. Three independent checks, each on a stored row. T1: fires when the entry's multiplicity (read from the entry, never inferred) is 1, a T1 is stored, the calculation's method is a restricted coupled-cluster method with singles (CCSD, CCSD(T), F12, DLPNO and PNO variants, CCSDT, QCISD) and T1 is strictly above 0.02. Spin contamination: fires when |s_squared - S(S+1)| is strictly above 0.1, S from the entry's multiplicity. Printed ideal value: fires when s_squared_expected differs from the entry's S(S+1) by more than 1e-3. Absent is absent. Open-shell T1 and D1 are stored and shown, never warned on.*
- `is_coupled_cluster_method` — `backend/app/services/wavefunction_reliability_warnings.py::is_coupled_cluster_method`
  *Scopes the T1 rule to methods whose T1 is Lee and Taylor's: a restricted coupled-cluster method with singles. An unrestricted method, a method the table does not recognise, a composite recipe that contains a CCSD(T) step, EOM-CCSD, BCCD(T), CCD, CC2 and CC3 are not in it.*
- Open-shell T1 and D1 are deliberately not thresholded. Add an open-shell T1 warning only when all three hold: (1) the producer reads the last 'T1 diagnostic' line of a Molpro open-shell log, not the first (the first is the RMP2-stage value, roughly thirty times too small); (2) TCKDB records the reference type (RHF, UHF or ROHF) next to the diagnostic, because the programs define open-shell T1 differently; (3) an open-shell threshold with a primary source is in hand (the 0.044 figure reached us only through secondary sources). D1 additionally needs a threshold verified in Janssen and Nielsen (1998) or a successor, and a producer that deposits it.
- Spin contamination is judged with the absolute criterion |s_squared - S(S+1)| > 0.1 (strict), S from the entry's declared multiplicity and never from s_squared or s_squared_expected, using s_squared and not s_squared_annihilated. The expectation is the entry's declared multiplicity so that a wrong ideal value printed by the program cannot hide a contaminated or mis-declared calculation; that disagreement is its own warning, spin_expected_contradicts_multiplicity, never merged with contamination. Both can apply to one calculation and then produce two warnings. Neither is judged for a calculation with no spin_diagnostic row.

**Thresholds.**

- `t1_closed_shell_threshold` = **0.02 dimensionless (singles norm over the square root of the number of correlated electrons)** — a constant fixed in code.
  Lee and Taylor, Int. J. Quantum Chem. 1989, 36 (S23), 199 (doi:10.1002/qua.560360824): 'a large T1 (i.e., >0.02) probably indicates the need for a multireference electron correlation procedure'. A rule of thumb with a hedge in its own abstract, used only for closed-shell coupled-cluster calculations, where every program computes the same quantity. Strictly greater than, so 0.0200 is on the line and does not warn.
- `s_squared_contamination_threshold` = **0.1 dimensionless (absolute deviation of <S^2> from S(S+1))** — a constant fixed in code.
  A convention of the field, not a recommendation of any paper: Williams et al., Chem. Phys. Lett. 2011, 504, 88 (doi:10.1016/j.cplett.2011.01.020) is cited for the absolute 0.1 and Rettig et al., J. Chem. Theory Comput. 2020, 16, 7473 (doi:10.1021/acs.jctc.0c00986) for a relative 10%. The absolute form is used. Strictly greater than, so a deviation of exactly 0.1 does not warn.
- `s_squared_expected_tolerance` = **0.001 dimensionless (absolute difference between two <S^2> values)** — a constant fixed in code.
  Compares the ideal <S^2> a program printed with S(S+1) of the declared multiplicity. Programs print it to four to six decimals, so 1e-3 is well above print noise, and the nearest distinct spin states differ by at least 0.75, so it cannot hide a different multiplicity.

**Escape hatch.** None needed -- no check here refuses. A calculation whose T1 is above 0.02, or whose <S^2> is more than 0.1 from S(S+1), for a reason the depositor understands is stored as sent; the warning only reports the value against the conventional criterion. A printed ideal <S^2> that contradicts the declared multiplicity is cleared by correcting whichever of the two is wrong.

## Bath gas of a rate

### 53. A bath gas a rate or a network solve states is a composition: its mole fractions sum to one, and for a rate each species appears once and no bath gas is given at the high-pressure limit, where there is no collider.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_bath_gas_mole_fraction_sum`, `kinetics_bath_gas_duplicate_species`, `kinetics_bath_gas_at_high_pressure_limit` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A mole-fraction set that does not sum to one, or that names one species twice, is not a composition, and no correct measurement or calculation can have been obtained in it. The high-pressure limit is the rate with collisional energy transfer removed, so a bath gas attached to it contradicts the pressure context the same record states. A network solve's bath gas is held to the same sum, with the same tolerance. All three are contradictions inside one payload, not expectations about a result. Nothing is refused for an absent bath gas: it reads as 'not stated' and is never inferred, which is why the check is a block and not a demand.

**Enforced at.**

- `check_kinetics_bath_gas` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/kinetics_bath_gas.py::check_kinetics_bath_gas`
  *Shared by the kinetics upload and the computed-reaction bundle, so the two routes cannot disagree about what a valid bath gas is.*
- `check_bath_gas_mole_fraction_sum` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/kinetics_bath_gas.py::check_bath_gas_mole_fraction_sum`
  *The sum rule alone, which the network solve's bath gas also calls.*

**Thresholds.**

- `bath_gas_mole_fraction_sum_tolerance` = **1e-09 absolute, on the sum** — a constant fixed in code.
  Tight on purpose: fractions are stated, not measured, so a sum that is off by more than rounding means a component is missing or doubled.

**Escape hatch.** Omit the bath gas (it then reads as not stated), merge repeated components into one with their total mole fraction, or state the pressure context the rate was obtained in.

## Network composition and sources

### 54. The kind of a network channel (isomerization, association, dissociation, exchange) is the one its source and sink states imply, so a stated kind that disagrees is refused.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_channel_kind_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. Whether a channel is an association or a dissociation follows from whether each end is a well or a set of separated fragments, by one rule applied to every pair (`derive_channel_kind`). A stated kind is only ever a claim to check, and one that disagrees contradicts the states the same payload declares. No correct network can contain it, and storing it would let two readers disagree about the direction of the same channel.

**Enforced at.**

- `derive_channel_kind` — `schemas/python/tckdb-schemas/tckdb_schemas/network_channel_kind.py::derive_channel_kind`
  *The one place the rule lives; the upload validator and the persisting workflow both call it.*
- `NetworkPDepUploadRequest.validate_channel_kind_from_states` — `backend/app/schemas/workflows/network_pdep_upload.py::NetworkPDepUploadRequest.validate_channel_kind_from_states`
  *Fills an omitted kind and refuses a stated one that differs; the workflow repeats the comparison.*

**Escape hatch.** Omit the kind and it is derived from the states.

### 55. A network state is the multiset of its participants' species entries, a state lists a species entry once (with a stoichiometry), and a network holds one state per composition.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_state_duplicate_composition`, `network_state_duplicate_participant` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A well or a separated-fragment set is identified by what it is made of; two states with the same composition are one state declared twice, and a state naming one entry twice states a stoichiometry the wrong way. Both are refused before any row is written. Stereoisomers are different species entries, so a network that really holds two isomers is not refused.

**Enforced at.**

- `refuse_duplicate_state_compositions` — `backend/app/workflows/network_pdep.py::refuse_duplicate_state_compositions`
  *Runs before any state row is written and names the depositor's local state keys.*

**Escape hatch.** Declare each state once. If two states are meant to differ, say how (stereo marks, a stereo label or a geometry on the species) so they resolve to different entries.

## Levels and geometries of linked calculations

### 56. One record has one energy level and the declared level matches it: the linked single points or composites share one level, which the declaration names (the optimisations' when none is linked); for kinetics, each linked energy, modulo spin treatment per participant.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_energy_level_contradiction`, `statmech_energy_level_contradiction`, `kinetics_energy_level_contradiction`, `thermo_energy_level_ambiguous`, `statmech_energy_level_ambiguous`, `thermo_energy_level_requires_sp`, `statmech_energy_level_requires_sp` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** A contract between statements of one record, not an expectation about a result. A depositor may legitimately run the optimisation and the single point at different levels (a legitimate shape), which is why the record must say which level its energy stands at, once: linked energies at two levels give no single answer (`*_energy_level_ambiguous`), a declared level that contradicts the linked energies is a false label (`*_energy_level_contradiction`), and a declared level that differs from the optimisation's with no energy calculation at it linked has nothing behind it (`*_energy_level_requires_sp`, the declared-level branch; its coverage branch is entry 57). No re-run decides which statement was meant. Kinetics is compared per participant and modulo spin treatment, so a restricted and an unrestricted calculation of closed-shell species do not contradict each other.

**Enforced at.**

- `assert_role_consistency` — `backend/app/services/calculation_levels.py::assert_role_consistency`
  *Thermo and statmech, on every upload shape; the codes arrive as arguments, one set per product.*
- `assert_kinetics_energy_level_consistency` — `backend/app/services/calculation_levels.py::assert_kinetics_energy_level_consistency`
  *Kinetics, by `kinetics_energy_level_violation`.*

**Escape hatch.** Omit the declaration (the level is then derived from the links at read time), link every energy at one level, or declare the level the linked calculations ran at.

### 57. Energy calculations and optimisations pair one-to-one by geometry: each energy sits on a linked optimisation's geometry, each optimisation has one energy, and an SCF stability source is on the verdict's conformer.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_sp_geometry_mismatch`, `statmech_sp_geometry_mismatch`, `thermo_role_duplicate`, `statmech_role_duplicate`, `thermo_energy_sp_and_composite_linked`, `statmech_energy_sp_and_composite_linked`, `thermo_energy_level_requires_sp`, `statmech_energy_level_requires_sp`, `scf_stability_source_geometry_mismatch`, `scf_stability_source_conformer_unanchored` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** Definitional. A single-point energy belongs to the structure it was computed at; paired with another conformer's optimisation it is a different molecule's energy, two energies on one optimisation (or a single point and a composite) are two answers to one question, and an ensemble that gives some conformers a refined energy and not others is the 'forgot the single point' deposit. A stability analysis describes one wavefunction at one geometry. For thermo and statmech silence is kept where either side declares no geometry (absence of evidence is not evidence of a mismatch, and with at most one optimisation an energy with no geometry is assumed to belong to it). The stability half is stricter: a verdict and its source that are tied to different conformers are refused here, exactly one of them unanchored is refused (`scf_stability_source_conformer_unanchored`), and only when neither is tied to any conformer is it the warning of entry 80.

**Enforced at.**

- `assert_role_consistency` — `backend/app/services/calculation_levels.py::assert_role_consistency`
  *Rules R2' (distinctness), R3' (geometry) and Coverage, run once per product with that product's codes.*
- `scf_source_geometry_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_geometry_error`
  *Builds the stability-source refusal for both the request schema and the workflow (ADR 0017).*
- `scf_source_unanchored_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_unanchored_error`
  *Exactly one of verdict and source is tied to a conformer.*

**Escape hatch.** Link the optimisation whose geometry the energy was computed at, one energy per optimisation.

### 58. A calculation is evidence for one conformer and ran on one structure: its conformer key and its geometry key name the same conformer, and the geometry it states it ran on is one of its input geometries; a transition-state calculation did not run on a species minimum's geometry.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `calculation_geometry_conformer_mismatch`, `calculation_input_geometry_mismatch`, `transition_state_calculation_species_geometry` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0017 |

**Why this tier.** A contract between two statements of one calculation. Neither key overrides the other and neither can be chosen for the depositor, so storing both would attribute the same numbers to two conformers. A freq or single point runs on one structure; a stated geometry outside its own input list is a contradiction, not an unusual result.

**Enforced at.**

- `assert_calculation_geometry_matches_conformer` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::assert_calculation_geometry_matches_conformer`
- `attach_calculation_input_geometries` — `backend/app/services/calculation_resolution.py::attach_calculation_input_geometries`
- `assert_transition_state_calculations_unanchored` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::assert_transition_state_calculations_unanchored`

**Escape hatch.** State one conformer, or omit the redundant key; list the geometry the calculation ran on.

## Composite and single-point arithmetic

### 59. The numbers a composite energy states agree with each other and with its recipe: E0 is the electronic energy plus the recipe's zero-point energy, the stated terms sum to the electronic energy, and an assembled composite's total is what its scheme gives for the energies of its inputs.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `composite_e0_inconsistent`, `composite_terms_do_not_sum`, `composite_total_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** Definitional, and a comparison only: TCKDB forms the sums to compare them with the numbers sent and stores none of them. A total that does not follow from the inputs it cites contradicts its own recipe; an E0 that is not electronic plus zero-point contradicts what the three quantities are. The tolerance scales with the number of rounded quantities in the comparison, so printed precision never trips it.

**Enforced at.**

- `assert_composite_result_arithmetic` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py::assert_composite_result_arithmetic`
  *Run by the wire model on parse and again by the backend on persist, so the tolerance cannot differ.*
- `assert_composite_total_recomputes` — `schemas/python/tckdb-schemas/tckdb_schemas/composite_total.py::assert_composite_total_recomputes`

**Thresholds.**

- `composite_arithmetic_tolerance` = **1e-06 hartree (floor; 5e-7 per rounded quantity above two)** — a constant fixed in code.
  An equation among n numbers printed to six decimals can disagree by n half-units of the last place without anything being wrong; the floor protects short equations.
- `composite_total_recomputation_tolerance` = **1e-06 hartree (floor; 5e-7 times 1 + the summed sensitivity weights)** — a constant fixed in code.
  The recomputed total is max(1e-6, 5e-7 * (1 + sum_i |d total / d x_i|)) hartree over every stored number the recomputation consumed: a larger-basis energy enters an inverse-power extrapolation with a weight above 1, so a total computed from inputs printed to six decimals stays inside the tolerance it earns. The deposited total counts as one number of weight 1.

**Escape hatch.** Send the values the program printed, or omit the number you did not read.

### 60. Each input of an assembled composite ran at the level of theory its slot in the scheme names, and the inputs and the composite itself declare one geometry between them.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `composite_input_level_mismatch`, `composite_input_geometry_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** Definitional. A recipe that sums energies taken at different geometries describes no single molecule, and a slot filled by a calculation at another level is a different recipe from the one the scheme names. Spellings of one level are followed through merges, so only a genuinely different level refuses. An input that declares no geometry is a warning, not this refusal: absence of a geometry does not show a mismatch.

**Enforced at.**

- `_finalize_one` — `backend/app/services/composite_input_resolution.py::_finalize_one`
  *Also runs the one-geometry rule (`_check_geometries`).*

**Escape hatch.** Cite the calculation at the slot's level, all computed at the one geometry.

### 61. The parts of a single point's electronic energy do not contradict the energy they are parts of: each component is stated once, a stated total is the energy, and reference plus correlation (plus triples, where the program prints it separately) is the energy.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `sp_energy_component_total_mismatch`, `sp_energy_components_do_not_sum`, `sp_energy_component_duplicate` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** Definitional. A composite level consumes these parts, so a part from a different run than the energy poisons every number built on it. A component stated twice has two values and no way to say which is the energy's part (the same shape as a bath-gas species named twice, entry 53). Either convention for the triples is accepted and both sums are reported when neither matches, so a producer using ORCA's or Molpro's printing is not refused. TCKDB forms the sums only to compare them and never fills an absent part.

**Enforced at.**

- `check_sp_energy_components` — `schemas/python/tckdb-schemas/tckdb_schemas/sp_energy_components.py::check_sp_energy_components`

**Thresholds.**

- `sp_energy_component_sum_tolerance` = **1e-06 hartree** — a constant fixed in code.
  The tolerance the single-point log reconciliation uses: tight enough to catch a part from a different run, loose enough for the digits a program prints.

**Escape hatch.** Send the parts the program printed with its energy, or omit the components.

### 62. A frequency calculation linked beside a composite runs at the level of theory the composite's scheme prescribes for frequencies, because the recipe's zero-point energy is scaled for that level; and the composite role is given only to a calculation of type composite.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `composite_frequency_level_differs_from_recipe`, `composite_role_on_non_composite_calculation` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** An expectation, so a warning. A depositor may legitimately link the frequencies they have, at another level, and say so; the record's frequencies and its energy then describe different levels, which is worth telling the depositor and the reader but cannot be shown to be wrong. The composite role on a calculation of another type is the legacy shape from before the composite type existed: a warning now, to be refused once producers can send the new shape.

**Enforced at.**

- `collect_composite_link_warnings` — `backend/app/services/calculation_levels.py::collect_composite_link_warnings`

**Escape hatch.** None needed: the link is stored as stated. Link the frequency calculation at the recipe's level.

### 63. A named composite method (CBS-QB3, G4, W1U) is one program run and is deposited as a calculation of type composite, not as an optimisation or a single point at its level.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `named_composite_deposited_as_opt`, `named_composite_deposited_as_sp` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** The legacy shape from before the composite type existed, so a warning (refused once producers can send the new shape). A composite's number is a recipe's, not an optimisation's or a single point's, and storing it under the wrong type lets every reader take it for one; the deposit is kept as sent because the energy it carries is still true.

**Enforced at.**

- `collect_named_composite_deposit_warnings` — `backend/app/services/composite_result_resolution.py::collect_named_composite_deposit_warnings`

**Escape hatch.** None needed: the deposit is accepted. Send it as type composite with a composite_result block.

### 64. An assembled composite's inputs each declare the geometry their energy is at, and the deposited total can be recomputed from them, so the checks of entries 59 and 60 can run.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `composite_input_geometry_undeclared`, `composite_total_unverifiable` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** The absence sibling of entries 59 and 60. An input with no declared geometry cannot show that all inputs share one, and a total whose energies, components or correlation convention are not stated cannot be recomputed; an incomplete record is still a true record, so these warn (ADR 0008) and the deposit is kept.

**Enforced at.**

- `_unverifiable_warning` — `backend/app/services/composite_input_resolution.py::_unverifiable_warning`
  *The geometry-undeclared warning is raised in `_check_geometries`.*

**Escape hatch.** None needed: the deposit is accepted. Declare each input's geometry and state the energies used.

## Subject of a record

### 65. What a record cites as evidence belongs to the record's own subject (its species, transition state or participant). A source of another subject's is that subject's evidence, not weaker evidence for this one.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_energy_source_subject_mismatch`, `thermo_source_calculation_owner_mismatch`, `statmech_source_calculation_owner_mismatch`, `transport_source_calculation_owner_mismatch`, `scf_stability_source_calculation_owner_mismatch`, `composite_input_owner_mismatch`, `statmech_torsion_scan_calculation_owner_mismatch`, `applied_energy_correction_source_calculation_owner_mismatch`, `thermo_statmech_owner_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, and the same claim wherever it is made: a number is attributed to a subject by the calculations behind it, so a source of another molecule's, another saddle point's or another reaction's is not weak evidence for this record but a different record's evidence, and borrowing it would put one subject's provenance on another's number. The refusals are separate codes because the repair differs (the wrong job, the wrong role and the wrong rotor scan are different fields), not because the rule does. A bimolecular network state accepts a calculation of any one of its participants.

**Enforced at.**

- `assert_owned_by` — `backend/app/services/calculation_ownership.py::assert_owned_by`
  *The shared seam; every source-calculation and statmech owner code above arrives as its argument, through `assert_calculation_owned_by` and `assert_statmech_owned_by`.*
- `owner_mismatch_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::owner_mismatch_error`
  *Transport and stability source links, built once for the request schema and the workflow (ADR 0017).*
- `assert_state_energy_source_owner` — `backend/app/services/network_energy_sources.py::assert_state_energy_source_owner`
- `assert_barrier_source_owner` — `backend/app/services/network_energy_sources.py::assert_barrier_source_owner`
- `assert_network_role_source_owner` — `backend/app/services/network_energy_sources.py::assert_network_role_source_owner`
- `assert_state_participant_source_owner` — `backend/app/services/network_energy_sources.py::assert_state_participant_source_owner`

**Escape hatch.** Cite the calculation or statmech record of the subject the record is about.

## Declarations against the record

### 66. A kinetics applicability or protocol declaration does not contradict the columns the record already states (direction, pressure context, bath gas, scientific origin): the stored columns stay authoritative.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_declaration_contradicts_record` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** A contract between two statements of one record. A declaration is a claim, stored as made, and is never reconciled with the columns: an 'experimental' method on a computed record, or a fixed-mixture collider whose species differ from the bath gas, cannot both be true of the same rate. A column that is not stated is not a contradiction; the declaration is then the only statement.

**Enforced at.**

- `kinetics_applicability_error` — `schemas/python/tckdb-schemas/tckdb_schemas/kinetics_declarations.py::kinetics_applicability_error`
- `kinetics_protocol_origin_error` — `schemas/python/tckdb-schemas/tckdb_schemas/kinetics_declarations.py::kinetics_protocol_origin_error`
- `assert_kinetics_declaration_columns` — `backend/app/services/kinetics_declaration_resolution.py::assert_kinetics_declaration_columns`

**Escape hatch.** Correct whichever statement is wrong, or omit the declaration.

### 67. A determination, statistical-model, structure, protocol or interpretation declaration describes the record that carries it. Its reaction, direction, source, subject, cited calculations, statmech record and conformer selection are that record's own.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_determination_mismatch`, `statmech_model_declaration_mismatch`, `structure_determination_mismatch`, `kinetics_protocol_calculation_owner_mismatch`, `thermo_protocol_calculation_owner_mismatch`, `kinetics_interpretation_statmech_owner_mismatch`, `kinetics_interpretation_conformer_selection_owner_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** A contract about what the record says, and the claim in this group closest to referential integrity: the same subject rule as entry 65, made about a declaration instead of a link. It is registered because the declarations are the evidence a later method-aware comparison reads, and a determination of another reaction, or a model of another species, would be read as evidence for this one. `context.reason` names which relation failed. A declaration that restates an existing key with different content is refused here too, because a determination is immutable.

**Enforced at.**

- `_mismatch` — `backend/app/services/kinetics_declaration_resolution.py::_mismatch`
  *Also the kinetics protocol-calculation owner refusal.*
- `_resolve_protocol` — `backend/app/services/thermo_declaration_resolution.py::_resolve_protocol`
- `_mismatch` — `backend/app/services/statmech_declaration_resolution.py::_mismatch`
- `_mismatch` — `backend/app/services/structure_determination_resolution.py::_mismatch`
- `assert_statmech_owned_by` — `backend/app/services/calculation_ownership.py::assert_statmech_owned_by`

**Escape hatch.** Declare it on the record it describes, with the calculations and sources of that record.

## Observation identity

### 68. A curator attaches an observation of unresolved identity only to a ground-state, minimum-energy entry of a species whose connectivity agrees with the observation's own identity hint.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `observation_identity_target_not_ground_state_minimum`, `observation_identity_hint_conflict` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** A contract on a curator action, not a claim about a result. An observation carries no stereochemical or excited-state resolution of its own, so attaching it to an excited-state or non-minimum entry would present it as that state's, and a hint naming another molecular skeleton than the target's species would present it as another molecule's. Only the connectivity block is compared, so a stereochemistry-only difference is accepted. The ground-state-minimum test is the one the automatic resolver uses.

**Enforced at.**

- `_assert_ground_state_minimum_entry` — `backend/app/services/observation_identity_attach.py::_assert_ground_state_minimum_entry`
- `_assert_no_identity_hint_conflict` — `backend/app/services/observation_identity_attach.py::_assert_no_identity_hint_conflict`

**Escape hatch.** Attach it to the ground-state minimum entry of the species it was measured on.

## Corrections and scale factors

### 69. A Petersson bond-additivity correction applied as a total carries at least one bond component, unless its target species has no bonds; a transition state has no bond assignment and so cannot take a componentless total.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `bac_total_requires_components` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A Petersson bond additivity correction is a sum over bonds; a total with nothing behind it cannot be shown to be one rather than an empty sum or an arbitrary number. A species with no bonds (an atom) has nothing to sum. A Melius total does not decompose into bond components and is not judged.

**Enforced at.**

- `assert_bac_total_has_required_components` — `backend/app/services/energy_correction_resolution.py::assert_bac_total_has_required_components`

**Escape hatch.** Send the bond components, or apply the correction under another role.

### 70. The frequency scale factor a statmech record applies was derived for the program that ran the frequency calculation it is applied to.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `statmech_frequency_scale_factor_software_mismatch` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** A harmonic scale factor is specific to the code and level it was fit against, but applying one across codes is a deliberate and sometimes documented choice (re-scaling frequencies computed elsewhere), so it could fire on a correct deposit and cannot block. Both softwares must be known and differ; 'nothing to compare' is a distinct state and never counts as agreement.

**Enforced at.**

- `collect_frequency_scale_factor_software_mismatch_warnings` — `backend/app/services/statmech_resolution.py::collect_frequency_scale_factor_software_mismatch_warnings`

**Escape hatch.** None needed: the deposit is accepted. Confirm the choice, or attach a factor derived for that program.

## Enthalpy reference

### 71. A thermo record that carries an enthalpy states what the enthalpy is relative to, the only accepted reference is the standard enthalpy of formation at 298.15 K, and a declaration is made only for a record that carries enthalpy content.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `enthalpy_declaration_absent`, `enthalpy_reference_kind_unrecognized`, `enthalpy_declaration_without_content`, `enthalpy_quantity_not_storable_here` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** A contract, and the most consequential one for downstream arithmetic. An enthalpy is a number that means something only against a chosen zero; TCKDB used to store the number and not the zero. Combining enthalpies of different references (a reaction enthalpy, a Hess cycle) gives nonsense that looks like a result, so an undeclared enthalpy is refused rather than defaulted, and a producer that does not know its own convention must refuse to emit it. A near-miss spelling of the one value gets its own refusal because it is a typo, not a different quantity.

**Enforced at.**

- `enthalpy_reference_error` — `schemas/python/tckdb-schemas/tckdb_schemas/enthalpy_reference.py::enthalpy_reference_error`
  *The rule itself, shared with the client builder and the bundle export.*
- `assert_enthalpy_reference` — `backend/app/workflows/thermo.py::assert_enthalpy_reference`
  *Raises the refusal for the thermo upload.*

**Escape hatch.** Declare `formation_298k` when the source states that convention. Sensible increments and absolute enthalpies belong to the molecular property observation route.

## Calculation graph

### 72. The graph of which calculation depends on which is acyclic: no calculation depends on itself through a chain of others.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `calculation_dependency_cycle` |
| **Code reaches a client via** | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |
| **Governing ADR** | 0008 |

**Why this tier.** Held by the shape of the schema. A calculation that is an input to itself has no order in which it could have been run, so a record violating it cannot be represented. It is a trigger and not a Python check so that no write path can bypass it; the lock trigger beside it serialises concurrent inserts that would together close a cycle.

**Enforced at.**

- `trg_calculation_dependency_acyclic` (trigger on `calculation_dependency`)
  `BEFORE INSERT OR UPDATE FOR EACH ROW: refuse an edge whose parent is already reachable from its child`
- `ck_calculation_dependency_not_self` (check on `calculation_dependency`)
  `parent_calculation_id <> child_calculation_id`

**Escape hatch.** Drop or reverse the dependency that points back.

### 73. SCF stability verdicts do not cite one another as the job that measured them in a ring: some job measured each.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `scf_stability_source_cycle` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A verdict that names, as its measuring job, a calculation whose own verdict names the first has no measuring job at all. It is the bundle-level sibling of the dependency graph rule (entry 72), refused before anything is stored.

**Enforced at.**

- `scf_source_cycle_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_cycle_error`
  *The same refusal from the request schema and the workflow (ADR 0017).*

**Escape hatch.** Name, as the measuring job, a calculation that is not itself measured by the first.

## Energy comparison

### 74. A lowest-energy selection compares energies of one level and one species entry, and a relative-energy comparison names two different protocols.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `lowest_energy_across_levels`, `unsafe_lowest_energy_comparison`, `relative_conformer_energy_compare_identical_protocols` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional, on the read side. Absolute energies from different levels differ by far more than the quantity a lowest-energy selection reports, so the answer would be an artefact of the levels; a protocol compared with itself compares nothing. The refusal names what to narrow rather than choosing for the reader.

**Enforced at.**

- `_refuse_mixed_levels` — `backend/app/api/routes/lookup.py::_refuse_mixed_levels`
- `_validate_ranking` — `backend/app/services/scientific_read/species_calculations_search.py::_validate_ranking`
- `identical_protocols` — `backend/app/services/relative_conformer_energy/compare.py::identical_protocols`

**Escape hatch.** Filter to one level (method and basis) and one species entry, or use a select endpoint.

## Evidence a result rests on

### 75. An optimisation recorded as converged has an energy behind it: a single point of the same owner at any level, or its own final energy stated with an artifact.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `converged_opt_no_usable_energy` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An absence, so a warning: a converged geometry is a claim about the energy, but an incomplete record is still a true one and trust and reproducibility grade it. The sibling of entry 19, which asks for evidence that a saddle point connects its endpoints.

**Enforced at.**

- `collect_converged_opt_energy_warnings` — `backend/app/services/calculation_resolution.py::collect_converged_opt_energy_warnings`

**Escape hatch.** None needed: the deposit is accepted. Deposit a single point, or the optimisation's final energy with its artifact.

## Declared against the uploaded evidence

### 76. What a depositor typed about a calculation's settings and level of theory agrees with what the uploaded input deck and output log say, the deck agrees with the log, a later log agrees with an earlier one, and a value a log reports agrees with the value it was asked for.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `parameter_disagrees_with_artifact`, `input_deck_disagrees_with_output_log`, `output_log_disagrees_with_earlier_output_log`, `reported_setting_differs_from_requested`, `level_of_theory_disagrees_with_artifact` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An expectation about evidence, so it warns. A deck that differs from a log is common and legitimate (the deck may not be the exact input that ran), a log legitimately reports a value the program chose rather than the one requested, and a parser reads only a subset of a log; any of these could fire on a correct deposit. It is compared only where both sides state a canonical value for a setting that can change the result, only for the program the calculation declares, and absence is never a disagreement. Nothing is refused or corrected, and the precedence (reported, echoed, deck, typed) decides which value is shown.

**Enforced at.**

- `declared_parameter_warnings` — `backend/app/services/parameter_reconciliation.py::declared_parameter_warnings`
- `merge_artifact_observations` — `backend/app/services/parameter_reconciliation.py::merge_artifact_observations`
- `level_of_theory_warnings` — `backend/app/services/parameter_reconciliation.py::level_of_theory_warnings`
- `level_of_theory_qualifier_warnings` — `backend/app/services/parameter_reconciliation.py::level_of_theory_qualifier_warnings`
- `reported_setting_warnings` — `backend/app/services/parameter_reconciliation.py::reported_setting_warnings`

**Escape hatch.** None needed: the deposit is accepted. Correct the typed value, or attach the right deck.

### 77. The electronic energy a tool states for a single point, and the E0, zero-point and electronic energies it states for a composite, agree with the output log attached to the same calculation, and the log is the composite method the level of theory names.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `sp_energy_payload_log_mismatch`, `composite_energy_log_mismatch`, `composite_log_method_mismatch` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0021 |

**Why this tier.** The log is evidence, not authority: a tool may store a value at reduced precision, a parser may read the wrong line, and a flagged mismatch is for a reviewer, not a reject. TCKDB keeps the number exactly as sent and never overwrites it. A log that is a different composite method from the level of theory is flagged and the energies are then not compared, because they answer different questions. Where nothing can be read from the log the deposit stands with a recorded reason, so an unparseable log is never a mismatch.

**Enforced at.**

- `reconcile_sp_energy` — `backend/app/services/sp_energy_reconciliation.py::reconcile_sp_energy`
- `reconcile_composite_energy` — `backend/app/services/composite_energy_reconciliation.py::reconcile_composite_energy`

**Thresholds.**

- `composite_log_tolerance` = **1e-06 hartree (max(1e-6, 5e-7 * n), n the rounded quantities compared)** — a constant fixed in code.
  Each log number was printed to six decimals and so was a deposited one copied from it: E0 against its 0 K line and the ZPE against E(ZPE) use n = 2, the electronic energy against E0 - E(ZPE) uses n = 3.
- `sp_energy_log_tolerance` = **1e-06 hartree** — a constant fixed in code.
  Tight because a mismatch is only a reviewer flag: a wrong line, a wrong method or a unit slip diverges by far more, while a tool storing reduced precision stays inside it.

**Escape hatch.** None needed: the deposit is accepted. Send the value the log prints, or omit it and let the log speak.

### 78. The Hessian method a deck or log attached after the deposit states is the method the calculation's stored imaginary-mode tolerance rests on.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `hessian_method_disagrees_with_stored_tau_basis` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** The sibling of the refusal in entry 50, for the case that refusal cannot reach. A deck or log attached through the artifact route is read after the deposit was judged, so the tolerance was already decided without it, and stored rows are append-only: nothing is relabelled. The contradiction is real but the record it concerns is already accepted, so the only available consequence is an annotation. Agreement, a deck with no frequency directive and a tolerance basis resting on no method warn nothing.

**Enforced at.**

- `try_hessian_method_disagreement_from_input_upload` — `backend/app/services/hessian_method_deck_check.py::try_hessian_method_disagreement_from_input_upload`

**Escape hatch.** None needed: the record is annotated, not refused.

### 79. What a neutral ground-state atom declares agrees with the NIST ground term of its element: its term symbol is the ground term, and the degeneracy of the lowest electronic level it deposits is one that term allows (the J-resolved ground level, or the lowest levels lumped up to the unsplit term).

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `atomic_electronic_degeneracy_contradicts_term`, `term_symbol_contradicts_ground_term` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An atom's entropy and free energy depend on its electronic partition function, so a lowest degeneracy no ground term gives changes S(298 K) by several J/mol/K. It warns and does not block because the degeneracy is compared with a reference table (an expectation about a result), an atom in a non-ground configuration is a legitimate deposit, and the deposit is kept as sent. Absence of levels is a separate, unregistered absence warning.

**Enforced at.**

- `_degeneracy_warnings` — `backend/app/services/atomic_electronic_warnings.py::_degeneracy_warnings`
- `term_symbol_warnings` — `backend/app/services/upload_reconciliation.py::term_symbol_warnings`
  *The ground-term half; the multiplicity half is entry 12's.*

**Escape hatch.** None needed: the deposit is accepted. Deposit the levels that match the declared term.

### 80. The job named as having measured an SCF stability verdict ran at the level of theory of the calculation carrying the verdict, and the two can be shown to be on one conformer.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `scf_stability_source_level_mismatch`, `scf_stability_source_conformer_unverified` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008 |

**Why this tier.** An expectation and an absence, so a warning. A producer may legitimately run the stability check at a cheaper level and say so, which is worth telling the depositor but not a reason to refuse the record. When neither calculation is tied to any conformer the same-conformer rule cannot be checked, and an incomplete record is still a true record; exactly one side unanchored is a refusal, and a different conformer is entry 57's refusal.

**Enforced at.**

- `link_scf_stability_sources` — `backend/app/services/scf_stability_sources.py::link_scf_stability_sources`

**Escape hatch.** None needed: the link is stored as stated.

## Thermo declarations

### 81. A thermo declaration the file format defines describes what the format defines: a target declared by the CHEMKIN format is an equilibrium ensemble, on a gas-phase record that carries a NASA-7 block.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_declared_by_target_incoherent`, `thermo_declared_by_record_incoherent` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |
| **Governing ADR** | 0008 |

**Why this tier.** Definitional. A CHEMKIN NASA-7 polynomial is the equilibrium-ensemble thermochemistry of a gas-phase species, so a depositor cannot make the format declare a single conformer, a liquid, or a record with no polynomial. The depositor's own declarations are unrestricted: the rule applies only where the format is the declarer.

**Enforced at.**

- `thermo_target_error` — `schemas/python/tckdb-schemas/tckdb_schemas/thermo_declarations.py::thermo_target_error`
- `_declared_by_record_error` — `schemas/python/tckdb-schemas/tckdb_schemas/thermo_declarations.py::_declared_by_record_error`

**Escape hatch.** Declare the target as depositor-defined, or send a gas-phase record with its NASA-7 block.

## Hessian method of a stored tolerance

### 82. A recorded finite difference of gradients on an ORCA NumFreq deck, for a method with no analytic gradient, is stored as finite differences of energies, and the record says so.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `hessian_method_refined` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| **Governing ADR** | 0008, 0012 |

**Why this tier.** Not a contradiction with the deck (the sibling of entry 50): NumFreq differentiates gradients, and for a method with no analytic gradient those gradients are themselves numerical, so the recorded label and the deck agree about the program's procedure and differ only in what the label means. The tolerance stored with the record is the looser one for energies; the deposit is kept and the refinement is stated.

**Enforced at.**

- `_refinement_findings` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::_refinement_findings`

**Escape hatch.** None needed: the deposit is accepted. Record finite_difference_energy to state it directly.

---

Declarations live beside the checks they describe; see `backend/app/scientific_checks/__init__.py` for how to add one, and `backend/app/scientific_checks/declarations.py` for the two populations that cannot self-declare (PostgreSQL objects, and wire schemas that are forbidden from importing the backend).
