# Changelog

## 0.98.0 - 2026-10-04

The contract's "What changed" prints only the newest entries. Every entry now ships beside it as
`tckdb_schemas/contract/CHANGELOG.md`, which `--since` and `contract.changes_since` read. No upload model changed.

## 0.97.0 - 2026-10-04

New refusal code `kinetics_selection_population_too_large` (a selection request over 500 visible records).

## 0.96.0 - 2026-10-04

Kinetics records can declare their determination, applicability and protocol (`tckdb_schemas.kinetics_declarations`),
all optional, on `KineticsUploadRequest` and `BundleKineticsIn` (which also gains `direction`). `reaction.reversible`
on `/uploads/kinetics` is optional: unstated is inherited or refused, never guessed (#598). New `kinetics_*` refusal codes and
`unknown_kinetics_determination_ref`, `unknown_network_channel`. A determination may carry a bundle-local `group`.

## 0.95.0 - 2026-10-04

A network state energy can name one source calculation per participant, and is held against their sum
(#678). One new optional field on `POST /uploads/networks/pdep`, one new block-tier refusal and two new
upload warnings. Existing payloads are accepted as before.

- **`solve.state_energies[].source_calculation_keys`** (new, optional): a list of
  `{species_key, calculation_key}`, one per participant of the state. Send it or the older
  `source_calculation_key`, never both (a plain 422). Each calculation passes the existing
  `network_energy_source_type_mismatch` type rule and must belong to *exactly* the participant it is
  listed beside, otherwise `network_energy_source_subject_mismatch`; a `species_key` that is not a
  participant of the state is refused the same way, at `...source_calculation_keys[j].species_key`.
  `source_calculation_key` is unchanged and still accepts the calculation of any one participant.
- **`energy_precision_kj_mol`** (new, optional, positive): the rounding unit of `energy_kj_mol`. State
  unrounded kJ/mol derived from hartree with 2625.499639, or state this field; precision is never
  inferred from the digits of the number. It is stored and read back.
- **`network_state_energy_sum_mismatch` (422, block).** When every participant of a state has a source,
  TCKDB compares the stated `energy_kj_mol` with `sum(stoichiometry * stored energy)` in three bands.
  Within the printed-precision tolerance `max(1e-6, 5e-7 * n)` hartree, `n = 1 + sum(stoichiometry)`, the
  energy **agrees**. Beyond it but within an honest-rounding allowance (half `energy_precision_kj_mol`,
  or half of 1 kcal/mol = 2.09 kJ/mol when not stated, per stated energy, plus `1e-6 * |energy|` on an
  absolute energy for the spread of hartree-to-kJ/mol constants) it is stored **not compared**
  (`stated_precision_unknown`) with a warning, so a value rounded to 0.1 kJ/mol, converted from kcal/mol
  to two decimals, or converted with 2625.5 or 627.509 x 4.184 is never refused; if you state
  `energy_precision_kj_mol` the rounding is accounted for and it agrees. Only beyond the allowance is it
  refused. Defined for `correction_convention: electronic_only` (an `sp`, `opt` or `composite`
  energy) and, only where every source is a `composite` storing an E0, `electronic_plus_zpe`.
  `energy_zero_convention: absolute` is compared directly. `lowest_state` and `entrance_channel` shift
  every state of the solve by one constant, so those are compared as *differences* between states
  (`n = 2 + sum(nu_i) + sum(nu_j)`); the state blamed is the outlier inconsistent with a majority of the
  others, and with two states `context` names both. `context` names the field, the state and both
  numbers; no database id.
- **`network_state_energy_sum_not_compared` (warning).** A sum that cannot be formed is never refused
  and never guessed: `atom_and_bond_corrected`, `thermal_enthalpy_298k` and `other` corrections
  (`convention_not_summable`), `electronic_plus_zpe` with an `sp`/`opt`/`freq` source
  (`zpe_not_in_source`), `separated_reactants` and `other` zeros (`energy_zero_not_comparable`), a
  source that stores no such energy (`stored_energy_not_stated`), a state alone on its shared zero
  (`no_second_state_on_the_same_zero`), and a stated number beyond printed precision but inside honest
  rounding (`stated_precision_unknown`). The outcome is stored with its reason. A state with no source
  at all is stored as not compared (`no_source_stated`) without a warning: nothing was claimed.
- **`network_state_energy_sources_partial` (warning).** A source on some but not all of a state's
  participants, which includes a single `source_calculation_key` on a multi-species state. It is
  accepted and read back as partial; no other participant's source is borrowed.
- **Reads.** `NetworkSolveStateEnergySummary` gains `sources[]` (`species_entry_ref`, `stoichiometry`,
  `calculation_ref`), `partial_sources`, `source_sum_comparison` (`agrees`, `not_compared` or null for a
  row deposited before the check), `source_sum_not_compared_reason` and `energy_precision_kj_mol`. `source_calculation_ref` is the
  older single slot, one summand of several on a multi-species state. Stored networks read as they did,
  with the new fields describing what they hold.
- Producers: the hydrazine ingester now cites every participant's single point for a multi-species state
  (`source_calculation_keys`) and a single participant's with `source_calculation_key`. A list on a
  one-participant state also fills the older single slot, so `source_calculation_ref` keeps the source.


## 0.94.0 - 2026-10-04

A Hessian's geometry, every scan point's geometry and every IRC point's geometry are now checked against the
subject their calculation is filed under (#680). No payload field is added, removed or changed.

- **Payloads that were accepted are now refused.** A wrong-element or wrong-isotope geometry at
  `hessian.geometry`, at `scan_result.points[N].geometry` or at `irc_result.points[N].geometry` (any point
  direction, including the TS-marker point and `both`) is refused with the existing codes
  `calculation_geometry_composition_mismatch` (422) and `calculation_geometry_isotope_mismatch` (422), the
  same ones already raised for input and output geometries; `context.field` names the path. Before, such a
  geometry was stored: a deuterated Hessian geometry under a protium species gave deuterium frequencies on
  reanalysis. The contract's per-route refusal lists are unchanged (the codes were already listed for these
  routes).
- A Hessian recovered from an uploaded artifact is checked too; there a mismatch never fails the upload, the
  Hessian is simply not stored.
- A species stored before the label-stripping change with isotope labels on its SMILES and no isotope key is
  read through that SMILES when its isotope content is compared.

## 0.93.0 - 2026-10-04

The same molecule with its atoms listed in another order is no longer a way round the no-optimisation
duplicate rule (#679, follow-up to #667). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are one duplicate whatever order
  the atoms are listed in (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` (or two `composite`) links whose geometries are one structure
  moved rigidly are refused with `thermo_role_duplicate` / `statmech_role_duplicate` even when one lists
  its atoms in a different order, for example water with its hydrogens first, or the hydrogens of a CH3
  exchanged. The rule searches for a relabelling that lays one geometry on the other, using the
  geometries alone, and then applies the 0.80.0 comparison unchanged (Kabsch-aligned RMSD, tolerance from
  the precision of the coordinates). Only atoms of the same element and the same stated isotope are
  exchanged (`D`/`T` count as 2H/3H), and an enantiomer stays a different structure in any order.
- **The search is bounded, and past a bound a pair is treated as different** (the behaviour before this
  change): geometries over 200 atoms, a pair needing more than 256 trial alignments or examining more
  than 50,000 candidate placements, and a record's work beyond 4,096 alignments or 50 million work units
  are not decided. A duplicate energy on a reordered copy of such a structure is therefore still
  accepted. Ordinary molecules need one alignment.
- The same-order comparison of 0.80.0 and the one-atom rule of 0.74.0 are unchanged.

## 0.90.0 - 2026-10-03

No wire model changes. Contract notes the read-only thermo select endpoint.

## 0.89.0 - 2026-10-03

A `D`/`T` element spelling now means 2H/3H (#672, ADR 0022): **a `D`/`T` geometry on a protium species, previously accepted, is now refused**, `[2H]O[2H]` with it is accepted, and `isotopes` contradicting the spelling is refused as `geometry_isotope_symbol_conflict`.

## 0.87.0 - 2026-10-03

A geometry linked to a calculation is now checked against the isotopes of the subject the calculation is
filed under (#666). No payload field is added, removed or changed.

- New refusal code `calculation_geometry_isotope_mismatch` (422, ADR 0008 block), on every route that
  already runs `calculation_geometry_composition_mismatch`: thermo, statmech, conformer, computed
  species, computed reaction, transition state and network. The multiset of `(element, mass number)`
  substitutions on each calculation geometry (`geometry.isotopes`) must equal the one the species entry
  declares through its SMILES isotope labels, or, for a transition state, the sum over its reaction's
  reactants. Before, a deuterium geometry could be attached to a protium species and the reverse.
- The comparison is by count, not by atom: a calculation geometry carries no map to the species graph,
  so which atom carries a label is not checked. A `D` or `T` element spelling stays isotope-silent, as
  documented in `resolve_element_symbol`; only `geometry.isotopes` and SMILES labels count.
- Network and computed-reaction uploads: a transition state's `geometry.isotopes` is now read. It was
  accepted by the schema and dropped before the geometry was stored (contribution bundles take the
  computed-reaction route).
- A deposit whose calculation geometries carry no isotope labels under a species that declares some
  (for example `[2H]C` with an unlabelled CH4 opt input) is now refused; label the geometry.

## 0.86.0 - 2026-10-03

The layout of `PRODUCER_CONTRACT.md` changed; its content did not (#681). No field, rule, code or enum
changes. What a rule, a refusal code or a nested model says is now printed once and linked from each
surface that has it, instead of once per surface. Every surface still lists exactly the same refusal
codes, checks and nested models: a code or model several surfaces share sits in a numbered group
("Code group N", "Model group N") that each of those surfaces links, and a "Will be refused if" line
for such a code no longer repeats its sentence (the code reference has it).

## 0.85.0 - 2026-10-03

A thermo record can state what its values describe (`thermodynamic_target`) and how they were
produced (`protocol`, version 1): optional attributed claims, never inferred or defaulted, `null`
when omitted. Existing payloads are accepted unchanged. Fields and rules: `backend/schema_spec.md`,
"Thermodynamic target and protocol declarations". New codes (`thermo_target_group_*`,
`thermo_protocol_*`, `thermo_recipe_name_listed`, `thermo_declaration_invalid`) and the shared rule
`thermo_declaration_error`.

## 0.81.0 - 2026-10-03

TS energy-ordering energies are held against the stored energies (#638). No field is removed or changed;
`zpe_scale_factor` is added.

- **`ts_energy_ordering_stated_energy_mismatch` (422).** A stated energy that is not what TCKDB stores for
  its `source_calculation_key` is refused: `electronic` against the cited `sp`'s energy (or `opt`'s final
  energy), `e0` against the same participant's stored `electronic` energy plus the cited `freq`'s ZPE, when
  both are at one geometry. Tolerance `max(1e-6, 5e-7 * n)` hartree, n = 2 (electronic) or 3 (E0).
- **`zpe_scale_factor` (optional, `e0` energies only).** TCKDB stores your ZPE unscaled. An `e0` built as
  `E_electronic + s * ZPE` states `s` and is held to that sum (n = 2 + s + 100 * ZPE, which assumes `s` has
  at least four decimals: state it as multiplied, since 0.954 for a true 0.953649 can be refused). With no factor, an `e0` equal to `electronic + ZPE` agrees; any other is not refused
  but stored `not_compared` with reason `zpe_scaling_unstated`.
- **`transition_state_energy_ordering_not_compared` (warning).** An energy that cannot be compared (stored
  energy or ZPE not stated, no electronic energy to pair an E0 with, geometries not pairable) is accepted
  and reported, never read as agreement.
- Reads: compared energies gain `stored_energy_comparison`, `not_compared_reason` and `zpe_scale_factor`
  (null on earlier records). Uploads only.

## 0.80.0 - 2026-10-03

A rigidly moved copy of a polyatomic geometry is no longer a way round the no-optimisation duplicate
rule (#667, the polyatomic half of #623). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are now one duplicate, wherever
  the structure sits (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` links (or two `composite` links) whose geometries are the
  same structure moved rigidly, translated and/or rotated, are refused with `thermo_role_duplicate` /
  `statmech_role_duplicate`; before, only the very same stored geometry was. "The same structure" is
  the same atoms in the same order (element and stated isotope) whose Kabsch-aligned RMSD is within
  the rounding of the coordinates as deposited (1.7e-6 Angstrom for coordinates written to six
  decimals, never more than 1.7e-4 Angstrom). An enantiomer is a different structure (a mirror image counts as the same only when a rotation superposes it atom for atom). A genuinely
  different geometry, such as another conformer or a bond length changed by more than the rounding,
  is unchanged and still accepted. The same atoms listed in a different order are still treated as
  different geometries (no canonical atom order exists for a bare geometry; deferred).
- The one-atom rule of 0.74.0 is unchanged.

## 0.79.0 - 2026-10-03

Network solve energy sources must belong to the subject they state an energy for (#668). No field or
enum member changes; the `source_calculation_key` description of a state energy and of a channel
barrier gains a sentence, and one new refusal code joins the catalogue.

- **`network_energy_source_subject_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source
  cited for the wrong subject: a `state_energies[].source_calculation_key` that is not a calculation of
  a species of that state (any one of them for a bimolecular state), a `channel_barriers[]` source that
  is not a calculation of the barrier's own transition state (a species single point is the case that
  used to be stored), a `well_energy` link that is not a calculation of a species in one of the
  network's states, and a `barrier_energy` link that is not a calculation of one of its transition
  states. `context` names the field, the expected and the found kind of owner and, for a state or a
  barrier, the key of the state or transition state; no database id. Producers that cite the single
  point of the species in the state, or of the transition state (every producer known to us, the
  hydrazine ingester included) are unaffected. Uploads only: stored networks read exactly as before.

## 0.77.0 - 2026-10-03

Composite levels of theory, phase P7b (ADR 0021): a read-shape note only. No upload payload field is
added, removed or changed, and every payload accepted before is accepted unchanged.

- Reads: `LevelOfTheorySummary.label` is now always the full rendered label of the level, a string,
  never `null`: `method/basis` then each stated part of its identity, for example
  `CCSD(T)/cc-pCVTZ (core=all_electron)`. It is the same text as the notation of a record's levels and
  the ML export's label. Before, it was `null` on most reads and a slash-joined `method/basis` on a few,
  so a caller that treated it as the short form should read `display` instead (unchanged: `method/basis`).
- Reads: `LevelOfTheoryCoreBlock.label` is new, with the same text. The level-of-theory detail and browse
  reads carry it.
- The producer contract is unchanged except for its version line and this entry.

## 0.76.0 - 2026-10-03

Network solve energy sources are typed (#642). No field or enum member changes; the `source_calculation_key` of a state energy and of a channel
barrier gains a description, and one new refusal code joins the catalogue so a producer branching on
codes can learn it.

- **`network_energy_source_type_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source that
  cannot carry the energy it is cited for: `state_energies[].source_calculation_key` and
  `channel_barriers[].source_calculation_key` when stated as `correction_convention: electronic_only`
  (accepted from `sp`, `opt` or `composite`), and, for any convention and for the `well_energy` and
  `barrier_energy` roles of `source_calculations`, any calculation that is not an `sp`, `opt`, `freq` or
  `composite` (so an `irc`, `scan`, `path_search` or `conf` is refused). `context` names the field, the
  stated convention or role, the accepted types and the type found. Producers that cite a single point
  (every producer known to us) are unaffected. Uploads only: stored networks read exactly as before.

## 0.75.0 - 2026-10-03

The producer contract now records that `/bundles/submit` and `/bundles/dry-run` apply the
frequency-list linearity check to the records in a bundle, and that a bundle reports the upload
warnings each record earns. Nothing a producer sends changes and every payload accepted before is
accepted unchanged; this release only adds.

- **Bundle warnings.** `/bundles/submit` and `/bundles/dry-run` return, in `messages`, the same
  upload warnings (code, message, field, order) that `POST /uploads/thermo` and
  `POST /uploads/kinetics` return for the same record, each with the upload's `local_ref`.
  The producer contract's rule listing gains `ContributionBundleV0` as a place the linearity check
  applies.

## 0.74.0 - 2026-10-03

A shifted copy of an atom is no longer a way round the no-optimisation duplicate rule (#623).
No field is added, removed or renamed; one payload that was accepted is now refused, and one
refusal changes its code.

- **Two single points (or two composites) on one atom are now one duplicate, wherever the atom sits
  (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes change).** With no
  `opt` linked, two `sp` links (or two `composite` links) on one structure are refused with
  `thermo_role_duplicate` / `statmech_role_duplicate`. For a geometry of exactly one atom the
  structure is the element (`D` and `T` count as hydrogen) and its stated isotope mass number, not the
  stored geometry: a second `sp` on a copy of the atom at another
  coordinate (`1\nH\nH 1.0 0.0 0.0` beside `1\nH\nH 0.0 0.0 0.0`) used to be a different geometry and
  returned 201, and is now refused. A geometry of two or more atoms is still its own structure, so
  two `sp` links on genuinely different polyatomic geometries are unchanged. Two single points at two
  levels on a shifted atom were never accepted: they used to be refused as
  `*_energy_level_ambiguous` and are now refused as `*_role_duplicate`, which is what the same
  geometry at two levels has always returned. The message for an atom now says "per atom".
- **The rule is stated for polyatomic sp-only links too.** It has always applied to a polyatomic
  species whose linked calculations are `sp`s with no `opt`, including on the standalone
  `/uploads/thermo` and `/uploads/statmech` routes; the workflow guide `depositing_a_thermo_record.md`
  now says so.
- **The producer contract no longer lists `atom_map_geometry_unparseable` against surfaces that only
  count atoms.** The atom counter used by the frequency-list and one-atom-primary rules swallowed
  that refusal internally but was traced as if it raised it, so eight surfaces (computed species,
  conformer, kinetics, network pressure-dependent, reactions, statmech, thermo and transport) named a
  code they cannot return. It is still listed where the atom-map rules raise it: the computed-reaction
  bundle and the transition-state upload. `/uploads/reactions` no longer lists it. Nothing a producer
  sends changes for this item.
- `xyz_block_shape` is new in `tckdb_schemas.fragments.reaction_atom_map`: it splits an XYZ block into
  its declared atom count and coordinate lines and never raises. `parse_xyz_elements` is built on it
  and behaves as before.

## 0.73.0 - 2026-10-03

Composite levels of theory, phase P7a (ADR 0021): the wire package gains the closed-form
coefficients of the linear extrapolation formulas. Nothing a producer sends changes and every
payload accepted before is accepted unchanged; this release only adds.

- **`composite_formulas.extrapolation_coefficients(formula, cardinals, exponent)`.** The signed
  weights `c_i` with `E_CBS = sum_i c_i * E_i` for the linear formulas (`inverse_power`,
  `inverse_power_shifted_half`, `karton_martin_scf`), in ascending cardinal order. They sum to 1 and
  their absolute values are what `extrapolation_weights` already returned. For
  `exponential_three_point`, whose limit is a ratio of differences of the energies and so has no fixed
  weights, it returns `None`; a degenerate or mis-shaped input raises `ExtrapolationError` like its
  siblings.
- **`composite_formulas.is_linear_formula(formula)`.** `True` for the three two-point formulas,
  `False` for `exponential_three_point`.
- The producer contract is unchanged except for its version line.

## 0.72.0 - 2026-10-02

Composite levels of theory, phase P5 (ADR 0021): a composite energy you build
yourself, from your own recipe and the single points you ran, is deposited as an
`assembled` composite. Every payload accepted before is accepted unchanged; this
release only adds. See the "Worked payloads" section of the producer contract
for two complete examples (CCSD(T)/CBS from a TZ/QZ pair, and a focal-point sum).

- **`level_of_theory.composite_scheme`.** A level of theory now carries exactly
  one of `method` and `composite_scheme`. `method` stays required whenever
  `composite_scheme` is absent, so every existing payload is unchanged;
  `method` is now optional in the schema only because the other member exists.
  Both, or neither, is refused (`level_of_theory_method_with_composite_scheme`,
  `level_of_theory_requires_method_or_composite_scheme`). The definition:
  `kind` (`extrapolation` or `additive`; `named_method` is the server's own and
  is refused with `composite_scheme_named_method_not_sendable`), `terms[]` and an
  optional `literature`. A term has a local `key`, an `operation` (`base`,
  `value`, `extrapolation`, `difference`; `empirical` is refused), an
  `energy_component`, a `formula` and `exponent` on an extrapolation, and
  `inputs[]`, each a `slot` (`value`, `high`, `low`, `cardinal`), an ordinary
  `level_of_theory` and a declared `cardinal_number`. The total is the sum of the
  terms. Formulas: `inverse_power` and `inverse_power_shifted_half` (with an
  exponent), `karton_martin_scf` and `exponential_three_point` (without). Shape
  mistakes are `composite_scheme_malformed` with `context.rule` naming which; a
  nested composite, or a named composite method such as CBS-QB3 used as an input
  level, is `composite_scheme_nested`.
- **Identity.** The server resolves the input levels, hashes the definition and
  names the level of theory itself (for example
  `CBS[ref:CCSD(T)/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]`).
  Formula, exponent, cardinal numbers, the input levels (with `core_treatment`)
  and the term order are identity; term keys and literature are not.
- **Assembled composites.** `composite_result.assembly: "assembled"` is accepted
  with `inputs[]`: each names a `term_key`, a `slot` (and `cardinal_number` on an
  extrapolation) and the calculation by a bundle-local `calculation_key` or a
  `calc_...` `calculation_ref`, never a database id. `inputs` on a `program_run`
  is refused (`composite_inputs_require_assembled`); an assembled composite at a
  level that carries no inline scheme keeps the code
  `composite_assembled_not_accepted`, now meaning exactly that.
  `software_release` is optional on an assembled composite and required on every
  other calculation (`calculation_software_release_required`, a coded refusal in
  place of the schema's missing-field error); a `program_run` composite still
  needs it.
- **Server-side refusals** (422): `composite_input_missing`,
  `composite_input_slot_unknown`, `composite_input_duplicate`,
  `composite_input_reference_invalid`, `composite_input_edge_is_derived` (the
  `composite_input` dependency role is written by the server and cannot be
  declared in `depends_on`), `composite_input_type_invalid` (an input is not a
  single point or optimisation), `composite_input_level_mismatch`,
  `composite_input_owner_mismatch`, `composite_input_geometry_mismatch`,
  `composite_total_required` (an assembled composite deposits its total; there is
  no unverifiable-by-absence), `composite_assembled_cannot_be_primary` (an
  assembled composite is not the run that produced a geometry; send it as an
  additional calculation) and `composite_total_mismatch` (the deposited total is not
  what the scheme gives for the inputs' stored energies, beyond
  `max(1e-6, 5e-7 * (1 + sum |weight|))` hartree, the sum over the stored numbers
  consumed: weight 1 for a value, base or difference input, the extrapolation's own
  weight for an extrapolated one, so `max(1e-6, 5e-7 * n)` when every weight is 1).
  TCKDB recomputes the total only to check it; it never stores the recomputed value.
  Warnings: `composite_input_geometry_undeclared` and `composite_total_unverifiable`
  (a needed energy or component is not stated, the correlation convention cannot be
  determined, or an extrapolation is degenerate).
- **Correlation and triples.** A `correlation` term reads the whole correlation
  energy, (T) included: the stored `correlation` where it already includes (T)
  (ORCA: `reference + correlation` equals the energy), `correlation + triples`
  where triples are stored separately (Molpro). The new
  `EnergyComponentKind.correlation_excluding_triples` reads the CCSD part: the
  stored `correlation` under Molpro's convention, `correlation - triples` under
  ORCA's. The textbook scheme (CCSD correlation extrapolated, (T) at a smaller
  basis as its own `triples` term) is written with it and counts (T) once. It is
  derived and never a stored single-point component (`sp_energy_component_derived`).
  The convention is read off the stored row; where it cannot be read the total is
  unverifiable, never guessed.
- **Term order is not identity.** The total is a sum, so terms sent in any order are
  one scheme; stored positions are canonical and the positions in
  `composite_result.terms` are mapped to them. A `cardinal_number` on a slot that is
  not a `cardinal` slot is refused (`composite_scheme_malformed`,
  `rule: cardinal_on_non_cardinal_slot`).
- **New public modules.** `tckdb_schemas.composite_formulas` (the four formulas,
  checked against the correlation energies printed in the ORCA manuals),
  `composite_total` (the recomputation, pure arithmetic a producer can run before
  sending) and `composite_worked_examples`.
- **New enums mirrored from the server:** `CompositeSchemeKind`,
  `CompositeTermOperation`, `CompositeExtrapolationFormula`,
  `CompositeInputSlot`, and `CalculationDependencyRole.composite_input`.

## 0.71.0 - 2026-10-01

Composite levels of theory, phase P3b (ADR 0021): a `composite` calculation's
deposited energy is compared with the Gaussian output log attached to it. No
payload field changes and every payload accepted before is accepted unchanged;
this release only adds three warnings to upload responses.

- **New warning `composite_energy_log_available`** (informational): the
  `composite_result` stated no energy and the attached log's summary block
  states E0 and the recipe ZPE. Nothing is filled; the message carries the
  numbers so the producer can send them.
- **New warnings** (the upload is accepted; the deposited values are kept
  exactly as sent, and nothing is filled from the log):
  `composite_energy_log_mismatch` (an attached Gaussian output log's summary
  block states a different `e0_hartree`, `recipe_zpe_hartree` or
  `electronic_energy_hartree` than the `composite_result`, beyond printed
  precision, `max(1e-6, 5e-7 * n)` hartree) and `composite_log_method_mismatch`
  (the log is a different composite method from the calculation's level of
  theory; the energies are then not compared).
- **What ARC (or any producer) must attach:** the Gaussian output log of the
  composite run as an `output_log` artifact on the `composite` calculation.
  Logs of CBS-QB3, ROCBS-QB3, CBS-4M and G3 are read; other composite methods,
  G4 and G4MP2 included, are not compared (no warning either way). G4 and G4MP2
  are declined because the Gaussian 16 Rev A.03 summary labels are shifted by one
  pair, so the number under `G4(0 K)` is not E0.
- An `sp` at a composite level whose attached log is a composite job still gets
  no single-point energy, as before.

## 0.70.0 - 2026-10-01

Composite levels of theory, phase P3a (ADR 0021): a calculation of type
`composite` records one program-run composite energy such as CBS-QB3 or G4.
Every payload accepted before is accepted unchanged; this release only adds.

- **New calculation type `composite`, with a `composite_result` block.**
  `type: "composite"` is accepted on the conformer upload, the thermo and
  statmech uploads' inline calculations, and the computed-species and
  computed-reaction bundles (the shared bundle-local calculation shape that the
  network upload also uses carries the block too, but that route has no test
  for it yet). It carries `composite_result`: `assembly`
  (`program_run`, or `assembled`, which the server refuses until user-built
  schemes arrive), the optional energies `electronic_energy_hartree` (ZPE-free,
  every recipe term included), `e0_hartree` (0 K, including the recipe's scaled
  zero-point energy) and `recipe_zpe_hartree`, and an optional `terms` list of
  `{term_position, value_hartree}`. A `null` energy means not stated. TCKDB never
  stores a total it computed itself.
- **The two always come together.** `composite_result` on any other type is
  refused with `composite_result_requires_composite_type`, and a `composite`
  calculation without it with `composite_type_requires_composite_result`.
- **Two arithmetic checks, blocking, to printed precision.** The tolerance is
  `max(1e-6, 5e-7 * n)` hartree, where `n` counts the rounded numbers in the
  equation (Gaussian prints each to six decimals): 3 for e0, `len(terms) + 1`
  for the terms. With all three energies
  present, `e0_hartree` must equal `electronic_energy_hartree +
  recipe_zpe_hartree` (`composite_e0_inconsistent`); with terms given and the
  total present, the terms must sum to `electronic_energy_hartree`
  (`composite_terms_do_not_sum`).
- **Server-side refusals** (422): `composite_assembled_not_accepted` (the
  assembled form arrives with user schemes), `composite_level_not_scheme_bound`
  (the level of theory must be a catalogued named method such as `CBS-QB3`),
  `composite_program_run_requires_software`, and
  `composite_term_position_unknown` (a term names a position the scheme does not
  have; a named method has none, so any position is accepted there).
- **A composite may be a conformer's primary calculation.** A species of two or
  more atoms may send, as `primary_calculation` / `calculation`, a program-run
  named composite that produced the geometry, in place of the `opt` (the `opt`
  rule is otherwise unchanged). With no `output_geometries` declared, the
  conformer's geometry is its final output, as for an `opt`.
- **A freq, sp or scan may depend on a composite that has an output geometry**
  (`depends_on` role `freq_on`, `single_point_on`, `scan_parent`), as on an `opt`.
- **Two energies refused.** A statmech or thermo record that links both an `sp`
  and a `composite` as its energy is refused with
  `statmech_energy_sp_and_composite_linked` /
  `thermo_energy_sp_and_composite_linked`. The existing role-consistency rules
  (`*_role_duplicate`, `*_sp_geometry_mismatch`, `*_energy_level_requires_sp`,
  `*_energy_level_ambiguous`, `*_energy_level_contradiction`) now apply to a
  linked `composite` as to an `sp`, and the energy level of a record is the
  composite's when one is linked (composite, then sp, then opt, then imported).
- **New warnings** (the upload is accepted): `named_composite_deposited_as_opt`
  and `named_composite_deposited_as_sp` (an `opt` or `sp` at a named composite
  method's level: send it as `composite`; refused once producers can),
  `composite_role_on_non_composite_calculation` (the source role `composite` on
  a calculation of another type) and
  `composite_frequency_level_differs_from_recipe` (a linked `freq` at a level
  other than the composite scheme's own frequency level).
- **Reads:** a calculation read returns `composite` (assembly, energies, terms)
  under `results`; a levels summary gains `geometry_source` and
  `frequency_source` (`composite_recipe` when the level is the named method's own
  internal level). Server responses, not producer payloads.
- Not in this release: assembled composites and user-built schemes, energy
  components on a single point, a Gaussian composite-log parser.

## 0.69.0 - 2026-10-01

Composite levels of theory, phase P4 (ADR 0021): energy components on single
points, and a core-treatment field on the level of theory. Both are additions;
every payload accepted before is accepted unchanged and means the same thing.

- **`sp_energy_components[]` on single points.** `CalculationWithResultsPayload`,
  the computed-species `CalculationInBundle` and the flat `CalculationIn` (the
  computed-reaction and network bundles) take a list of
  `{component, value_hartree}`, where `component` is one of `total`,
  `reference` (the SCF / HF energy), `correlation`, `triples`, `dboc` or
  `scalar_relativistic`. The value is what the program printed. Five refusals,
  each with a code and context:
  - `sp_energy_component_not_on_sp`: the calculation is not a single point.
  - `sp_energy_components_require_energy`: the single point's electronic energy
    is not stated. The parts come from the same output as the energy, so state
    it; a log can no longer fill the energy in after the parts were checked.
  - `sp_energy_component_duplicate`: one value per component.
  - `sp_energy_component_total_mismatch`: a `total` must equal the energy
    within 1e-6 Eh.
  - `sp_energy_components_do_not_sum`: `reference + correlation` must equal the
    energy within 1e-6 Eh. When a `triples` component is also sent,
    `reference + correlation + triples` may match instead (ORCA's correlation
    energy already includes (T); Molpro prints CCSD and (T) separately). The
    refusal reports both sums. On F12 methods, `reference` must include the
    CABS-singles correction if the program's total does.

  The server compares and never stores a value it computed. A single-point read
  returns the components under `results.sp.energy_components`.
- **`LevelOfTheoryRef.core_treatment`** (optional): `frozen_core` or
  `all_electron`. State it only when the run says so. It is part of the level's
  identity **only when stated**, so a payload that omits it resolves to exactly
  the level it always did. Frozen-core and all-electron CCSD(T)/cc-pCVTZ are now
  two levels instead of one. A partial treatment (an energy window, Gaussian's
  `FC=1`) has no value yet; leave the field out and describe it in `keywords`.
- New public enums `CoreTreatment` and `EnergyComponentKind`.
- Reads: `LevelOfTheorySummary` and the level-of-theory detail carry
  `core_treatment` (`null` = not stated).

## 0.68.0 - 2026-10-01

Composite levels of theory, phase P2 (ADR 0021): the server now records the
recipe behind a named composite method. No upload payload field is added,
removed or changed, and every payload accepted before is accepted unchanged.

- **A level of theory that names a catalogued composite method is bound to its
  recipe.** `CBS-QB3`, `G4`, `W1U` and the other methods in the server's
  catalogue (`cbsqb3` and the other aliases reach the same one) now resolve to a
  level of theory bound to a `named_method` composite scheme. The level's hash
  and ref are unchanged, so a level sent before and after is the same row. A
  method that is not in the catalogue is bound to nothing.
- **Reads gain the recipe.** `GET /scientific/composite-schemes/{ref}` returns a
  scheme (`csch_...` ref), and every level-of-theory summary on a read carries
  `composite_scheme` (`{composite_scheme_ref, kind, name}`), `null` for an
  ordinary level. These are server responses, not producer payloads.
- **New public-ref prefix `csch_`** (content-derived: the same recipe has the
  same ref on every instance).
- Not in this release: sending a scheme of your own, the `composite`
  calculation type, and energy components. Those are later phases and change
  the upload contract when they land.

## 0.67.0 - 2026-10-01

`level_of_theory.method` guards for named composite methods (ADR 0021). Every
payload that was accepted and meant one method is unchanged.

- **`//` is refused in `method`.** `LevelOfTheoryRef.method` containing `//`
  (ARC's `energy//geometry` shorthand, `"ccsd(t)-f12/cc-pvtz-f12//b3lyp/def2tzvp"`)
  is refused with code `level_of_theory_method_is_compound` and `context`
  `{field: "method", value}`. It is two levels of theory, not one method: send
  the single-point and the optimization levels as separate calculations, each
  with its own level of theory. A single `/` is still accepted.
- **Correction-table names warn.** A named composite method followed by a
  correction-table label or a year (`cbs-qb3-paraskevas`, `cbsqb32023`) is
  accepted with an upload warning `level_of_theory_method_names_correction_table`
  at `...level_of_theory.method`. These names select Arkane AEC/BAC parameters,
  not a method: the calculation that ran is CBS-QB3. The name is stored as sent,
  as a separate level of theory from the method, and is never aliased. Send the
  method and name the table on the energy correction scheme. A later release
  will refuse these once the producers send the method.
- **New helpers.** `collect_ref_warnings` walks a validated request and returns
  the software-release version warnings and these method warnings together;
  `collect_software_release_version_warnings` is unchanged.
  `correction_table_method_stem` recognises the shape.

Server side, in the same change: `cbsqb3`, `rocbsqb3`, `cbs4m` and `cbsapno`
now key to the hyphenated spellings, and `g4(mp2)`, `g3(mp2)` and `g3(mp2)b3`
to `g4mp2`, `g3mp2` and `g3mp2b3`, so a level of theory written either way is
one row. `W1`, `W1U`, `W1BD` and `W1RO` stay four methods and `CBS-QB3` and
`ROCBS-QB3` stay two. A producer that hashes level-of-theory identity locally
must adopt the same aliases to agree with the server.

## 0.66.0 - 2026-10-01

Frequency level on correction schemes (composite-levels plan P6). One optional
field, and one new warning. A payload written against 0.64.0 is still valid and
means what it meant.

- **`EnergyCorrectionSchemeRef.frequency_level_of_theory`**
  (`LevelOfTheoryRef | null`). The level of theory the frequencies were computed
  at, for a scheme keyed on an `energy//frequency` pair. Arkane keys Petersson
  and Melius BAC (and only those; atom energies are keyed on the energy level
  alone) on `CompositeLevelOfTheory(freq=..., energy=...)`; a scheme held only one level of
  theory, so the frequency half was lost and two such schemes either collapsed
  into one row (identical tables) or were refused as a value conflict (different
  tables). Send the `energy` half in `level_of_theory` as before and the `freq`
  half here. It joins scheme identity, in both identity forms (with and without
  `data_revision`): the same energy level with two different frequency levels is
  two schemes. An absent value is a value of its own: a scheme sent without it
  never matches one that has it, keeps exactly the identity it had before the
  field existed, and keeps its public ref byte for byte. It is resolved like
  `level_of_theory`, so a level that was merged into another resolves to the one
  that holds it. Adapters: for an Arkane `energy//freq`-keyed BAC, send both halves; for
  an atom-energy scheme or a scheme keyed on one level, send nothing new.
  Three rules, each stated where it applies: the field is **refused** on any
  kind other than `bac_petersson` and `bac_melius`
  (`energy_correction_scheme_frequency_level_not_applicable`) and without
  `level_of_theory` (`energy_correction_scheme_frequency_level_without_energy_level`),
  and a frequency level that resolves to the same level of theory as the energy
  level is **stored as absent**, so one table cannot become two schemes by
  spelling its level twice.
- **`composite_delta_prefer_scheme_terms` warning.** A new applied correction
  with `application_role = "composite_delta"` is stored as sent and answered
  with this warning (field `application_role`). Focal-point deltas
  (core-valence, higher-order triples, relativistic, DBOC) are going to become
  composite-scheme terms, which can name every calculation a delta is built from
  and sit on the energy instead of the species entry. It is a warning and not a
  refusal; no payload that was accepted is refused.

Scientific reads gain `frequency_level_of_theory` beside `level_of_theory` (a
level-of-theory summary, `null` when the scheme is keyed on one level) on the
scheme detail and search records.

## 0.65.0 - 2026-10-01

A single atom may be deposited with an `sp` primary on the pressure-dependent
network bundle too (#615). Every existing payload is unchanged.

- **`POST /uploads/networks/pdep`: a one-atom conformer may send its `sp` as
  `calculation`.** The two other bundle routes accepted this already (#610);
  the network route still required an `opt`, so the H atom of a hydrazine-style
  network could not be sent honestly. The rule is the same one: a conformer
  whose geometry is exactly one atom may carry `type: "sp"` as its primary, with
  `sp_electronic_energy_hartree`; an `sp` on two or more atoms, an uncountable
  geometry, or any type other than `opt`/`sp` is still refused with the same
  message. The atom's statmech `source_calculations` link that `sp` with role
  `sp`, and the solve's `source_calculations` and `state_energies` name it. A
  relabelled `opt` on an atom is still accepted. Transition states still require
  an `opt` primary. Only the producer contract's description of the network's
  conformer changed; no field was added or removed.
- **Duplicate single points are now refused when they declare output-only
  geometry (shared rule, so `/uploads/statmech` and `/uploads/thermo` change
  too).** With no `opt` linked, two `sp` links on one geometry are refused with
  `statmech_role_duplicate` / `thermo_role_duplicate`. The rule used to read an
  `sp`'s input geometry only; it now reads its input geometry, else its output
  geometry. A deposit whose `sp` declares only an output geometry on the same
  geometry as another linked `sp` therefore changes: the same level of theory
  used to return 201 and now returns the `*_role_duplicate` refusal; a different
  level used to return `*_energy_level_ambiguous` and now returns
  `*_role_duplicate`. This is what two input-linked `sp`s already got.

## 0.64.0 - 2026-10-01

Transition-state contract additions (#621). Every field is optional and
every existing payload is unchanged.

- **Two more kinds of transition-state validation evidence.**
  `TransitionStateValidationEvidenceIn.kind` was `"irc"` only. It now also
  accepts `"energy_ordering"` (the saddle point lies above both wells) and
  `"imaginary_mode"` (what the frequency calculation found). At most one record
  per kind. An `energy_ordering` record carries `energies`, one per participant
  (`"ts"`, `"reactant:N"`, `"product:N"`), each with an `energy_kind`
  (`"electronic"` or `"e0"`, which are never compared with each other), an
  `energy_hartree` and the `source_calculation_key` it was taken from, which
  must belong to that participant. An `imaginary_mode` record carries
  `imaginary_frequency_count`, `imaginary_frequency_cm1` (negative) and
  `mode_displacement_agrees` (your displacement check's verdict, null when not
  assessed). A pass that the record's own *stated* numbers contradict is refused, a
  field is refused on a kind it does not describe, and only a passing `irc`
  record silences `transition_state_missing_irc_evidence`. `energy_ordering`
  is accepted on the computed-reaction and pressure-dependent bundles and
  refused on the standalone transition-state upload, which has no
  calculations for the wells; `imaginary_mode` binds there to the single
  `freq` additional calculation. Energies are finite and not positive (absolute,
  in hartree; zero is exact for the bare proton), `imaginary_frequency_cm1` is finite, and the database refuses
  NaN and infinities too. An `electronic` energy must come from an `sp` or
  `opt` calculation and an `e0` from a `freq`; one energy kind taken at more
  than one level of theory is accepted with a
  `transition_state_energy_ordering_mixed_levels` warning. An `imaginary_mode`
  count or frequency that disagrees with the frequency result it cites is
  refused, and a pass with more than one imaginary mode needs that result to
  designate the reaction coordinate. Stated energies are not reconciled with
  the energies stored on the cited calculations.
- **Transition-state statmech on the reaction bundle.**
  `BundleTransitionStateIn` gains `statmech`, the block a species carries on
  the computed-species route, written through the same code, so the same rules
  apply (source roles, the three energy levels, scale-factor resolution).
  Its `source_calculations` and torsion scans must be the saddle point's own
  calculations. The response gains `transition_state_statmech_id`, and a
  transition-state entry read serves it under `include=statmech`.
- **Standalone transition-state route.** `additional_calculations` now accepts
  `scan`, and `CalculationWithResultsPayload` gains `scan_result` (type `scan`
  only) so the points travel with it. That payload is shared, so this is a
  cross-route addition: a route that already accepted a `scan` calculation
  (the conformer route does not restrict its primary calculation's type) now
  stores the scan's points, and a route that does not allow `scan` is unchanged. The request gains
  `applied_energy_corrections` (no source keys or frequency scale factor, since
  the payload has no key namespace), and `atom_map`, with a `key` and a
  `geometry` on each reaction participant and a `geometry_key` for the saddle
  point, because a map counts its indices into geometries.
- **An IRC result may leave its direction and flags unstated.**
  `IRCResultPayload.direction`, `has_forward` and `has_reverse` are now
  optional. Unstated is stored and read back as null, never as `false`; a flag
  stated `false` against points of that direction is still refused.

## 0.63.0 - 2026-09-30

The Arrhenius reference temperature, and the reaction bundle's kinetics block
now takes the same evidence as the standalone route (#620). Both are additions:
a payload valid under 0.62.0 is valid under this release, byte for byte.

**`t0_k`** on `BundleKineticsIn` (`POST /uploads/computed-reaction`) and on
`KineticsUploadRequest` (`POST /uploads/kinetics`): the reference temperature
T0 of the scalar rate, in K, meaning `k = A (T/T0)^n exp(-Ea/RT)`. It defaults
to 1 K, which is the plain `A T^n` form and what every record deposited
before this release meant. Before, a producer that fitted with T0 = 298 K had
to send `A / 298^n` and the T0 it fitted with was lost. It must satisfy
`0 < t0_k <= 10000`. It applies to the record's own `a`, `n` and `reported_ea`
of a modified-Arrhenius rate. Falloff (`lindemann`, `troe`, `sri`), `plog`,
`chebyshev` and `multi_arrhenius` records are always at 1 K and the standalone
route refuses any other `t0_k` on them: their low-pressure limit or child rows
carry no T0 of their own (Arkane and RMG give each falloff limit an independent
T0), so one value would silently mis-state part of the rate. The server stores `a` as sent (it is A at T0, not A rescaled) and
serves `t0_k` back; a consumer that evaluates k(T) from a stored `a`, `n` and
`ea_kj_mol` must use it.

**`interpretation_assignments`, `tunneling_application`, `network_kinetics_ref`**
on `BundleKineticsIn`. They are the standalone route's own models and cross-field
checks, now defined once in `tckdb_schemas.fragments.kinetics_evidence`
(`KineticsInterpretationAssignmentUpload`, `KineticsTunnelingApplicationUpload`,
`ConformerSelectionContentRef`), and the server writes them with the same
persistence code, so the two routes refuse the same interpretation and tunneling
mistakes with the same codes (a coded 404 for an unknown statmech, transition
state, calculation, artifact or network ref, the same 422 for an incomplete
interpretation set or a tunneling block that disagrees with `tunneling_model`).
Two things follow from how a bundle is built and are part of the contract:
every reference is the public ref of a record deposited *earlier* (a bundle
cannot cite a statmech, transition state or calculation it is itself creating,
because the depositor cannot know its ref in advance), and a cited transition
state must be a transition state of this rate's reaction: a reaction entry of
the same reaction with the same structure participants (same species entries,
either direction), so the excited-state or isotopologue entry's transition state
is refused. A bundle mints its own reaction entry, so the entry itself can
never match. The bundle's `tunneling_model` is filled from
`tunneling_application.model` when only the evidence is sent, as on the
standalone route.

Three wire enums are now mirrored here, so the evidence models can live in this
package: `KineticsEnsemblePolicy`, `KineticsStandardStateConvention` and
`KineticsDegeneracyInterpretation`, with the same members and order as the
backend's.

## 0.62.0 - 2026-09-30

Four changes for correction-scheme provenance (#619), all optional and
backward compatible: a payload written against 0.61.0 is still valid and means
what it meant.

- **`EnergyCorrectionSchemeRef.data_revision`** (`str | null`, at most 200
  characters). The revision of the data that holds the parameter tables, for
  example the RMG-database commit holding Arkane's atom-energy and BAC tables.
  When present it joins the scheme's identity and the workflow-tool build stops
  being part of it: identity is `(kind, name, level_of_theory,
  source_literature, software, data_revision)`. Two builds that read the same
  revision's tables are one scheme (the first depositor's build is kept as
  provenance), and a new revision is a new scheme, so a one-parameter change in
  a new database revision is no longer refused as a value conflict. When absent
  the identity is exactly what it was, including the tool build, so every scheme
  already deposited keeps its identity and its public ref. A deposit with a
  revision never matches one without, even when the tables are identical. A value
  of 7 to 64 hex digits is lower-cased as a git commit; any other value is kept
  as written. Adapters: send the RMG-database commit here, keep stamping the tool
  release, and expect the tool release to stop splitting schemes.
- **`EnergyCorrectionSchemeRef.atom_params_applied_as`** (`subtracted` |
  `added`, new enum `AtomParamApplication`). How the scheme's `atom_params`
  enter the corrected energy. It covers every entry of `atom_params` and nothing
  else, requires `atom_params` to be present, and is not inferred when omitted.
  A value that differs from the one stored on the matched scheme is refused like
  a differing parameter value; a value sent for a row that has none is stored.
  Arkane's `atom_energy` tables are `subtracted` (`count * value` is removed
  from the energy), its `atom_hf` tables are `added`, and its `atom_thermal`
  tables are `subtracted`, because Arkane applies
  `+ count * (atom_hf - atom_thermal)` per atom. The first deposit of a scheme
  fixes its sign: a later differing value is refused.
- **`SchemeAtomParamPayload` now documents its meaning and unit.** `value` is in
  the scheme's `units`. For `kind=atom_energy` it is the level's atomic energy of
  `element`; `atom_hf` is the atom's experimental enthalpy of formation;
  `atom_thermal` its thermal enthalpy increment; `soc` its spin-orbit correction.
  No field changed; the contract text did.
- **`energy_level_of_theory` is now stored.** On thermo and statmech blocks
  (every bundle root, `/uploads/thermo`, `/uploads/statmech`, and the statmech
  nested in `/uploads/conformers`) the declared level was checked against the
  linked calculations and discarded. It is now stored as declared and read back as
  `levels.declared_energy` on the scientific thermo and statmech reads. It is
  separate from `levels.energy`, which is still derived from the linked
  calculations on every read. It stays `null` when nothing was declared, and is
  never back-filled. No request field changed.

Scientific reads gain `levels.declared_energy`,
`energy_correction_scheme.data_revision` and
`energy_correction_scheme.atom_params_applied_as` (all `null` when not stated).

## 0.61.0 - 2026-09-30

Four things the two species-bearing bundles, `POST /api/v1/uploads/computed-species`
and `POST /api/v1/uploads/computed-reaction`, could not say (#622), all additive.

- **Transport.** `ComputedSpeciesUploadRequest.transport` and, per species,
  `BundleSpeciesIn.transport` take a `TransportInBundle`: exactly the standalone
  `POST /api/v1/uploads/transport` content (`sigma_angstrom` and
  `epsilon_over_k_k` together, `dipole_debye`, `polarizability_angstrom3`,
  `rotational_relaxation`, `scientific_origin`, `literature`, `software_release`,
  `workflow_tool_release`, `note`, at least one property) plus
  `source_calculations`, a list of `{calculation_key, role}` naming the bundle's
  own calculations (`role` is `full_transport`, `dipole`, `polarizability` or
  `supporting_geometry`). It lands on the same species entry as that species'
  thermo and statmech, and is append-only. A source calculation must belong to
  the same species. Provenance follows thermo: the block's own release wins,
  and where it names none the bundle's fills in (`workflow_tool_release` on
  both bundles, `analysis_software_release` as well on the reaction bundle).
  The responses name what was written: `ComputedSpeciesUploadResult.transport`
  (`{transport_id, transport_ref}`) and `transport_ids` with `transport_refs`
  (`trn_...`, same order) on the reaction bundle's response. A source
  calculation of another species on the reaction bundle is refused with the
  coded `transport_source_calculation_owner_mismatch` (422, `context` has
  `field`, `target`, `owner_kind`), now a code a depositor can receive.
  Provenance gaps are annotated as warnings under `transport.` /
  `species['<key>'].transport.`.
- **Rejected rotors.** `invalidated_reason` on `StatmechTorsionInBundle` and on
  the reaction bundle's `BundleStatmechTorsionIn`, the same field the conformer
  route's `StatmechTorsionIn` has. It is stored and read back under the
  statmech's torsions.
- **Who measured the SCF stability.** `SCFStabilityContent.source_calculation_key`
  names the calculation (job) in the same bundle that measured the verdict, when
  it is not the calculation the block is attached to. It may point at a
  calculation declared later in the payload. Rules: it must name a declared
  calculation; of the same species entry (or of the transition state, for a
  transition-state calculation), else the coded
  `scf_stability_source_calculation_owner_mismatch`; on the same conformer as
  the carrier (reaction bundle: the same `conformer_key`, or the conformer of
  its `geometry_key`, where both are stated), else the coded
  `scf_stability_source_geometry_mismatch` (422, `context` has `field`, `key`,
  `carrier_key`), the sibling of `thermo_sp_geometry_mismatch`; it may not name
  the carrier itself or close a cycle with other blocks' keys. A measuring job
  at a different level of theory than the carrier is accepted with the upload
  warning `scf_stability_source_level_mismatch`. The key is stored in the
  existing `calc_scf_stability.source_calculation_id` and read back as
  `source_calculation_ref`. No stability calculation type was added: the
  measuring job keeps the type it has. `SCFStabilityPayload` (the primitive
  routes, which name a calculation by id) and `SCFStabilityContent` now share
  `SCFStabilityBase`, so the key is not in the primitive routes' schemas and is
  refused there as an unknown field. It is also refused on
  `POST /api/v1/uploads/networks/pdep`, which has no pass to link it.
- **Contract prose.** `SoftwareReleaseRef` now says what `version`, `revision`
  and `build` hold and that all three are part of release identity (`revision`
  is the vendor label such as Gaussian `C.02`, or the commit hash for analysis
  software; `build` is a compile or packaging variant). There is still no
  `git_commit` on `SoftwareReleaseRef`; adding one needs a column and a change
  to release identity, and is deferred. `PathSearchResultPayload.converged` now
  says what it means for each method and that an output file existing is not a
  convergence verdict. `PathSearchPointPayload.is_climbing_image` stays a plain
  `bool` defaulting to `false`: `false` still reads as both "not a climbing image"
  and "not stated", because a tri-state value needs the stored column to accept
  NULL, which is a migration and is deferred.

## 0.60.0 - 2026-09-30

`electronic_levels` is now accepted on the bundle statmech blocks,
`StatmechInBundle` (`/uploads/computed-species`) and `BundleStatmechIn`
(`/uploads/computed-reaction`), with the same element shape
(`ElectronicLevelIn`: `level_index`, `energy_cm1`, `degeneracy`) and the same
rule as `/uploads/statmech` (`level_index` unique within a statmech). Additive
and optional: it defaults to no levels, and both bundle roots keep the same
field set (#609). The network PDep upload's species and transition-state statmech blocks share the backend persist path, so they now accept and store `electronic_levels` too. Before this, either block refused it with 422
`extra_forbidden`, so a single-atom bundle (ARC's O and Cl atoms) had no way to
carry its electronic partition function. The backend also gains upload
warnings for a one-atom species whose ground term is not S:
`missing_atomic_electronic_levels`, `missing_atomic_spin_orbit_correction`,
`atomic_electronic_degeneracy_contradicts_term`, and `term_symbol_contradicts_multiplicity`
for a term symbol whose leading 2S+1 disagrees with the declared multiplicity.
`missing_statmech_frequency_source` now decides "single atom" from the
geometry, `rigid_rotor_kind` or the species identity rather than from the
absence of rotational constants (#608).

## 0.59.0 - 2026-09-30

A single atom may be deposited with an `sp` primary (#610), on both
`POST /api/v1/uploads/computed-species` and
`POST /api/v1/uploads/computed-reaction`. Both routes required the conformer's
primary calculation to be an `opt`, and an atom has no geometry to optimise, so
ARC's adapter relabelled the atom's single point as an "optimisation" (same log
and energy, `converged=false`, and a level of theory and program that were not
the ones used). The rule is now: a conformer whose own XYZ has exactly one atom
may send `type: "sp"` as its primary; a conformer of two or more atoms still
needs `opt`, and a one-atom primary of any type other than `opt` or `sp` is
still refused. The refusal for an `sp` on two or more atoms now says how many
atoms the geometry has. A relabelled `opt` on an atom is still accepted, so no
existing producer breaks. The server stores the atom's `sp` with the conformer
geometry as both its input and its final output, so the conformer reads back
with a geometry; a further `sp` on the atom gets no inferred
`single_point_on` edge and no `dependency_edge_not_inferred` warning, since the
atom has no `opt` for the edge to name. With no `opt` linked, two `sp` links
on one geometry (thermo or statmech) are refused `thermo_role_duplicate` /
`statmech_role_duplicate`, as two on one optimisation always were; the
`/uploads/conformers` route gives a one-atom `sp` primary the same geometry
link. What an atom should send is in the
producer contract, under the two conformer primary-calculation rules. The
wire shape gains nothing: no field is added, removed or renamed.

## 0.58.0 - 2026-09-30

Producer contract only; no model in this package changes. `POST /api/v1/bundles/dry-run`
and `POST /api/v1/bundles/submit` now cap one bundle (#586): a request body
over 5 MiB is refused `bundle_too_large` (413, `context.max_bytes`, and
`context.given_bytes` when the request declared its length) before it is
parsed, and a bundle with more than 500 thermo plus kinetics records is
refused `bundle_too_many_records` (422, `context.max_records`,
`context.records`). Both caps are operator settings; the defaults are far
above any bundle measured. The dry run also has its own, tighter rate bucket
(10 per minute per credential by default), still answered `429
rate_limit_exceeded`.

## 0.57.0 - 2026-09-30

Every upload, job and bundle response now names its submission by public ref
as well as row id: `submission_ref` (`sub_...`) beside `submission_id`. In this
package that is `ComputedSpeciesUploadResult.submission_ref`, and
`CalculationUploadRefInBundle.calculation_ref` (`calc_...`) beside
`calculation_id`. Both are optional and additive. The two routes that took a
row id in the path, `POST /api/v1/submissions/{submission_id}/rights-attestations`
and `POST /api/v1/calculations/{calculation_id}/artifacts`, now accept either
the integer or the ref there (a `handle_type_mismatch` 422 for a ref of the
wrong kind, 404 `handle_not_found` for an unknown one, in either form); the integer is deprecated, not removed.

## 0.56.0 - 2026-09-29

`reversible` on the reaction of `POST /api/v1/uploads/transition-states`
(`TSReactionUpload`) is now optional and defaults to `true`, matching
`POST /api/v1/uploads/computed-reaction`. Omitted means `true` on both
routes: a transition state belongs to an elementary step, and an elementary
step is reversible by microscopic reversibility. Send `false` only to state
that the step is irreversible. A payload that already sends the field is
unaffected. Both routes now carry the same description of the field in the
contract. (#583)

## 0.55.0 - 2026-09-29

Producer contract only; no model in this package changes. New refusal code
on `POST /api/v1/submissions/{submission_ref}/supersede`:
`submission_supersede_not_owner` (403). The caller must have created both
submissions -- the one in the path and the one named by `new_submission_ref`
-- or hold the curator or admin role. An unknown ref is still 404.

## 0.54.0 - 2026-09-29

The producer contract now shows the enthalpy-reference rule
(`enthalpy_reference_error`) as reached from `POST /api/v1/bundles/dry-run`
as well as `POST /api/v1/bundles/submit` (#577). A dry run used to skip
every check inside the thermo and kinetics upload workflows, so a bundle
could pass it and then be refused on submit; it now rehearses submit and
reports the refusal submit would give, with the same `code` and message,
as an `error` entry in `messages` (and `bundle_valid: false`). No wire
model changed.

New refusal code on `POST /api/v1/bundles/dry-run` only:
`dry_run_contended` (503, `Retry-After: 1`, `context.reason` is
`lock_timeout` or `deadlock`). The rehearsal gives way to a concurrent
deposit writing the same records rather than delay or deadlock it; nothing
was decided about the bundle, so retry the dry run.

## 0.53.0 - 2026-09-29

Producer contract only; no model in this package changes. The submission
supersede surface is now `POST /api/v1/submissions/{submission_ref}/supersede`
with body `{"new_submission_ref": "sub_..."}` (issue #571): both submissions
are named by public ref, and a row id is refused with 422 in the path and in
the body. **Breaking for that route:** the 0.52.0 contract's
`new_submission_id` (an integer) is no longer accepted. A submission's ref is
`public_ref` on a submission read.

## 0.52.0 - 2026-09-29

Ship a **producer contract** inside the package:
`tckdb_schemas/contract/PRODUCER_CONTRACT.md` plus one JSON Schema per
upload payload under `tckdb_schemas/contract/schemas/`. It states, per
upload route, the payload fields (types, units, allowed values,
constraints), the rules the payload models and the upload workflows
enforce, the refusal codes the route can return, and a minimal valid
example. It is generated from the backend's routes, models and code
catalogue (`backend/scripts/generate_producer_contract.py`) and CI refuses a
stale copy. Read it with `python -m tckdb_schemas.contract --print`, see
what moved since the version you target with `--since <version>`, and load a
schema with `tckdb_schemas.contract.json_schema("ThermoUploadRequest")`.

Also new: `tckdb_schemas.producer_rule.producer_rule`, a marker for a
shared rule a workflow applies outside the payload model; the contract
prints every marked function a route reaches. `enthalpy_reference_error` is
marked, and its docstring now states the rule in a producer's terms.
`ThermoStateFields` gains field descriptions for `phase`,
`enthalpy_reference_kind` and `reference_pressure_bar`, and the four upload
request models this package defines declare a minimal valid example
(`json_schema_extra["examples"]`). No field, value or validation behaviour
changes: every 0.51.0 payload validates identically.

## 0.51.0 - 2026-09-27

`enthalpy_reference.enthalpy_reference_error` now counts a tabulated Gibbs
energy (`points[*].g_kj_mol`) as enthalpy content. A stored G is
H(T) - T*S(T) on the record's enthalpy zero, so it carries H's reference.
**Behaviour change for depositors:** a thermo deposit whose points carry `g`
must now declare `enthalpy_reference_kind`, like one that carries an
enthalpy; before, an undeclared G-only (or G-and-S) deposit was accepted,
and declaring `formation_298k` on it was refused as
`enthalpy_declaration_without_content`. Now the undeclared one is refused
with `enthalpy_declaration_absent` and the declared one is accepted. Codes
and messages are unchanged. Existing stored records are not revisited.

## 0.50.0 - 2026-09-27

Add `enthalpy_reference.shared_enthalpy_reference(kinds)`, the rule for
combining enthalpies from several thermo records: every term must declare
its `enthalpy_reference_kind`, and all must declare the same one. It returns
the shared kind, or declines with `enthalpy_reference_unrecorded` (any term
undeclared) or `enthalpy_reference_mixed` (terms disagree), exported as
`ENTHALPY_REFERENCE_UNRECORDED` / `ENTHALPY_REFERENCE_MIXED`. An empty
collection raises `ValueError` rather than reporting a vacuous shared basis.
The ML reaction export's `delta_h298` already applied this rule privately
and now calls it; its reason values are unchanged. Additive: no existing
name, value or behaviour changes.

## 0.49.0 - 2026-09-24

Stop defaulting `reference_pressure_bar` to 1 bar on computed thermo uploads
(issue #529). ARC computes entropy at 1 atm via a hardcoded translational
partition function and records no pressure anywhere in its output, so the
default was stamping every computed deposit with a standard state its own
numbers were not computed at. An omitted pressure now stays unrecorded, for
every scientific origin. `phase` keeps defaulting to `gas` for computed
uploads: unlike the pressure convention, gas is a direct consequence of the
ideal-gas statistical mechanics every current computed producer uses, not
an arbitrary convention a producer could plausibly have gotten wrong.
Existing stored records still carry the old 1 bar default; correcting them
is a separate, undecided question.

## 0.47.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.
