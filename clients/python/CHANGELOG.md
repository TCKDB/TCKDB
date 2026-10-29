# Changelog

## 0.179.0 - 2026-10-29

Allene `Ra`/`Sa` and odd-cumulene `E`/`Z` in the entry stereo descriptor.

- Requires `tckdb-schemas>=0.141.0`.
- Reads: `stereo_not_represented` on species-entry and structure-search records now lists `axial_small_ring` (a cyclic allene in a ring of eight or fewer) and
  `allene_dependent` (a centre or double bond that depends on an allene's sense) and `cumulene_tied_end` (a cumulated chain with a tied pair of stereo-bearing arms at one end). It no longer lists `axial` or `cumulene`: an open-chain allene or cumulene with
  a `?` is simply not settled yet.
- `stereo_descriptor` and `stereo_label` can hold `Ra` / `Sa`. `stereo_smiles` is `null` for any entry with an allene or cumulene element.
- A CHEMKIN export prints `STEREO_NOT_REPRESENTED=axial_small_ring|allene_dependent|cumulene_tied_end` in place of `axial|cumulene`.

## 0.178.0 - 2026-10-28

A network channel's kind is derived from its states.

- Requires `tckdb-schemas>=0.140.0`.
- One refusal code is published: `network_channel_kind_mismatch` (422).

## 0.177.0 - 2026-10-27

Stereo isomerisations, duplicate network states, stereo in exports.

- Requires `tckdb-schemas>=0.139.0`.
- Three refusal codes are published: `reaction_entry_null_reaction`, `network_state_duplicate_composition` and `network_state_duplicate_participant` (all 422).
- Reads: species-entry and structure-search records gain `stereo_not_represented` (`axial` / `cumulene`): the elements behind a `?` that no data can settle yet.
- Reaction participant rows (`reactants` / `products` on search, browse and `get_reaction_full`) are one per distinct entry per side, with `stoichiometry` the number of
  times that entry occurs (`H + H -> H2` lists the one H entry once, with 2). Summing `stoichiometry` over a side now gives the right count.
- `species_entry_label` is built from the descriptor and resolution (`? unresolved`, `E,? partial`); the review-queue subject chemistry carries
  `stereo_descriptor` and `stereo_resolution`.
- A CHEMKIN export now prints each entry's stereo (`STEREO=E`, `STEREO_RESOLUTION=partial`, ...) after the `SMILES=` token in its comments; a reader taking
  the first token after `SMILES=` is unaffected.

## 0.176.0 - 2026-10-27

A species entry's stereo is a descriptor; the species is the constitution.

- Requires `tckdb-schemas>=0.138.0`: `stereo_label` accepts descriptor tokens only (`R S r s E Z e z - ?`, one per stereo element). Free text such as `"trans"`
  is refused before the request is sent.
- Reads: the species-entry types gain `stereo_descriptor`, `stereo_resolution` and `stereo_smiles`; `stereo_label` is now the display text of the descriptor
  (null while nothing is resolved). Exact structure search takes `isomeric`.
- Two refusal codes are published (`species_stereo_contradiction`, `species_stereo_label_invalid`). Allenes and longer cumulenes are accepted with a `?`
  axis and the upload warning `species_entry_stereo_not_represented` (a different code from `species_entry_stereo_unresolved`: no further data would settle it).

## 0.175.0 - 2026-10-27

The README and docstrings describe the `curated` profile as it works.

- Documentation only; no behaviour change. `profile="curated"` applies the `approved` floor to the records a list, search, browse, select or export returns, and
  shows the evidence behind a returned record with its review badge. Only `rejected` and `deprecated` records are withheld. The typed-dict notes
  that said "not approved" now say "rejected or deprecated".

## 0.174.0 - 2026-10-27

Thermo and statmech selections say when the basin they were asked about was forwarded.

- `ThermoSelectionResponse` and `StatmechSelectionResponse` carry `forwarded_from_conformer_group_ref`: the conformer-group ref that was asked for when
  that group had been merged into another (the decision is made on the group that took its members, which is the one `request` echoes), otherwise `None`.
  Additive; nothing is removed.
- `search_conformers` takes `wide_basin` and `min_span_deg`: list every conformer group that is a wide basin (optimised members linked end to end but not
  all within tolerance of each other) in one query. They are filters, not a ranking; the order is unchanged.

## 0.173.0 - 2026-10-26

`MadeUnder` no longer carries `inputs_unchecked_reason`.

- **Behaviour change.** A curated read that asks for `check_inputs` now compares a stored assessment's inputs exactly as an exploratory one does,
  so `state` is `current` or `stale` (`stale_reason` `inputs_changed`) with `inputs_checked` true, the same under both profiles. The `curated_profile`
  reason had no other use, so the field and its `Literal` are removed from the `MadeUnder` typed dict. A reader that tested for it should drop the test.

## 0.172.0 - 2026-10-26

The thermo builders default the enthalpy declaration.

- **Behaviour change.** `Thermo.scalar`, `Thermo.nasa` and `Thermo.points` set `enthalpy_reference_kind="formation_298k"` when the argument is
  omitted and the record carries enthalpy content (an `h298_kj_mol`, a NASA fit, or points with `h_kj_mol` or `g_kj_mol`, as judged by
  `tckdb_schemas.enthalpy_reference`). That is formation enthalpy at 298.15 K plus the species' own increment from 298.15 K. The built payload carries the field.
- Only an omitted argument triggers the default. An explicit value is kept; an explicit `None` stays `None` (the builder refuses with
  `enthalpy_declaration_absent`, as before). A record with no enthalpy content gets no default. The server is unchanged and still refuses a deposit
  that carries enthalpies without the declaration.
- The `enthalpy_reference_kind` parameter of the three factories now tells "not passed" apart from an explicit `None` (its type hint widens accordingly); the bare `Thermo(...)` constructor is unchanged.

## 0.171.0 - 2026-10-26

Thermo and kinetics take the full provenance filter set, and a level of theory carries a method family and DFT rung.

- `get_species_thermo` and `get_reaction_kinetics` take `method`, `basis`, `software_version`, `workflow_tool`,
  `workflow_tool_version`, `method_family`, `dft_rung_min`, `dft_rung_max` and `scientific_origin` (`computed`, `experimental` or `estimated`, matched on the
  record itself); `search_thermo` and `search_kinetics` take the same. `search_statmech` takes `scientific_origin` too. `search_transition_states`, `search_levels_of_theory` and `browse_levels_of_theory` take
  `method_family`, `dft_rung_min` and `dft_rung_max`.
- **A behaviour change.** On thermo and kinetics, `level_of_theory_ref` / `level_of_theory_id`, `software` and the new provenance fields used to be matched
  independently: a record with a calculation at level X in software P and another at level Y in software Q matched level X with software Q. Now ONE source
  calculation has to match every supplied provenance field, as on every other route. A calculation the read profile hides never matches (under `curated`).
- `method_family` is one family or a list: `dft`, `wavefunction`, `composite`, `multireference`, `semiempirical`, `force_field`, `unclassified`.
  `dft_rung_min` / `dft_rung_max` bound the DFT rung, 1 (LDA) to 5 (double hybrid), and imply `method_family=dft`. These classify the method; they are not
  a measure of accuracy (the ladder covers DFT only), and nothing in TCKDB orders, ranks or selects by them.
- **Breaking, pre-release.** `search_reactions`, `search_thermo` and `search_kinetics` name their HTTP-verb argument `method_http` (as the other search
  methods do), not `method`. `search_thermo` and `search_kinetics` take `method` as the method-name filter. `search_species_calculations` takes
  `method_family`, `dft_rung_min` and `dft_rung_max`; `search_calculations` passes them through its `**filters`.
- A level-of-theory record gains `method_family`, `dft_rung` (`null` unless `dft`) and `method_classification` (`{name, version}` of the classification
  that produced them: `method_family_classification`, version 1).
- `SelectionFilters` gains `scientific_origin`, `method_family`, `dft_rung_min` and `dft_rung_max`. Thermo and kinetics select take the provenance fields
  too, with the one-calculation rule. Their manifest format moves to version 5; a manifest in format 4 is refused on replay.

## 0.170.0 - 2026-10-25

- Requires `tckdb-schemas>=0.137.0`: a statmech declaration's ensemble member states the convention of its relative energy
  (`relative_energy_convention`), required with `relative_energy_kj_mol`. The client carries declarations as the wire models, so
  nothing else changes.

## 0.169.0 - 2026-10-25

A kinetics record can state the bath gas it was obtained for.

- `Kinetics.modified_arrhenius` takes `pressure_context`, `pressure_bar` and `bath_gas`, a list of `(Species, mole_fraction)` pairs. The species join the upload's
  species list and are sent by bundle key. Nothing is inferred or defaulted; the builder refuses, with the server's codes, a bath gas at `high_p_limit`
  (`kinetics_bath_gas_at_high_pressure_limit`), fractions that do not sum to 1 (`kinetics_bath_gas_mole_fraction_sum`) and one species twice
  (`kinetics_bath_gas_duplicate_species`).
- The three codes join `RejectionCode`. A kinetics record read back carries `bath_gas` (`KineticsDetailRecord`), null when none was stated.
- Requires `tckdb-schemas>=0.136.0`.

## 0.168.0 - 2026-10-25

A calculation dependency that would close a cycle is refused with a coded 409.

- New rejection code `calculation_dependency_cycle` (`RejectionCode.CALCULATION_DEPENDENCY_CYCLE`). `context` carries
  `parent_calculation` and `child_calculation`, both public references. Nothing else about the client changes.

## 0.167.0 - 2026-10-25

Every list read can be walked by cursor, and every `iter_*` helper does.

- Each `search_*` / `browse_*` / `get_*` list method takes `cursor`, and the response carries `next_cursor` (`None` on the last page) and
  `watermark`. `pagination.mode` says whether a page was served by `offset` or by `cursor`.
- All `iter_*` helpers follow `next_cursor` (they used to count offsets, except the four analytics ones). A traversal over a corpus that is
  being written to no longer repeats or skips a record. Passing a non-zero `offset` to an `iter_*` helper still walks by offset, from there.
- `offset` is random access and is deterministic only for a static corpus; use it for "page N of M". The cursor pins a traversal to the
  records that existed when it began; it does not freeze review state. A cursor is bound to its endpoint, filters and read profile
  (`cursor_query_mismatch`), and cannot be combined with a non-zero `offset` (`cursor_offset_conflict`).
- The analytics request echo no longer carries `pagination_mode`; it is `pagination.mode`.

## 0.166.0 - 2026-10-24

A select call takes the caller's filters, and today's exclusions are named defaults the request can lift.

- `select_species_thermo`, `select_reaction_kinetics`, `select_network_kinetics`, `select_species_calculations`, `select_conformer_basins`,
  `select_transition_state_evidence` and their `get_*_selection_manifest` twins take `filters` and `eligibility`. `select_statmech_models`
  and `get_statmech_selection_manifest` send them as part of `request`. Each is sent only when given; the client invents neither.
- `filters` uses the matching search's vocabulary: thermo and kinetics `model_kind`, `level_of_theory_ref`, `software`; network and statmech
  `model_kind` plus `method`, `basis`, `software`, `software_version`, `workflow_tool`, `workflow_tool_version`; structure the six provenance
  fields. A filter the search does not offer is a 422, never ignored. A filter excludes on a fact and ranks nothing.
- `eligibility` lifts named defaults: `include_rejected`, `include_deprecated` (every route), `include_hard_failed_evidence` (thermo,
  kinetics), `include_confirmed_findings` (thermo, kinetics, statmech, structure). A rejected calculation quality is lifted by listing `rejected`
  in `permitted_quality`. The read profile's review floor is not a default: `curated` cannot be overridden.
- Every answer gains `filters` (the normalised filters and how many candidates they removed), `eligibility` (each default: `name`, `applied`,
  `source` of `default`, `request` or `read_profile`, `excluded`, `admitted_by_override`) and `funnel` (`considered`, `removed_by_filters`,
  `removed_by_eligibility`, `remaining`). Each candidate or unit gains `blocked_by` and `passed_only_by_override`. The network answer gains `solves`.
- `funnel` adds up: `removed_by_filters + removed_by_floor + removed_by_eligibility + remaining` is `considered`, each candidate counted once (the
  per-default `excluded` counts can overlap). `removed_by_floor` is the caller's own `min_review_status`. `filters.applied` lists a narrower
  `permitted_quality`, `member_refs` and a `model_revisions` population too, and `filters.note` says so when the filters removed everything.
  A determination with no conformer observation reports `review_status: null` (its species entry's status is not copied onto it), and a
  basin hides with a conformer group below the profile.
- A species or reaction entry is a scoping parent: its own review status no longer excludes a statmech candidate, a calculation or a conformer
  basin. A transition-state entry still gates its evidence and statmech candidates.
- Recorded decisions now carry the filters and eligibility settings, so replay reproduces them. Manifest formats move to version 3
  (`thermo`, `kinetics`, `network`, `statmech`, `structure`, relative conformer energies); a manifest in format 2 is refused on replay.
- New TypedDicts: `SelectionFilters`, `SelectionEligibilityIn`, `EligibilityDefaultEcho`, `SelectionFunnel`, `SelectionFilterAnswer`.

- `PublicAssessmentSummary.deterministic_trust` is now the typed `DeterministicTrustSummary` (it was a plain `JSONDict`), and both it and
  `ReproducibilityAssessmentSummary` gain `made_under` (new `MadeUnder` type): the rubric an assessment was made under, the version it was made under, the
  current version, `state` (`current`, `stale`, `unassessed`, `inputs_unchecked`), `stale_reason` and `inputs_checked`. `ReproducibilityAssessmentSummary.state`
  takes `inputs_unchecked` too: a list or search read compares the rubric version only and never says `current`. Version 0 means stamped before the 2026-10
  versioning reset. Additive.
- `get_species_thermo` and `get_reaction_kinetics` take `check_inputs` (with `include=assessments`, at most 10 records): compare each stored assessment's
  inputs with today's. Requires `tckdb-schemas>=0.134.0`. `RejectionCode` gains `assessment_input_check_limit`, `check_inputs_needs_assessments` and `check_inputs_not_supported`. `MadeUnder` gains `inputs_unchecked_reason` (`curated_profile`).

## 0.164.0 - 2026-10-24

- The deprecated `GET /species-entries/{id}/conformer-observations/lowest-sp` route is removed from the server (it was a silent pick and named records the profile hides); the parity table drops it. Use `relative_conformer_energies`.

## 0.163.0 - 2026-10-24

- `RejectionCode` gains `lowest_energy_across_levels` (the `/lookup` refusal above).
- `get_calculation_hessian` takes `profile`: the legacy entity reads now honour the read profile like the scientific reads.
- Requires `tckdb-schemas>=0.133.0`.

## 0.162.0 - 2026-10-23

No read and no export picks a record for you. Browse reads list every match in display order; "give me one" is the `select_*` methods.

- `collapse` is gone from every read method (`search_species`, `browse_species`, `search_reactions`, `browse_reactions`, `get_reaction_kinetics`,
  `get_species_thermo`, `search_thermo`, `search_kinetics`, `search_species_calculations`, and the other searches that took it). The server refuses
  `collapse` and `selection_policy` with 422 `browse_parameter_removed`, which names the select endpoint to use.
- On `get_species_thermo`, `search_thermo`, `get_reaction_kinetics` and `search_kinetics`, `temperature_min` / `temperature_max` are now
  `coverage_temperature_min_k` / `coverage_temperature_max_k`: they never filtered, they only set the window each record's `temperature_coverage`
  is computed against. The old names get 422 `parameter_renamed`. `pressure_bar` on the kinetics reads is unchanged (it does filter).
- A thermo and a kinetics record carry `created_at` (deposition time), the second key of the display order. `OrderEcho` gains `requested_by`
  (`"ranking=lowest_energy"`, `"mode=substructure"`; `None` for the default order). The statmech and transport `effective_order`,
  `selection_policy` and `selection_policy_applied` echoes are gone.
- `pagination.post_collapse_total` is gone (the server no longer sends it and the `Pagination` type drops it); `iter_paginated_records` no longer
  reads `collapse` and follows `pagination.total`.
- `export_ndjson` and `export_chemkin` no longer take `selection_policy` (the server refuses it). `export_chemkin` takes `thermo_refs`,
  `transport_refs` and `kinetics_refs`: Chemkin holds one record per species or reaction and the server never picks, so name the record where a
  species or reaction has several candidates, or the call fails with 422 `export_selection_ambiguous` (which lists every ambiguous subject).
- The ML species export's record carries `thermos`, `frequency_results` and `hessians` (lists); the ML reactions record carries
  `transition_states`, `barriers` and `barriers_unavailable` in place of `transition_state` / `barrier`. Format `tckdb.ml` version 2.
- Requires `tckdb-schemas>=0.132.0`.
- `RejectionCode` gains `browse_parameter_removed`, `parameter_renamed`, `export_selection_ambiguous` and `export_ref_unmatched`.

## 0.161.0 - 2026-10-22

The method-preference rules are gone from the selection endpoints, and the browse order is named in every list response.

- `select_species_thermo`, `select_reaction_kinetics` and `select_network_kinetics`: `policy` is `default` (the server default), `most_reviewed` or
  `latest`. `method_preferred` is retired (the server refuses it). Every policy is an administrative display order, never a method claim.
- The selection response types lose `policy.rules[]`, `relations`, `rule_matches` and `pair_checks`; the outcome tokens lose `policy_preferred` and
  `policy_conflict`, and a pick's `basis` is `sole_eligible_candidate` or `administrative_first`.
- `select_network_kinetics` no longer takes `objective`, `reference_model_ref` or `reference_outputs`.
- The structure selection methods no longer take `apply_rules`, `objective` or `reference_model`; `StructureSelectionOutcomeToken` has 11 values.
  `protocol_coverage` now lists the protocols that cover every requested member, in display order.
- `StatmechSelectionRequest` loses `comparison_objective` and `reference_model_ref`; `StatmechSelectionResponse` loses `edges`, `conflicts`,
  `undecided` and `protocol` and gains `administrative_order` and `administrative_key`; `StatmechSelectionSelection` loses `scientific_ref`;
  the outcome token loses `policy_preferred`, `policy_conflict` and `unresolved_comparability`.
- `request.sort` on every browse and list response is a structured `OrderEcho` (`kind: "display_order"`, `keys`, `statement`), not a string; its keys
  carry a direction and a meaning (`created_at` is deposition time). Statmech's `effective_order` has the same shape. `OrderEcho` and
  `OrderKeyEcho` are exported.
- Decision manifests returned by the selection routes are format version 2; the shape that held rules and edges is refused on replay.

## 0.160.0 - 2026-10-21

Follows `tckdb-schemas` 0.131.0 (review fixes to versioning PR-4). The floor is raised to `tckdb-schemas>=0.131.0`: the client now speaks the
`ParserStamp` / `{name, version}` wire that older schemas packages do not have, and the old floor (`>=0.116.0`) let a resolver pick one of them.

- The selection manifests the client returns (`RelativeConformerEnergyManifest`, `RelativeConformerEnergyCompareManifest`, and the engines' manifests,
  which the client passes through untouched) open with a root `format` block, `{"name": ..., "version": ...}`, in place of
  `manifest_format_version` (and, for the relative-energy manifests, the separate `manifest_format` name string). A manifest's
  `assessment_semantics` block gains a `name`, and its `source_findings` is a `{name, version}` pair instead of a bare integer.
- `tckdb-qcschema` 0.9.1: its floors are now `tckdb-client>=0.160.0` and `tckdb-schemas>=0.131.0` (they were unpinned).

## 0.159.0 - 2026-10-20

Follows `tckdb-schemas` 0.130.0 (versioning PR-4: a name and an integer version everywhere, every TCKDB-owned version reset to 1).

- The replay sidecar's `bundle_format_version` is the integer `1` (it was the string `"0"`); `DEFAULT_FORMAT_VERSIONS` is `(1,)`. A sidecar written by an
  older client holds `"0"` and is marked failed with `unsupported bundle_format_version`; re-export the bundle.
- `ReproducibilityAssessmentSummary.rubric_version` is `int | None` (was `str | None`).
- `ExecutionEnvironmentManifestRecord` carries `schema_name` and an integer `schema_version`.
- `StatmechSelectionRequest`-shaped records carry `contract_version: 1` (was `"statmech-selection-v1"`).

## 0.158.0 - 2026-10-20

`RejectionCode` gains one member, `unknown_manifest_schema` (409), for a release manifest whose stored format tag this server cannot
render. `tckdb-schemas` is 0.128.0 (the same code in the shipped contract).

## 0.157.0 - 2026-10-19

The two curator routes for structure-evidence findings (`POST` and `GET /api/v1/curation/structure-evidence-findings`) are registered in
the parity matrix as curation workflow, like the other curator routes: no typed method, reachable through the raw request helpers.
`RejectionCode` gains twelve members, all prefixed `structure_finding_`, for the refusals those routes can return.
`tckdb-schemas` is unchanged.

## 0.156.0 - 2026-10-18

`NetworkSolveRecord.network` and `NetworkKineticsRecord.network` are `JSONDict | None`: under `profile="curated"` a visible
network solve (or fit) whose network is not approved comes back with `network` null instead of naming the network (the key is
always present). Code that indexes `record["network"]["network_ref"]` under a type checker now needs a `None` check; the default
profile never returns `None`. No method changed, and `tckdb-schemas` is unchanged.

## 0.155.0 - 2026-10-18

`TransitionStateEntryRecord.transition_state` is `JSONDict | None`: under `profile="curated"` a visible transition-state entry whose
transition state is not approved comes back with `transition_state` null instead of naming the owner (the key is always present).
Code that indexes `record["transition_state"]["transition_state_ref"]` under a type checker now needs a `None` check; the default
profile never returns `None`. Also under `profile="curated"`, a kinetics record's `determination`, `tunneling_application` and
`provenance["irc"]` are `None` when the transition-state entry, network channel or calculation they name is not approved
(documented in the type's comment). No method changed, and `tckdb-schemas` is unchanged.

## 0.154.0 - 2026-10-18

Four typed methods for the relative conformer energy reads: `relative_conformer_energies`, `get_relative_conformer_energy_manifest`,
`compare_relative_conformer_energies` and `get_relative_conformer_energy_comparison_manifest` (the four `.../conformers/relative-energies`
routes, previously raw-only). The scientific choices are required keyword arguments with no default: the reference conformer,
`energy_kind`, `certificate` and the level (exactly one of `level_of_theory_ref` and `composite_scheme_ref`, else `ValueError` before any
request); the comparison also requires `candidate`, `reference` and `pairing`. Optional fields left unset are not sent. The manifest methods
return the server's JSON unchanged, for replay. New types: `RelativeConformerEnergyResponse`, `RelativeConformerEnergyManifest`,
`RelativeConformerEnergyCompareResponse`, `RelativeConformerEnergyCompareManifest`, `RelativeEnergyMember`, `RelativeEnergyComparePair`,
`RelativeEnergyCompareClaims`, `RelativeEnergyPairing`. No new dependency.

## 0.153.0 - 2026-10-18

`RejectionCode` gains `relative_conformer_energy_compare_identical_protocols` (`tckdb-schemas` 0.125.0): the code of the new server read
`POST /scientific/species-entries/{ref}/conformers/relative-energies/compare` (a descriptive comparison of two stored protocols). No
method changed; a client method for the route is a separate release.

## 0.152.0 - 2026-10-17

`ConformerObservationRecord.conformer_group` is `JSONDict | None`: under `profile="curated"` a visible observation whose conformer
group is not approved comes back with `conformer_group` null instead of naming the group. Code that indexes
`record["conformer_group"]` under a type checker now needs a `None` check; the default profile never returns `None`. Also under
`profile="curated"`, `StatmechRecord.species` and `StatmechRecord.transition_state` are both `None` when the record's subject is a
transition-state entry that is not approved (documented in the type's comment). No method changed, and `tckdb-schemas` is unchanged.

## 0.151.0 - 2026-10-17

`RejectionCode` gains `relative_conformer_energy_unsupported`, `relative_conformer_energy_population_too_large` and
`relative_conformer_energy_scope_owner_mismatch` (`tckdb-schemas` 0.124.0): the codes of the new server read
`POST /scientific/species-entries/{ref}/conformers/relative-energies` (relative conformer energies against a named reference
conformer). No method changed; a client method for the route is a separate release.

## 0.149.0 - 2026-10-13

`RejectionCode` gains `superseded_record_not_approvable` (`tckdb-schemas` 0.123.0): a curator re-approving a record that a correction
has replaced is refused with 409 on `PATCH /record-reviews/{type}/{id}`. Submission approval does not return it (it leaves a replaced
member deprecated). No method changed.

## 0.147.0 - 2026-10-12

`search_statmech` (and `iter_statmech`) take `transition_state_entry_ref` and `declared_conformer_group_ref`. The server now
applies the first (it used to ignore it); the second matches only records whose own declaration is that basin. In the typed
`StatmechRecord`, `species` is optional (`None` on a transition-state-owned record), and `transition_state`, `supersession` and
`levels` are added. Code that indexes `record["species"]` under a type checker now needs a `None` check, because
transition-state-owned records always sent `null`. No other method changed.

## 0.146.0 - 2026-10-12

`RejectionCode` gains `calculation_geometry_conformer_mismatch`, `calculation_input_geometry_mismatch`,
`transition_state_calculation_conformer_key` and `transition_state_calculation_species_geometry` (`tckdb-schemas` 0.121.0): a
calculation whose conformer and geometry keys, or geometry and explicit inputs, disagree is now refused. No method changed.

## 0.145.0 - 2026-10-11

Statmech selection: `select_statmech_models` and `get_statmech_selection_manifest`, with typed `StatmechSelectionRequest` and
`StatmechSelectionResponse`. The client sends the request as given, refuses an integer id, a ref of the wrong kind and any
bound, page, sort or rule before a request is made, never chooses a candidate, and returns the server's answer untouched
(`tckdb-schemas` 0.120.0). `RejectionCode` gains `statmech_selection_scope_owner_mismatch`. Each candidate's `advisory` and the response `notices` disclose
`imported_parameters_not_revalidated:<role>` and `source_literature_unidentified:<role>`.

## 0.144.0 - 2026-10-11

`RejectionCode` gains `scf_stability_source_cycle`, `scf_stability_source_conformer_unanchored` and
`ts_energy_ordering_source_type_mismatch` (`tckdb-schemas` 0.119.0). No method changed.

## 0.142.0 - 2026-10-11

Breaking: artifact uploads take the calculation's `calc_` ref only (#578). `upload_artifact(calculation_ref, ...)` raises
`ValueError` for anything that is not a `calc_` ref (an integer included), and `upload_artifacts` checks every plan item
for a `calculation_ref` before sending any request. The integer fallback is gone, along with the server's integer path
form; the server's minimum supported `tckdb-client` is now 0.97.0, the first release that addresses a calculation by its
ref. The client never called `POST /submissions/{submission_ref}/rights-attestations`, which now takes the `sub_` ref
only. Requires `tckdb-schemas` 0.116.0.

Retry caveat, narrowed: idempotency keys are scoped by URL path, so a lost-response retry sent through a different path
form is not a replay. An upload committed by 0.96 through the integer path before this change can still be retried with
the same key on the same integer path until its receipt expires (it replays, `Idempotency-Replayed: true`); a new key on
the integer path is refused (422 `invalid_handle`) and a client below 0.97.0 gets 426.

## 0.141.0 - 2026-10-11

`RejectionCode` gains `statmech_selection_bound_exceeded`, `statmech_selection_unsupported` and `unknown_statmech_candidate_ref` (`tckdb-schemas` 0.114.0).
No method changed; nothing in the server serves them yet.

## 0.140.0 - 2026-10-11

`RejectionCode` gains `statmech_model_declaration_invalid`, `statmech_model_declaration_version_unsupported`,
`statmech_model_declaration_mismatch` and `unknown_structure_determination_ref` (`tckdb-schemas` 0.113.0). No method changed:
the statmech blocks the client builds pass a `model_declaration` through untouched.

## 0.139.0 - 2026-10-06

Structure selection: `select_species_calculations`, `select_conformer_basins`, `select_transition_state_evidence` and
their `get_..._selection_manifest` siblings, with typed requests and `StructureSelectionResponse`. The client invents no default
of the question, sends `quantity=None` as JSON null only (evidence-only qualification), refuses an integer id, a ref of the wrong
kind and any bound, page, sort or rule before a request is made, and returns the server's answer untouched.

## 0.138.0 - 2026-10-05

`RejectionCode` gains `structure_selection_manifest_too_large` (`tckdb-schemas` 0.107.0). No method changed; nothing in the
server serves it yet.

## 0.137.0 - 2026-10-05

`RejectionCode` gains `structure_selection_population_too_large`, `structure_selection_evidence_too_large`,
`structure_selection_traversal_too_deep`, `structure_selection_unsupported` and `unknown_structure_member_ref` (`tckdb-schemas` 0.106.0).
No method changed; nothing in the server serves them yet.

## 0.136.0 - 2026-10-05

`Calculation.opt/freq/sp(..., actual_protocol_declaration=...)` carries a calculation's declared actual protocol
(electronic state and root, spin and relativistic treatment, core potential, core correlation, material numerical
approximations, included corrections) to both bundle wires (`tckdb-schemas` 0.105.0). It is validated against the shared
model when the builder is made; left out it is not sent, which the server reads as "not stated", never as a standard
recipe or gas phase. `RejectionCode` gains `structure_declaration_invalid`, `structure_declaration_version_unsupported`,
`structure_determination_invalid` and `structure_determination_mismatch`.

## 0.135.0 - 2026-10-04

`export_selected_network_kinetics(network_ref, *, manifest, node_ref, representation_refs, format=None,
allow_administrative_choice=None, energy_units=None, naming_policy=None, profile=None)` posts to
`/scientific/networks/{ref}/kinetics/export-selected` and serialises one network selection you saved with
`get_network_kinetics_selection_manifest`. The server replays the manifest, re-checks it against its own content under
one snapshot and refuses a stale, forged or incomplete one with a structured 422 (`network_export_manifest_invalid`,
`network_export_manifest_stale`, `network_export_choice_not_allowed`, `network_export_representation_choice_invalid`,
`network_export_unsupported_form`, now in `RejectionCode`). `allow_administrative_choice` is false on the server unless
you pass true. It takes a public `net_` ref only (an integer id is refused before any request); `None` options are
dropped, never sent as null. `include_reported` (CHEMKIN only) writes a solve of kind `reported` with its literature annotated (ADR 0010). New typed shapes `NetworkSelectedKineticsExportRequest` and `NetworkSelectedKineticsExport`.

## 0.134.0 - 2026-10-04

Method-aware selection among a network's stored pressure-dependent solves, for one stated gas-phase question
(`tckdb-schemas` 0.103.0 only lists the two new read-only routes in the contract). Read-only; the network reads are unchanged.

- `select_network_kinetics(network_ref, *, coefficient_basis, temperature_min_k, temperature_max_k, pressure_min_bar,
  pressure_max_bar, bath, partition, scope=None, channel_key=None, observable=None, outputs=None, ...,
  profile=None)` posts to `/scientific/networks/{ref}/kinetics/select`. It takes a public `net_` ref only (an
  integer id is refused before any request), and `channel_key` is a body field, never part of the path. The question
  is required and never defaulted; optional fields that are `None` are dropped, never sent as JSON null.
- `get_network_kinetics_selection_manifest(...)` returns the replayable decision manifest of the same request
  (`/kinetics/select/manifest`), a new snapshot of the current data and not a retrieval of an earlier selection.
- New typed shapes: `NetworkSelectionRequest`, `NetworkSelectionBathIn`, `NetworkSelectionPartitionIn`,
  `NetworkSelectionBoundaryIn`, `NetworkSelectionRegimeIn`, `NetworkSelectionOutputIn`, `NetworkSelectionResponse`,
  `NetworkSelectionPick`, `NetworkSelectionMember`, `NetworkSelectionDisclosures` and `NetworkSelectionOutcomeToken`.
- A population over a server bound, including more than 200 required outputs, is a 422
  `network_selection_population_too_large`; the client never caps or pages it itself.

## 0.133.0 - 2026-10-04

`RejectionCode` gains `network_selection_population_too_large` and `network_selection_snapshot_too_large`, regenerated from the server's catalogue; nothing else changes. The selection service they belong to is not routed yet.

## 0.132.0 - 2026-10-04

`RejectionCode` gains `network_declaration_invalid`, `network_declaration_version_unsupported` and `unknown_network_solve_ref`, regenerated from the server's catalogue; nothing else changes. The server accepts optional network solve declarations and fit determinations on `POST /uploads/networks/pdep` (`tckdb-schemas` 0.101.0); the client has no network upload builder.

Method-aware selection among a reaction entry's stored rate coefficients, for one stated gas-phase question
(`tckdb-schemas` is unchanged; the producer contract is unchanged). Read-only; browsing (`get_reaction_kinetics`) is
unchanged.

- `select_reaction_kinetics(reaction_entry_ref, *, direction, target, coefficient_basis, temperature_min_k,
  temperature_max_k, pressure, collider=None, policy=None, mode=None, min_review_status=None, phase=None,
  profile=None)` posts to `/scientific/reaction-entries/{ref}/kinetics/select`. It takes a public `rxe_` ref only
  (an integer id is refused before any request). The question is required and never defaulted; the optional fields
  are sent only when you supply them.
- `get_reaction_kinetics_selection_manifest(...)` returns the replayable decision manifest of the same request
  (`/kinetics/select/manifest`), a new snapshot of the current data and not a retrieval of an earlier selection.
- New typed shapes: `KineticsSelectionRequest`, `KineticsSelectionTargetIn`, `KineticsSelectionPressureIn`,
  `KineticsSelectionColliderIn`, `KineticsSelectionResponse`, `KineticsSelectionPick`, `KineticsSelectionCandidate`,
  `KineticsSelectionDisclosures` and the `KineticsSelectionOutcomeToken` vocabulary (`policy_preferred`,
  `incomparable_alternatives`, `sole_eligible_candidate`, `no_applicable_candidate`, `policy_conflict`). A
  population over the server's cap of 500 visible records is a 422 (`kinetics_selection_population_too_large`); the
  client never caps or pages it itself.
- Every rule in this server release is inactive, so `method_preferred` ranks nothing yet: expect
  `incomparable_alternatives` or `sole_eligible_candidate`, with the administrative order and the reasons.

## 0.130.0 - 2026-10-04

`RejectionCode` gains `kinetics_energy_level_contradiction`, regenerated from the server's catalogue; nothing else changes.

## 0.129.0 - 2026-10-04

`RejectionCode` gains `kinetics_selection_population_too_large`, regenerated from the server's catalogue; nothing
else changes. The server-side selection service it belongs to is not routed yet.

## 0.128.0 - 2026-10-04

`Kinetics.modified_arrhenius` can state a fit's `direction`, `determination`, `applicability` and `protocol`
(`tckdb-schemas` 0.96.0), optional attributed claims checked here by the rule the server applies and never
defaulted. A protocol's supporting calculations are given as `protocol_calculations`, `(purpose, Calculation)`
pairs the assembler names by bundle key. A fit that declares nothing emits the payload it always did.
`KineticsDetailRecord` gains `determination`, `applicability`, `protocol` and `declaration_unreadable`.
`RejectionCode` gains the kinetics declaration codes, regenerated from the server's catalogue.

## 0.127.0 - 2026-10-04

Adds the `RejectionCode.NETWORK_STATE_ENERGY_SUM_MISMATCH` member (`tckdb-schemas` 0.95.0, #678): the
network-PDep upload now refuses a state energy that contradicts the sum of the energies stored for the
per-participant source calculations it cites. Nothing in the client's code changes.


## 0.126.0 - 2026-10-03

Method-aware selection of a species entry's thermo record for the gas-phase formation enthalpy at
298.15 K (`tckdb-schemas` 0.90.0 carries the matching producer-contract note). Read-only; browsing
(`get_species_thermo`) is unchanged.

- `select_species_thermo(species_entry_ref, *, target, policy=None, result_mode=None,
  min_review_status=None, temperature_k=None, phase=None, profile=None)` posts to
  `/scientific/species-entries/{ref}/thermo/select`. It takes a public `spe_` ref only (an integer id is
  refused before any request) and sends only the fields you supply.
- `get_species_thermo_selection_manifest(...)` returns the replayable decision manifest of the same
  request (`/thermo/select/manifest`).
- New typed shapes: `ThermoSelectionRequest`, `ThermoSelectionTargetIn`, `ThermoSelectionResponse`,
  `ThermoSelectionPick`, `ThermoSelectionCandidate`, `ThermoSelectionDisclosures` and the
  `ThermoSelectionOutcomeToken` vocabulary (`policy_preferred`, `incomparable_alternatives`,
  `sole_eligible_candidate`, `no_applicable_candidate`, `policy_conflict`). A population over the server's cap of 500 visible records is a 422 (`thermo_selection_population_too_large`).
- `RejectionCode` gains `thermo_selection_condition_conflict` and `thermo_selection_population_too_large`, regenerated from the server's catalogue.

## 0.125.0 - 2026-10-03

Adds the `RejectionCode.GEOMETRY_ISOTOPE_SYMBOL_CONFLICT` member (`tckdb-schemas` 0.89.0, #672). A
`D`/`T` element spelling now means deuterium/tritium: an `isotopes` entry that contradicts the spelling
(`D` with mass 3 or 1, `T` with mass 2) is refused with this code. **A `D`/`T`-spelled geometry on a
protium species, previously accepted, is now refused** with the existing
`species_geometry_isotope_mismatch`; label the species (`[2H]O[2H]`) or spell the atom `H` with
`isotopes`. Nothing in the client's code changes.

## 0.122.0 - 2026-10-03

Adds the `RejectionCode.CALCULATION_GEOMETRY_ISOTOPE_MISMATCH` member (`tckdb-schemas` 0.78.0, #666): an
upload is refused when a geometry linked to a calculation carries different isotope substitutions than
the species (or, for a transition state, the reaction's reactants) the calculation is filed under.
Nothing in the client's code changes.

## 0.121.0 - 2026-10-03

Thermo builders can state what a record's values describe and how they were produced
(`tckdb-schemas` 0.85.0). Both are optional attributed claims; leave them out and the payload is
byte-for-byte what it was.

- `Thermo.scalar` / `Thermo.nasa` / `Thermo.points` accept `thermodynamic_target`
  (`"equilibrium_ensemble"` or `"single_conformer"`), `protocol` (a `ThermoProtocolDeclaration` or the
  equivalent dict, without its supporting calculations) and `protocol_calculations` (the
  `Calculation` builders the protocol rests on, named by bundle key at assembly).
- A `single_conformer` target names the species' own conformer: `ComputedSpeciesUpload` and
  `ComputedReactionUpload` resolve it to the conformer key they mint, and refuse it
  (`TCKDBBuilderValidationError`) when the species has no conformer in the upload. The reaction
  block's `thermo` keeps its place in the species block either way.
- The builder runs the shared `thermo_declaration_error` rule before any request, so a
  self-contradicting target, an unknown protocol field or an unsupported protocol version is refused
  locally with the server's code in the message (`thermo_target_group_required`,
  `thermo_protocol_version_unsupported`, ...).
- `RejectionCode` gains the codes above, regenerated from the server's catalogue:
  `thermo_target_group_required`, `thermo_target_group_not_allowed`,
  `thermo_target_group_owner_mismatch`, `thermo_declaration_invalid`,
  `thermo_protocol_version_unsupported`, `thermo_protocol_calculation_owner_mismatch`.
- Thermo reads gain `thermodynamic_target` (`kind`, `conformer_group_ref`) and `protocol`, both `null`
  on a record that declared nothing. `ThermoDetailRecord` types them.
- `tckdb-schemas>=0.85.0` is now required.
- `tckdb-schemas>=0.77.0` is now required.

## 0.117.0 - 2026-10-03

Adds the `RejectionCode.TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH` member (`tckdb-schemas` 0.81.0, #638):
a transition-state `energy_ordering` energy that contradicts the energy TCKDB stores for the calculation
it cites is refused. An `e0` energy built with a scaled zero-point energy states the new optional
`zpe_scale_factor` (same release). Nothing in the client's code changes.

## 0.116.0 - 2026-10-03

Adds the `RejectionCode.NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH` member (`tckdb-schemas` 0.79.0, #668):
the network-PDep upload now refuses a state energy, barrier or `well_energy` / `barrier_energy` source
link whose cited calculation belongs to another subject than the one the energy is stated for (a
species outside the state, another transition state, or a species calculation for a barrier). Nothing
in the client's code changes.

## 0.113.0 - 2026-10-03

Adds the `RejectionCode.NETWORK_ENERGY_SOURCE_TYPE_MISMATCH` member (`tckdb-schemas` 0.76.0, #642): the
network-PDep upload now refuses a state energy, barrier energy or `well_energy` / `barrier_energy` source
link whose cited calculation is of a type that carries no such energy (an IRC, scan, path search or
conformer search; a `freq` for an `electronic_only` energy). Nothing in the client's code changes.

## 0.112.0 - 2026-10-03

A polyatomic species whose only calculation is a single point that declares no geometry is told what
is actually wrong again (#623). `ComputedSpeciesUpload` and `ComputedReactionUpload` now raise
"primary_calculation.type must be 'opt', got 'sp'; the bundle endpoint anchors each conformer on an
opt" (species) and "must contain at least one opt calculation" (reaction), as they did before the
one-atom `sp` primary was accepted, instead of "must declare output_geometry or input_geometry".
Both messages now also say that a single atom may anchor on its `sp` when that `sp` declares the
atom's geometry. Builders raise `TCKDBBuilderValidationError`, which carries no code, so no code
changes. A calculation that does declare a geometry is judged exactly as before, and an `opt`
primary without a geometry still gets the geometry message.

## 0.111.0 - 2026-10-03

Reads gain the composite annotations of ADR 0021, phase P7a (`tckdb-schemas` 0.73.0).
The client's read methods return the server's JSON as dictionaries, so nothing in the client's code
changes; this release records which keys a caller may now find, all additions:

- `levels` on a thermo, statmech or kinetics record gains `notation` (for example
  `CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP`, or `CBS-QB3`; `null` when the energy or geometry level
  is absent), `composite_energy_verification` (`state`: `recomputed`, `recompute_mismatch`,
  `log_reconciled`, `program_reported` or `unverifiable`, with an optional `reason`,
  `difference_hartree` and `tolerance_hartree`; `null` unless the energy comes from a composite) and
  `legacy_composite_shape` (`null` unless the record was deposited the way it was before the
  `composite` calculation type existed).
- A calculation record gains `composite_energy_verification` and `legacy_composite_shape`.
- A level-of-theory summary gains `aux_basis`, `cabs_basis` and `solvent_model` (`null` when not stated), and `notation` writes every stated part of each level: `method/basis (aux=..., cabs=..., disp=..., solvent=model:name, spin=..., core=...)`, so levels that differ only in dispersion, solvent model, spin or core treatment read differently.
- The ML-dataset export's level-of-theory `label` is written by the same renderer, so its text changes for a level that states a spin treatment, a core treatment or an auxiliary or CABS basis (it already wrote dispersion and solvent). `lot_hash` stays the key; the label is not.
- A level-of-theory summary's `composite_scheme` gains `geometry_level_of_theory_ref`.
- `GET /scientific/composite-schemes/{ref}`: each term gains `linearity`, each term input a
  `coefficient`, and the record `linear_in_energies`.
- `GET /levels-of-theory` returns `core_treatment` and `spin_treatment` and accepts a `core_treatment`
  filter, as do the scientific level-of-theory search and browse reads; the ML-dataset level-of-theory
  block carries `core_treatment`.

## 0.110.0 - 2026-10-02

`RejectionCode` gains the codes of user-built composite schemes, regenerated from
the server's catalogue (ADR 0021, `tckdb-schemas` 0.72.0):
`calculation_software_release_required`, `composite_assembled_cannot_be_primary`,
`composite_input_duplicate`,
`composite_input_edge_is_derived`, `composite_input_geometry_mismatch`,
`composite_input_level_mismatch`, `composite_input_missing`,
`composite_input_owner_mismatch`, `composite_input_reference_invalid`,
`composite_input_slot_unknown`, `composite_input_type_invalid`,
`composite_inputs_require_assembled`, `composite_scheme_malformed`,
`composite_scheme_named_method_not_sendable`, `composite_scheme_nested`,
`composite_total_mismatch`, `composite_total_required`,
`level_of_theory_method_with_composite_scheme`,
`level_of_theory_requires_method_or_composite_scheme` and `sp_energy_component_derived`. They are the refusals of
the new `composite_scheme` level of theory and the assembled composite, whose
payload shapes ship in `tckdb-schemas`. Nothing else in the client changes: the
typed builders still build only `opt`, `freq` and `sp` calculations, and a
composite calculation is sent as a payload.

## 0.109.0 - 2026-10-01

`RejectionCode` gains ten codes, regenerated from the server's catalogue
(ADR 0021, `tckdb-schemas` 0.70.0): `composite_assembled_not_accepted`,
`composite_e0_inconsistent`, `composite_level_not_scheme_bound`,
`composite_program_run_requires_software`,
`composite_result_requires_composite_type`, `composite_term_position_unknown`,
`composite_terms_do_not_sum`, `composite_type_requires_composite_result`,
`statmech_energy_sp_and_composite_linked` and
`thermo_energy_sp_and_composite_linked`. They are the refusals of the new
`composite` calculation type, whose payload shape ships in `tckdb-schemas`. A
conformer primary of type `composite` now passes the builders' primary-type
check (the rule is `tckdb-schemas`'). The typed builders still build only
`opt`, `freq` and `sp` calculations; send a composite calculation as a payload.
Nothing else in the client changes.

## 0.108.0 - 2026-10-01

`RejectionCode` gains `sp_energy_component_not_on_sp`,
`sp_energy_components_require_energy`, `sp_energy_component_duplicate`, `sp_energy_component_total_mismatch` and
`sp_energy_components_do_not_sum`, regenerated from the server's catalogue
(ADR 0021, `tckdb-schemas` 0.69.0). They are the refusals for the new
`sp_energy_components[]` field on single points. Reads of a level of theory now carry
`core_treatment` (`frozen_core`, `all_electron` or `null`) and a single point's
result carries `energy_components`; both arrive in the existing JSON shapes. The
client's request builders are unchanged.

## 0.107.0 - 2026-10-01

`Client.get_composite_scheme(ref)` reads `GET /scientific/composite-schemes/{ref}`
(ADR 0021, `tckdb-schemas` 0.68.0): the recipe behind a composite level of
theory such as CBS-QB3, with its internal levels, terms and the levels of theory
bound to it. A level of theory's own summary now carries `composite_scheme`
(`composite_scheme_ref`, `kind`, `name`, or `null` for an ordinary level); pass
that ref here. New typed shapes `CompositeSchemeRecord` and
`CompositeSchemeDetailResponse`. Nothing else in the client changes.

## 0.106.0 - 2026-10-01

Docs and help text only: README, `examples/query_cookbook.py` and
`examples/scientific_reads.py` now say that `temperature_min` / `temperature_max`
on the thermo and kinetics reads only fill each record's coverage field (they are
not filters) and that the top record is chosen by review status, then newest. No
API change.

## 0.105.0 - 2026-10-01

Docstring-only: the thermo search recipe in `examples/query_cookbook.py` now
describes the server's thermo order (review status, then newest). No API change.

## 0.104.0 - 2026-10-01

`RejectionCode` gains `level_of_theory_method_is_compound`, regenerated from the
server's catalogue (ADR 0021, `tckdb-schemas` 0.67.0). A level of theory whose
`method` contains `//` (`"x//y"`, an energy level and a geometry level written as
one name) is now refused: send the single-point and the optimization levels as
separate calculations, each with its own level of theory. Nothing else in the
client changes.

## 0.103.0 - 2026-10-01

`RejectionCode` gains `energy_correction_scheme_frequency_level_not_applicable`
and `energy_correction_scheme_frequency_level_without_energy_level`, the two
refusals for `EnergyCorrectionSchemeRef.frequency_level_of_theory`
(`tckdb-schemas` 0.66.0). No client behaviour changed.

## 0.102.0 - 2026-09-30

`Kinetics.modified_arrhenius(..., T0=...)` carries the Arrhenius reference
temperature (#620), matching `tckdb-schemas` 0.63.0, which now requires
`>=0.63.0`. `T0` is the temperature `A` was fitted at, in K, so the rate is
`A (T/T0)^n exp(-Ea/RT)`; leave it out for the plain `A T^n` form. It is sent
as `t0_k` and only when it is not 1 K, so a payload built without it is
byte-identical to one built before. Pass the fit's own T0 instead of folding
`A / T0**n` into `A`: the server then stores what was fitted. The bundle
kinetics block also accepts `interpretation_assignments`,
`tunneling_application` and `network_kinetics_ref` now, but these builders do
not emit them yet: they cite records that must already exist by public ref.

## 0.101.0 - 2026-09-30

`RejectionCode` gains three members, regenerated from the server's catalogue:
`transport_source_calculation_owner_mismatch`,
`scf_stability_source_calculation_owner_mismatch` and
`scf_stability_source_geometry_mismatch`. The first two are a cross-species
source calculation on a computed-reaction bundle's transport or SCF-stability
block; the third is a stability verdict whose measuring job is on another
conformer than the calculation carrying it (#622, `tckdb-schemas` 0.61.0). The
first was already a code the server could raise; it is now one a depositor can
receive, so it is exported. Nothing else in the client changes.

## 0.100.0 - 2026-09-30

`upload_artifacts(batch_by_calculation=True)` no longer keeps only the response
body. Each `ArtifactUploadBatchResult` now also carries `status_code`,
`request_id` (the server's `X-Request-ID`), `replayed` (the server answered from
a stored `Idempotency-Key` receipt) and `warnings` (the body's `warnings`, as a
tuple). `response` is unchanged, so existing callers keep working; the four new
fields have defaults, so code that builds the dataclass itself keeps working
too. `TCKDBResponse` gains a `request_id` property. An adapter that called
`request_json` itself to get these can go back to `upload_artifacts`.

## 0.99.0 - 2026-09-30

A single atom may anchor its conformer on an `sp` (#610), matching
`tckdb-schemas` 0.59.0, which now requires `>=0.59.0`. `ComputedSpeciesUpload`
and `ComputedReactionUpload` no longer refuse a non-`opt` primary
calculation outright: they apply the schema's own rule to the conformer
geometry they will send, so an `sp` primary is accepted when that geometry is
one atom, and refused (with the atom count) otherwise. With no explicit
`primary_calculation=` and no `opt`, the first `sp` is the candidate. Anything
with two or more atoms is unchanged. Producers should stop relabelling an
atom's single point as an `opt`.

## 0.98.0 - 2026-09-30

Adds `RejectionCode.BUNDLE_TOO_LARGE` (HTTP 413) and
`RejectionCode.BUNDLE_TOO_MANY_RECORDS` (HTTP 422). `POST /api/v1/bundles/dry-run`
and `/submit` now refuse a bundle whose request body exceeds the server's
`BUNDLE_MAX_BODY_BYTES` (default 5 MiB) or that carries more than
`BUNDLE_MAX_RECORDS` (default 500) thermo plus kinetics records; split the
bundle. The dry run also has a tighter rate limit than uploads.

## 0.97.0 - 2026-09-29

Artifact uploads address a calculation by its `calc_` ref. Upload results now
carry `calculation_ref` beside `calculation_id` (and `calculation_key_refs`
beside `calculation_keys` on a computed reaction), and `submission_ref` beside
`submission_id`. `PlannedArtifactUpload` gains an optional `calculation_ref`,
filled from those responses, and `upload_artifacts` / `upload_artifact` send it
in the path when they have one, falling back to the integer against a server
that predates it. `upload_artifact` now accepts either form. Nothing existing
is removed: plans built without refs behave as before.

Retry caveat: idempotency keys are scoped by URL path. An artifact upload
committed by 0.96 through `/calculations/{integer}/artifacts` whose response
was lost, then retried by 0.97 with the same key, goes to the `calc_` path, is
not treated as a replay, and attaches duplicate artifacts. Finish in-flight
uploads before upgrading.

## 0.96.0 - 2026-09-29

Adds `RejectionCode.SUBMISSION_SUPERSEDE_NOT_OWNER` (HTTP 403). The server now
refuses `POST /api/v1/submissions/{submission_ref}/supersede` unless the caller
created both submissions or holds the curator or admin role. The client has no
typed method for this route; a raw `post_json` caller can branch on the code.

## 0.95.1 - 2026-09-29

The parity table follows the server renaming the submission supersede route
(issue #571): it is now `POST /api/v1/submissions/{submission_ref}/supersede`,
and its body is `{"new_submission_ref": "sub_..."}`. Both submissions are named
by their public ref (`public_ref` on a submission read), and a row id is
refused with 422 in the path and in the body. The client has no typed method
for this route and never sent the integer, so nothing here changes behaviour;
a raw `post_json` caller must send the refs.

## 0.95.0 - 2026-09-29

A 2xx response that is not the API's answer now raises the new
`TCKDBUnexpectedResponseError` instead of being returned (issue #568). Before,
a JSON endpoint that answered 200 with a body that is not JSON handed that
body back as a string in `data`; the usual cause is a `base_url` naming the
site root (`https://host`) rather than the API root (`https://host/api/v1`),
where the web app answers every path with its HTML page, and callers then
failed later with a bare `TypeError`. The message names the request
(`GET https://host/scientific/calculations/... returned HTTP 200 with an
HTML page`) and, for HTML, the base URL to use instead. The client still
does not append `/api/v1` itself: `base_url` is documented as the API root.

`TCKDBUnexpectedResponseError` subclasses `TCKDBHTTPError`, so existing
`except TCKDBHTTPError` handlers catch it; it carries `url`, `content_type`,
`status_code`, `response_text` and `headers`, and `code` is `None` because
no server sent one. The NDJSON and Chemkin exports refuse an HTML 200 the
same way. `download_artifact` does not: the server labels a download by its
stored filename, so an uploaded `.html` file is a legitimate `text/html` 200.
An empty success body (204) is still `data=None`.

## 0.94.0 - 2026-09-29

`RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` now also covers ARC
and RMG (`RMG-Py`), not only Arkane: the server refuses a calculation whose
`software_release.name` is any of them (issue #305, owner decision), in any
case or spelling, with or without a trailing version (`ARC 1.1.0`,
`ARC-1.1.0`, `rmgpy`, `rmg_py`, `RMG Py`, `RMG-Py 3.2.0`). A
calculation's software is the electronic-structure program that ran the job;
put ARC in `workflow_tool_release`. No enum member changed. A product's
`analysis_software_release` is still unaffected, so RMG remains valid there.

The server also now fills a missing version from an uploaded output log: a
calculation declared with no `software_release.version` whose `output_log`
banner names the same program with a version is re-pointed at that versioned
release, and the upload returns an informational
`software_release_version_filled_from_artifact` warning.

## 0.93.0 - 2026-09-28

Adds `RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` (HTTP 422). The
server now refuses a calculation whose `software_release.name` is a workflow
tool (Arkane): a calculation's software is the electronic-structure program
that ran it. Declare that program instead, and put Arkane in
`workflow_tool_release` if it orchestrated the job. A thermo, statmech or
kinetics record's `analysis_software_release` is unaffected (issue #305).

## 0.92.0 - 2026-09-27

Requires `tckdb-schemas>=0.51.0`. The thermo builder (`Thermo`,
`Thermo.points`) now refuses tabulated Gibbs energies (`g_kj_mol`) without
`enthalpy_reference_kind`, with `enthalpy_declaration_absent`, and accepts
them when `formation_298k` is declared, even with no point enthalpy. A
stored G sits on the record's enthalpy zero, so it needs the same
declaration an enthalpy does. This matches the server, which applies the
same shared rule; with an older `tckdb-schemas` the builder would pass a
deposit the server refuses.

## 0.91.0 - 2026-09-23

**Breaking:** `tckdb` (the `get reaction`/`download artifact` CLI) no longer
defaults `--base-url` to a hardcoded deployment. Pass `--base-url`, or set
`TCKDB_BASE_URL`; omitting both is now a clear error naming both ways to
supply it, instead of a silent request to that host. If you relied on the
default, add `--base-url` or `TCKDB_BASE_URL` to your invocation. See issue
#521.

## 0.89.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.## 0.170.0 - 2026-10-25

## 0.170.0 - 2026-10-25

- Requires `tckdb-schemas>=0.137.0`: a statmech declaration's ensemble member states the convention of its relative energy
  (`relative_energy_convention`), required with `relative_energy_kj_mol`. The client carries declarations as the wire models, so
  nothing else changes.

## 0.169.0 - 2026-10-25

A kinetics record can state the bath gas it was obtained for.

- `Kinetics.modified_arrhenius` takes `pressure_context`, `pressure_bar` and `bath_gas`, a list of `(Species, mole_fraction)` pairs. The species join the upload's
  species list and are sent by bundle key. Nothing is inferred or defaulted; the builder refuses, with the server's codes, a bath gas at `high_p_limit`
  (`kinetics_bath_gas_at_high_pressure_limit`), fractions that do not sum to 1 (`kinetics_bath_gas_mole_fraction_sum`) and one species twice
  (`kinetics_bath_gas_duplicate_species`).
- The three codes join `RejectionCode`. A kinetics record read back carries `bath_gas` (`KineticsDetailRecord`), null when none was stated.
- Requires `tckdb-schemas>=0.136.0`.

## 0.168.0 - 2026-10-25

A calculation dependency that would close a cycle is refused with a coded 409.

- New rejection code `calculation_dependency_cycle` (`RejectionCode.CALCULATION_DEPENDENCY_CYCLE`). `context` carries
  `parent_calculation` and `child_calculation`, both public references. Nothing else about the client changes.

## 0.167.0 - 2026-10-25

Every list read can be walked by cursor, and every `iter_*` helper does.

- Each `search_*` / `browse_*` / `get_*` list method takes `cursor`, and the response carries `next_cursor` (`None` on the last page) and
  `watermark`. `pagination.mode` says whether a page was served by `offset` or by `cursor`.
- All `iter_*` helpers follow `next_cursor` (they used to count offsets, except the four analytics ones). A traversal over a corpus that is
  being written to no longer repeats or skips a record. Passing a non-zero `offset` to an `iter_*` helper still walks by offset, from there.
- `offset` is random access and is deterministic only for a static corpus; use it for "page N of M". The cursor pins a traversal to the
  records that existed when it began; it does not freeze review state. A cursor is bound to its endpoint, filters and read profile
  (`cursor_query_mismatch`), and cannot be combined with a non-zero `offset` (`cursor_offset_conflict`).
- The analytics request echo no longer carries `pagination_mode`; it is `pagination.mode`.

## 0.166.0 - 2026-10-24

A select call takes the caller's filters, and today's exclusions are named defaults the request can lift.

- `select_species_thermo`, `select_reaction_kinetics`, `select_network_kinetics`, `select_species_calculations`, `select_conformer_basins`,
  `select_transition_state_evidence` and their `get_*_selection_manifest` twins take `filters` and `eligibility`. `select_statmech_models`
  and `get_statmech_selection_manifest` send them as part of `request`. Each is sent only when given; the client invents neither.
- `filters` uses the matching search's vocabulary: thermo and kinetics `model_kind`, `level_of_theory_ref`, `software`; network and statmech
  `model_kind` plus `method`, `basis`, `software`, `software_version`, `workflow_tool`, `workflow_tool_version`; structure the six provenance
  fields. A filter the search does not offer is a 422, never ignored. A filter excludes on a fact and ranks nothing.
- `eligibility` lifts named defaults: `include_rejected`, `include_deprecated` (every route), `include_hard_failed_evidence` (thermo,
  kinetics), `include_confirmed_findings` (thermo, kinetics, statmech, structure). A rejected calculation quality is lifted by listing `rejected`
  in `permitted_quality`. The read profile's review floor is not a default: `curated` cannot be overridden.
- Every answer gains `filters` (the normalised filters and how many candidates they removed), `eligibility` (each default: `name`, `applied`,
  `source` of `default`, `request` or `read_profile`, `excluded`, `admitted_by_override`) and `funnel` (`considered`, `removed_by_filters`,
  `removed_by_eligibility`, `remaining`). Each candidate or unit gains `blocked_by` and `passed_only_by_override`. The network answer gains `solves`.
- `funnel` adds up: `removed_by_filters + removed_by_floor + removed_by_eligibility + remaining` is `considered`, each candidate counted once (the
  per-default `excluded` counts can overlap). `removed_by_floor` is the caller's own `min_review_status`. `filters.applied` lists a narrower
  `permitted_quality`, `member_refs` and a `model_revisions` population too, and `filters.note` says so when the filters removed everything.
  A determination with no conformer observation reports `review_status: null` (its species entry's status is not copied onto it), and a
  basin hides with a conformer group below the profile.
- A species or reaction entry is a scoping parent: its own review status no longer excludes a statmech candidate, a calculation or a conformer
  basin. A transition-state entry still gates its evidence and statmech candidates.
- Recorded decisions now carry the filters and eligibility settings, so replay reproduces them. Manifest formats move to version 3
  (`thermo`, `kinetics`, `network`, `statmech`, `structure`, relative conformer energies); a manifest in format 2 is refused on replay.
- New TypedDicts: `SelectionFilters`, `SelectionEligibilityIn`, `EligibilityDefaultEcho`, `SelectionFunnel`, `SelectionFilterAnswer`.

- `PublicAssessmentSummary.deterministic_trust` is now the typed `DeterministicTrustSummary` (it was a plain `JSONDict`), and both it and
  `ReproducibilityAssessmentSummary` gain `made_under` (new `MadeUnder` type): the rubric an assessment was made under, the version it was made under, the
  current version, `state` (`current`, `stale`, `unassessed`, `inputs_unchecked`), `stale_reason` and `inputs_checked`. `ReproducibilityAssessmentSummary.state`
  takes `inputs_unchecked` too: a list or search read compares the rubric version only and never says `current`. Version 0 means stamped before the 2026-10
  versioning reset. Additive.
- `get_species_thermo` and `get_reaction_kinetics` take `check_inputs` (with `include=assessments`, at most 10 records): compare each stored assessment's
  inputs with today's. Requires `tckdb-schemas>=0.134.0`. `RejectionCode` gains `assessment_input_check_limit`, `check_inputs_needs_assessments` and `check_inputs_not_supported`. `MadeUnder` gains `inputs_unchecked_reason` (`curated_profile`).

## 0.164.0 - 2026-10-24

- The deprecated `GET /species-entries/{id}/conformer-observations/lowest-sp` route is removed from the server (it was a silent pick and named records the profile hides); the parity table drops it. Use `relative_conformer_energies`.

## 0.163.0 - 2026-10-24

- `RejectionCode` gains `lowest_energy_across_levels` (the `/lookup` refusal above).
- `get_calculation_hessian` takes `profile`: the legacy entity reads now honour the read profile like the scientific reads.
- Requires `tckdb-schemas>=0.133.0`.

## 0.162.0 - 2026-10-23

No read and no export picks a record for you. Browse reads list every match in display order; "give me one" is the `select_*` methods.

- `collapse` is gone from every read method (`search_species`, `browse_species`, `search_reactions`, `browse_reactions`, `get_reaction_kinetics`,
  `get_species_thermo`, `search_thermo`, `search_kinetics`, `search_species_calculations`, and the other searches that took it). The server refuses
  `collapse` and `selection_policy` with 422 `browse_parameter_removed`, which names the select endpoint to use.
- On `get_species_thermo`, `search_thermo`, `get_reaction_kinetics` and `search_kinetics`, `temperature_min` / `temperature_max` are now
  `coverage_temperature_min_k` / `coverage_temperature_max_k`: they never filtered, they only set the window each record's `temperature_coverage`
  is computed against. The old names get 422 `parameter_renamed`. `pressure_bar` on the kinetics reads is unchanged (it does filter).
- A thermo and a kinetics record carry `created_at` (deposition time), the second key of the display order. `OrderEcho` gains `requested_by`
  (`"ranking=lowest_energy"`, `"mode=substructure"`; `None` for the default order). The statmech and transport `effective_order`,
  `selection_policy` and `selection_policy_applied` echoes are gone.
- `pagination.post_collapse_total` is gone (the server no longer sends it and the `Pagination` type drops it); `iter_paginated_records` no longer
  reads `collapse` and follows `pagination.total`.
- `export_ndjson` and `export_chemkin` no longer take `selection_policy` (the server refuses it). `export_chemkin` takes `thermo_refs`,
  `transport_refs` and `kinetics_refs`: Chemkin holds one record per species or reaction and the server never picks, so name the record where a
  species or reaction has several candidates, or the call fails with 422 `export_selection_ambiguous` (which lists every ambiguous subject).
- The ML species export's record carries `thermos`, `frequency_results` and `hessians` (lists); the ML reactions record carries
  `transition_states`, `barriers` and `barriers_unavailable` in place of `transition_state` / `barrier`. Format `tckdb.ml` version 2.
- Requires `tckdb-schemas>=0.132.0`.
- `RejectionCode` gains `browse_parameter_removed`, `parameter_renamed`, `export_selection_ambiguous` and `export_ref_unmatched`.

## 0.161.0 - 2026-10-22

The method-preference rules are gone from the selection endpoints, and the browse order is named in every list response.

- `select_species_thermo`, `select_reaction_kinetics` and `select_network_kinetics`: `policy` is `default` (the server default), `most_reviewed` or
  `latest`. `method_preferred` is retired (the server refuses it). Every policy is an administrative display order, never a method claim.
- The selection response types lose `policy.rules[]`, `relations`, `rule_matches` and `pair_checks`; the outcome tokens lose `policy_preferred` and
  `policy_conflict`, and a pick's `basis` is `sole_eligible_candidate` or `administrative_first`.
- `select_network_kinetics` no longer takes `objective`, `reference_model_ref` or `reference_outputs`.
- The structure selection methods no longer take `apply_rules`, `objective` or `reference_model`; `StructureSelectionOutcomeToken` has 11 values.
  `protocol_coverage` now lists the protocols that cover every requested member, in display order.
- `StatmechSelectionRequest` loses `comparison_objective` and `reference_model_ref`; `StatmechSelectionResponse` loses `edges`, `conflicts`,
  `undecided` and `protocol` and gains `administrative_order` and `administrative_key`; `StatmechSelectionSelection` loses `scientific_ref`;
  the outcome token loses `policy_preferred`, `policy_conflict` and `unresolved_comparability`.
- `request.sort` on every browse and list response is a structured `OrderEcho` (`kind: "display_order"`, `keys`, `statement`), not a string; its keys
  carry a direction and a meaning (`created_at` is deposition time). Statmech's `effective_order` has the same shape. `OrderEcho` and
  `OrderKeyEcho` are exported.
- Decision manifests returned by the selection routes are format version 2; the shape that held rules and edges is refused on replay.

## 0.160.0 - 2026-10-21

Follows `tckdb-schemas` 0.131.0 (review fixes to versioning PR-4). The floor is raised to `tckdb-schemas>=0.131.0`: the client now speaks the
`ParserStamp` / `{name, version}` wire that older schemas packages do not have, and the old floor (`>=0.116.0`) let a resolver pick one of them.

- The selection manifests the client returns (`RelativeConformerEnergyManifest`, `RelativeConformerEnergyCompareManifest`, and the engines' manifests,
  which the client passes through untouched) open with a root `format` block, `{"name": ..., "version": ...}`, in place of
  `manifest_format_version` (and, for the relative-energy manifests, the separate `manifest_format` name string). A manifest's
  `assessment_semantics` block gains a `name`, and its `source_findings` is a `{name, version}` pair instead of a bare integer.
- `tckdb-qcschema` 0.9.1: its floors are now `tckdb-client>=0.160.0` and `tckdb-schemas>=0.131.0` (they were unpinned).

## 0.159.0 - 2026-10-20

Follows `tckdb-schemas` 0.130.0 (versioning PR-4: a name and an integer version everywhere, every TCKDB-owned version reset to 1).

- The replay sidecar's `bundle_format_version` is the integer `1` (it was the string `"0"`); `DEFAULT_FORMAT_VERSIONS` is `(1,)`. A sidecar written by an
  older client holds `"0"` and is marked failed with `unsupported bundle_format_version`; re-export the bundle.
- `ReproducibilityAssessmentSummary.rubric_version` is `int | None` (was `str | None`).
- `ExecutionEnvironmentManifestRecord` carries `schema_name` and an integer `schema_version`.
- `StatmechSelectionRequest`-shaped records carry `contract_version: 1` (was `"statmech-selection-v1"`).

## 0.158.0 - 2026-10-20

`RejectionCode` gains one member, `unknown_manifest_schema` (409), for a release manifest whose stored format tag this server cannot
render. `tckdb-schemas` is 0.128.0 (the same code in the shipped contract).

## 0.157.0 - 2026-10-19

The two curator routes for structure-evidence findings (`POST` and `GET /api/v1/curation/structure-evidence-findings`) are registered in
the parity matrix as curation workflow, like the other curator routes: no typed method, reachable through the raw request helpers.
`RejectionCode` gains twelve members, all prefixed `structure_finding_`, for the refusals those routes can return.
`tckdb-schemas` is unchanged.

## 0.156.0 - 2026-10-18

`NetworkSolveRecord.network` and `NetworkKineticsRecord.network` are `JSONDict | None`: under `profile="curated"` a visible
network solve (or fit) whose network is not approved comes back with `network` null instead of naming the network (the key is
always present). Code that indexes `record["network"]["network_ref"]` under a type checker now needs a `None` check; the default
profile never returns `None`. No method changed, and `tckdb-schemas` is unchanged.

## 0.155.0 - 2026-10-18

`TransitionStateEntryRecord.transition_state` is `JSONDict | None`: under `profile="curated"` a visible transition-state entry whose
transition state is not approved comes back with `transition_state` null instead of naming the owner (the key is always present).
Code that indexes `record["transition_state"]["transition_state_ref"]` under a type checker now needs a `None` check; the default
profile never returns `None`. Also under `profile="curated"`, a kinetics record's `determination`, `tunneling_application` and
`provenance["irc"]` are `None` when the transition-state entry, network channel or calculation they name is not approved
(documented in the type's comment). No method changed, and `tckdb-schemas` is unchanged.

## 0.154.0 - 2026-10-18

Four typed methods for the relative conformer energy reads: `relative_conformer_energies`, `get_relative_conformer_energy_manifest`,
`compare_relative_conformer_energies` and `get_relative_conformer_energy_comparison_manifest` (the four `.../conformers/relative-energies`
routes, previously raw-only). The scientific choices are required keyword arguments with no default: the reference conformer,
`energy_kind`, `certificate` and the level (exactly one of `level_of_theory_ref` and `composite_scheme_ref`, else `ValueError` before any
request); the comparison also requires `candidate`, `reference` and `pairing`. Optional fields left unset are not sent. The manifest methods
return the server's JSON unchanged, for replay. New types: `RelativeConformerEnergyResponse`, `RelativeConformerEnergyManifest`,
`RelativeConformerEnergyCompareResponse`, `RelativeConformerEnergyCompareManifest`, `RelativeEnergyMember`, `RelativeEnergyComparePair`,
`RelativeEnergyCompareClaims`, `RelativeEnergyPairing`. No new dependency.

## 0.153.0 - 2026-10-18

`RejectionCode` gains `relative_conformer_energy_compare_identical_protocols` (`tckdb-schemas` 0.125.0): the code of the new server read
`POST /scientific/species-entries/{ref}/conformers/relative-energies/compare` (a descriptive comparison of two stored protocols). No
method changed; a client method for the route is a separate release.

## 0.152.0 - 2026-10-17

`ConformerObservationRecord.conformer_group` is `JSONDict | None`: under `profile="curated"` a visible observation whose conformer
group is not approved comes back with `conformer_group` null instead of naming the group. Code that indexes
`record["conformer_group"]` under a type checker now needs a `None` check; the default profile never returns `None`. Also under
`profile="curated"`, `StatmechRecord.species` and `StatmechRecord.transition_state` are both `None` when the record's subject is a
transition-state entry that is not approved (documented in the type's comment). No method changed, and `tckdb-schemas` is unchanged.

## 0.151.0 - 2026-10-17

`RejectionCode` gains `relative_conformer_energy_unsupported`, `relative_conformer_energy_population_too_large` and
`relative_conformer_energy_scope_owner_mismatch` (`tckdb-schemas` 0.124.0): the codes of the new server read
`POST /scientific/species-entries/{ref}/conformers/relative-energies` (relative conformer energies against a named reference
conformer). No method changed; a client method for the route is a separate release.

## 0.149.0 - 2026-10-13

`RejectionCode` gains `superseded_record_not_approvable` (`tckdb-schemas` 0.123.0): a curator re-approving a record that a correction
has replaced is refused with 409 on `PATCH /record-reviews/{type}/{id}`. Submission approval does not return it (it leaves a replaced
member deprecated). No method changed.

## 0.147.0 - 2026-10-12

`search_statmech` (and `iter_statmech`) take `transition_state_entry_ref` and `declared_conformer_group_ref`. The server now
applies the first (it used to ignore it); the second matches only records whose own declaration is that basin. In the typed
`StatmechRecord`, `species` is optional (`None` on a transition-state-owned record), and `transition_state`, `supersession` and
`levels` are added. Code that indexes `record["species"]` under a type checker now needs a `None` check, because
transition-state-owned records always sent `null`. No other method changed.

## 0.146.0 - 2026-10-12

`RejectionCode` gains `calculation_geometry_conformer_mismatch`, `calculation_input_geometry_mismatch`,
`transition_state_calculation_conformer_key` and `transition_state_calculation_species_geometry` (`tckdb-schemas` 0.121.0): a
calculation whose conformer and geometry keys, or geometry and explicit inputs, disagree is now refused. No method changed.

## 0.145.0 - 2026-10-11

Statmech selection: `select_statmech_models` and `get_statmech_selection_manifest`, with typed `StatmechSelectionRequest` and
`StatmechSelectionResponse`. The client sends the request as given, refuses an integer id, a ref of the wrong kind and any
bound, page, sort or rule before a request is made, never chooses a candidate, and returns the server's answer untouched
(`tckdb-schemas` 0.120.0). `RejectionCode` gains `statmech_selection_scope_owner_mismatch`. Each candidate's `advisory` and the response `notices` disclose
`imported_parameters_not_revalidated:<role>` and `source_literature_unidentified:<role>`.

## 0.144.0 - 2026-10-11

`RejectionCode` gains `scf_stability_source_cycle`, `scf_stability_source_conformer_unanchored` and
`ts_energy_ordering_source_type_mismatch` (`tckdb-schemas` 0.119.0). No method changed.

## 0.142.0 - 2026-10-11

Breaking: artifact uploads take the calculation's `calc_` ref only (#578). `upload_artifact(calculation_ref, ...)` raises
`ValueError` for anything that is not a `calc_` ref (an integer included), and `upload_artifacts` checks every plan item
for a `calculation_ref` before sending any request. The integer fallback is gone, along with the server's integer path
form; the server's minimum supported `tckdb-client` is now 0.97.0, the first release that addresses a calculation by its
ref. The client never called `POST /submissions/{submission_ref}/rights-attestations`, which now takes the `sub_` ref
only. Requires `tckdb-schemas` 0.116.0.

Retry caveat, narrowed: idempotency keys are scoped by URL path, so a lost-response retry sent through a different path
form is not a replay. An upload committed by 0.96 through the integer path before this change can still be retried with
the same key on the same integer path until its receipt expires (it replays, `Idempotency-Replayed: true`); a new key on
the integer path is refused (422 `invalid_handle`) and a client below 0.97.0 gets 426.

## 0.141.0 - 2026-10-11

`RejectionCode` gains `statmech_selection_bound_exceeded`, `statmech_selection_unsupported` and `unknown_statmech_candidate_ref` (`tckdb-schemas` 0.114.0).
No method changed; nothing in the server serves them yet.

## 0.140.0 - 2026-10-11

`RejectionCode` gains `statmech_model_declaration_invalid`, `statmech_model_declaration_version_unsupported`,
`statmech_model_declaration_mismatch` and `unknown_structure_determination_ref` (`tckdb-schemas` 0.113.0). No method changed:
the statmech blocks the client builds pass a `model_declaration` through untouched.

## 0.139.0 - 2026-10-06

Structure selection: `select_species_calculations`, `select_conformer_basins`, `select_transition_state_evidence` and
their `get_..._selection_manifest` siblings, with typed requests and `StructureSelectionResponse`. The client invents no default
of the question, sends `quantity=None` as JSON null only (evidence-only qualification), refuses an integer id, a ref of the wrong
kind and any bound, page, sort or rule before a request is made, and returns the server's answer untouched.

## 0.138.0 - 2026-10-05

`RejectionCode` gains `structure_selection_manifest_too_large` (`tckdb-schemas` 0.107.0). No method changed; nothing in the
server serves it yet.

## 0.137.0 - 2026-10-05

`RejectionCode` gains `structure_selection_population_too_large`, `structure_selection_evidence_too_large`,
`structure_selection_traversal_too_deep`, `structure_selection_unsupported` and `unknown_structure_member_ref` (`tckdb-schemas` 0.106.0).
No method changed; nothing in the server serves them yet.

## 0.136.0 - 2026-10-05

`Calculation.opt/freq/sp(..., actual_protocol_declaration=...)` carries a calculation's declared actual protocol
(electronic state and root, spin and relativistic treatment, core potential, core correlation, material numerical
approximations, included corrections) to both bundle wires (`tckdb-schemas` 0.105.0). It is validated against the shared
model when the builder is made; left out it is not sent, which the server reads as "not stated", never as a standard
recipe or gas phase. `RejectionCode` gains `structure_declaration_invalid`, `structure_declaration_version_unsupported`,
`structure_determination_invalid` and `structure_determination_mismatch`.

## 0.135.0 - 2026-10-04

`export_selected_network_kinetics(network_ref, *, manifest, node_ref, representation_refs, format=None,
allow_administrative_choice=None, energy_units=None, naming_policy=None, profile=None)` posts to
`/scientific/networks/{ref}/kinetics/export-selected` and serialises one network selection you saved with
`get_network_kinetics_selection_manifest`. The server replays the manifest, re-checks it against its own content under
one snapshot and refuses a stale, forged or incomplete one with a structured 422 (`network_export_manifest_invalid`,
`network_export_manifest_stale`, `network_export_choice_not_allowed`, `network_export_representation_choice_invalid`,
`network_export_unsupported_form`, now in `RejectionCode`). `allow_administrative_choice` is false on the server unless
you pass true. It takes a public `net_` ref only (an integer id is refused before any request); `None` options are
dropped, never sent as null. `include_reported` (CHEMKIN only) writes a solve of kind `reported` with its literature annotated (ADR 0010). New typed shapes `NetworkSelectedKineticsExportRequest` and `NetworkSelectedKineticsExport`.

## 0.134.0 - 2026-10-04

Method-aware selection among a network's stored pressure-dependent solves, for one stated gas-phase question
(`tckdb-schemas` 0.103.0 only lists the two new read-only routes in the contract). Read-only; the network reads are unchanged.

- `select_network_kinetics(network_ref, *, coefficient_basis, temperature_min_k, temperature_max_k, pressure_min_bar,
  pressure_max_bar, bath, partition, scope=None, channel_key=None, observable=None, outputs=None, ...,
  profile=None)` posts to `/scientific/networks/{ref}/kinetics/select`. It takes a public `net_` ref only (an
  integer id is refused before any request), and `channel_key` is a body field, never part of the path. The question
  is required and never defaulted; optional fields that are `None` are dropped, never sent as JSON null.
- `get_network_kinetics_selection_manifest(...)` returns the replayable decision manifest of the same request
  (`/kinetics/select/manifest`), a new snapshot of the current data and not a retrieval of an earlier selection.
- New typed shapes: `NetworkSelectionRequest`, `NetworkSelectionBathIn`, `NetworkSelectionPartitionIn`,
  `NetworkSelectionBoundaryIn`, `NetworkSelectionRegimeIn`, `NetworkSelectionOutputIn`, `NetworkSelectionResponse`,
  `NetworkSelectionPick`, `NetworkSelectionMember`, `NetworkSelectionDisclosures` and `NetworkSelectionOutcomeToken`.
- A population over a server bound, including more than 200 required outputs, is a 422
  `network_selection_population_too_large`; the client never caps or pages it itself.

## 0.133.0 - 2026-10-04

`RejectionCode` gains `network_selection_population_too_large` and `network_selection_snapshot_too_large`, regenerated from the server's catalogue; nothing else changes. The selection service they belong to is not routed yet.

## 0.132.0 - 2026-10-04

`RejectionCode` gains `network_declaration_invalid`, `network_declaration_version_unsupported` and `unknown_network_solve_ref`, regenerated from the server's catalogue; nothing else changes. The server accepts optional network solve declarations and fit determinations on `POST /uploads/networks/pdep` (`tckdb-schemas` 0.101.0); the client has no network upload builder.

Method-aware selection among a reaction entry's stored rate coefficients, for one stated gas-phase question
(`tckdb-schemas` is unchanged; the producer contract is unchanged). Read-only; browsing (`get_reaction_kinetics`) is
unchanged.

- `select_reaction_kinetics(reaction_entry_ref, *, direction, target, coefficient_basis, temperature_min_k,
  temperature_max_k, pressure, collider=None, policy=None, mode=None, min_review_status=None, phase=None,
  profile=None)` posts to `/scientific/reaction-entries/{ref}/kinetics/select`. It takes a public `rxe_` ref only
  (an integer id is refused before any request). The question is required and never defaulted; the optional fields
  are sent only when you supply them.
- `get_reaction_kinetics_selection_manifest(...)` returns the replayable decision manifest of the same request
  (`/kinetics/select/manifest`), a new snapshot of the current data and not a retrieval of an earlier selection.
- New typed shapes: `KineticsSelectionRequest`, `KineticsSelectionTargetIn`, `KineticsSelectionPressureIn`,
  `KineticsSelectionColliderIn`, `KineticsSelectionResponse`, `KineticsSelectionPick`, `KineticsSelectionCandidate`,
  `KineticsSelectionDisclosures` and the `KineticsSelectionOutcomeToken` vocabulary (`policy_preferred`,
  `incomparable_alternatives`, `sole_eligible_candidate`, `no_applicable_candidate`, `policy_conflict`). A
  population over the server's cap of 500 visible records is a 422 (`kinetics_selection_population_too_large`); the
  client never caps or pages it itself.
- Every rule in this server release is inactive, so `method_preferred` ranks nothing yet: expect
  `incomparable_alternatives` or `sole_eligible_candidate`, with the administrative order and the reasons.

## 0.130.0 - 2026-10-04

`RejectionCode` gains `kinetics_energy_level_contradiction`, regenerated from the server's catalogue; nothing else changes.

## 0.129.0 - 2026-10-04

`RejectionCode` gains `kinetics_selection_population_too_large`, regenerated from the server's catalogue; nothing
else changes. The server-side selection service it belongs to is not routed yet.

## 0.128.0 - 2026-10-04

`Kinetics.modified_arrhenius` can state a fit's `direction`, `determination`, `applicability` and `protocol`
(`tckdb-schemas` 0.96.0), optional attributed claims checked here by the rule the server applies and never
defaulted. A protocol's supporting calculations are given as `protocol_calculations`, `(purpose, Calculation)`
pairs the assembler names by bundle key. A fit that declares nothing emits the payload it always did.
`KineticsDetailRecord` gains `determination`, `applicability`, `protocol` and `declaration_unreadable`.
`RejectionCode` gains the kinetics declaration codes, regenerated from the server's catalogue.

## 0.127.0 - 2026-10-04

Adds the `RejectionCode.NETWORK_STATE_ENERGY_SUM_MISMATCH` member (`tckdb-schemas` 0.95.0, #678): the
network-PDep upload now refuses a state energy that contradicts the sum of the energies stored for the
per-participant source calculations it cites. Nothing in the client's code changes.


## 0.126.0 - 2026-10-03

Method-aware selection of a species entry's thermo record for the gas-phase formation enthalpy at
298.15 K (`tckdb-schemas` 0.90.0 carries the matching producer-contract note). Read-only; browsing
(`get_species_thermo`) is unchanged.

- `select_species_thermo(species_entry_ref, *, target, policy=None, result_mode=None,
  min_review_status=None, temperature_k=None, phase=None, profile=None)` posts to
  `/scientific/species-entries/{ref}/thermo/select`. It takes a public `spe_` ref only (an integer id is
  refused before any request) and sends only the fields you supply.
- `get_species_thermo_selection_manifest(...)` returns the replayable decision manifest of the same
  request (`/thermo/select/manifest`).
- New typed shapes: `ThermoSelectionRequest`, `ThermoSelectionTargetIn`, `ThermoSelectionResponse`,
  `ThermoSelectionPick`, `ThermoSelectionCandidate`, `ThermoSelectionDisclosures` and the
  `ThermoSelectionOutcomeToken` vocabulary (`policy_preferred`, `incomparable_alternatives`,
  `sole_eligible_candidate`, `no_applicable_candidate`, `policy_conflict`). A population over the server's cap of 500 visible records is a 422 (`thermo_selection_population_too_large`).
- `RejectionCode` gains `thermo_selection_condition_conflict` and `thermo_selection_population_too_large`, regenerated from the server's catalogue.

## 0.125.0 - 2026-10-03

Adds the `RejectionCode.GEOMETRY_ISOTOPE_SYMBOL_CONFLICT` member (`tckdb-schemas` 0.89.0, #672). A
`D`/`T` element spelling now means deuterium/tritium: an `isotopes` entry that contradicts the spelling
(`D` with mass 3 or 1, `T` with mass 2) is refused with this code. **A `D`/`T`-spelled geometry on a
protium species, previously accepted, is now refused** with the existing
`species_geometry_isotope_mismatch`; label the species (`[2H]O[2H]`) or spell the atom `H` with
`isotopes`. Nothing in the client's code changes.

## 0.122.0 - 2026-10-03

Adds the `RejectionCode.CALCULATION_GEOMETRY_ISOTOPE_MISMATCH` member (`tckdb-schemas` 0.78.0, #666): an
upload is refused when a geometry linked to a calculation carries different isotope substitutions than
the species (or, for a transition state, the reaction's reactants) the calculation is filed under.
Nothing in the client's code changes.

## 0.121.0 - 2026-10-03

Thermo builders can state what a record's values describe and how they were produced
(`tckdb-schemas` 0.85.0). Both are optional attributed claims; leave them out and the payload is
byte-for-byte what it was.

- `Thermo.scalar` / `Thermo.nasa` / `Thermo.points` accept `thermodynamic_target`
  (`"equilibrium_ensemble"` or `"single_conformer"`), `protocol` (a `ThermoProtocolDeclaration` or the
  equivalent dict, without its supporting calculations) and `protocol_calculations` (the
  `Calculation` builders the protocol rests on, named by bundle key at assembly).
- A `single_conformer` target names the species' own conformer: `ComputedSpeciesUpload` and
  `ComputedReactionUpload` resolve it to the conformer key they mint, and refuse it
  (`TCKDBBuilderValidationError`) when the species has no conformer in the upload. The reaction
  block's `thermo` keeps its place in the species block either way.
- The builder runs the shared `thermo_declaration_error` rule before any request, so a
  self-contradicting target, an unknown protocol field or an unsupported protocol version is refused
  locally with the server's code in the message (`thermo_target_group_required`,
  `thermo_protocol_version_unsupported`, ...).
- `RejectionCode` gains the codes above, regenerated from the server's catalogue:
  `thermo_target_group_required`, `thermo_target_group_not_allowed`,
  `thermo_target_group_owner_mismatch`, `thermo_declaration_invalid`,
  `thermo_protocol_version_unsupported`, `thermo_protocol_calculation_owner_mismatch`.
- Thermo reads gain `thermodynamic_target` (`kind`, `conformer_group_ref`) and `protocol`, both `null`
  on a record that declared nothing. `ThermoDetailRecord` types them.
- `tckdb-schemas>=0.85.0` is now required.
- `tckdb-schemas>=0.77.0` is now required.

## 0.117.0 - 2026-10-03

Adds the `RejectionCode.TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH` member (`tckdb-schemas` 0.81.0, #638):
a transition-state `energy_ordering` energy that contradicts the energy TCKDB stores for the calculation
it cites is refused. An `e0` energy built with a scaled zero-point energy states the new optional
`zpe_scale_factor` (same release). Nothing in the client's code changes.

## 0.116.0 - 2026-10-03

Adds the `RejectionCode.NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH` member (`tckdb-schemas` 0.79.0, #668):
the network-PDep upload now refuses a state energy, barrier or `well_energy` / `barrier_energy` source
link whose cited calculation belongs to another subject than the one the energy is stated for (a
species outside the state, another transition state, or a species calculation for a barrier). Nothing
in the client's code changes.

## 0.113.0 - 2026-10-03

Adds the `RejectionCode.NETWORK_ENERGY_SOURCE_TYPE_MISMATCH` member (`tckdb-schemas` 0.76.0, #642): the
network-PDep upload now refuses a state energy, barrier energy or `well_energy` / `barrier_energy` source
link whose cited calculation is of a type that carries no such energy (an IRC, scan, path search or
conformer search; a `freq` for an `electronic_only` energy). Nothing in the client's code changes.

## 0.112.0 - 2026-10-03

A polyatomic species whose only calculation is a single point that declares no geometry is told what
is actually wrong again (#623). `ComputedSpeciesUpload` and `ComputedReactionUpload` now raise
"primary_calculation.type must be 'opt', got 'sp'; the bundle endpoint anchors each conformer on an
opt" (species) and "must contain at least one opt calculation" (reaction), as they did before the
one-atom `sp` primary was accepted, instead of "must declare output_geometry or input_geometry".
Both messages now also say that a single atom may anchor on its `sp` when that `sp` declares the
atom's geometry. Builders raise `TCKDBBuilderValidationError`, which carries no code, so no code
changes. A calculation that does declare a geometry is judged exactly as before, and an `opt`
primary without a geometry still gets the geometry message.

## 0.111.0 - 2026-10-03

Reads gain the composite annotations of ADR 0021, phase P7a (`tckdb-schemas` 0.73.0).
The client's read methods return the server's JSON as dictionaries, so nothing in the client's code
changes; this release records which keys a caller may now find, all additions:

- `levels` on a thermo, statmech or kinetics record gains `notation` (for example
  `CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP`, or `CBS-QB3`; `null` when the energy or geometry level
  is absent), `composite_energy_verification` (`state`: `recomputed`, `recompute_mismatch`,
  `log_reconciled`, `program_reported` or `unverifiable`, with an optional `reason`,
  `difference_hartree` and `tolerance_hartree`; `null` unless the energy comes from a composite) and
  `legacy_composite_shape` (`null` unless the record was deposited the way it was before the
  `composite` calculation type existed).
- A calculation record gains `composite_energy_verification` and `legacy_composite_shape`.
- A level-of-theory summary gains `aux_basis`, `cabs_basis` and `solvent_model` (`null` when not stated), and `notation` writes every stated part of each level: `method/basis (aux=..., cabs=..., disp=..., solvent=model:name, spin=..., core=...)`, so levels that differ only in dispersion, solvent model, spin or core treatment read differently.
- The ML-dataset export's level-of-theory `label` is written by the same renderer, so its text changes for a level that states a spin treatment, a core treatment or an auxiliary or CABS basis (it already wrote dispersion and solvent). `lot_hash` stays the key; the label is not.
- A level-of-theory summary's `composite_scheme` gains `geometry_level_of_theory_ref`.
- `GET /scientific/composite-schemes/{ref}`: each term gains `linearity`, each term input a
  `coefficient`, and the record `linear_in_energies`.
- `GET /levels-of-theory` returns `core_treatment` and `spin_treatment` and accepts a `core_treatment`
  filter, as do the scientific level-of-theory search and browse reads; the ML-dataset level-of-theory
  block carries `core_treatment`.

## 0.110.0 - 2026-10-02

`RejectionCode` gains the codes of user-built composite schemes, regenerated from
the server's catalogue (ADR 0021, `tckdb-schemas` 0.72.0):
`calculation_software_release_required`, `composite_assembled_cannot_be_primary`,
`composite_input_duplicate`,
`composite_input_edge_is_derived`, `composite_input_geometry_mismatch`,
`composite_input_level_mismatch`, `composite_input_missing`,
`composite_input_owner_mismatch`, `composite_input_reference_invalid`,
`composite_input_slot_unknown`, `composite_input_type_invalid`,
`composite_inputs_require_assembled`, `composite_scheme_malformed`,
`composite_scheme_named_method_not_sendable`, `composite_scheme_nested`,
`composite_total_mismatch`, `composite_total_required`,
`level_of_theory_method_with_composite_scheme`,
`level_of_theory_requires_method_or_composite_scheme` and `sp_energy_component_derived`. They are the refusals of
the new `composite_scheme` level of theory and the assembled composite, whose
payload shapes ship in `tckdb-schemas`. Nothing else in the client changes: the
typed builders still build only `opt`, `freq` and `sp` calculations, and a
composite calculation is sent as a payload.

## 0.109.0 - 2026-10-01

`RejectionCode` gains ten codes, regenerated from the server's catalogue
(ADR 0021, `tckdb-schemas` 0.70.0): `composite_assembled_not_accepted`,
`composite_e0_inconsistent`, `composite_level_not_scheme_bound`,
`composite_program_run_requires_software`,
`composite_result_requires_composite_type`, `composite_term_position_unknown`,
`composite_terms_do_not_sum`, `composite_type_requires_composite_result`,
`statmech_energy_sp_and_composite_linked` and
`thermo_energy_sp_and_composite_linked`. They are the refusals of the new
`composite` calculation type, whose payload shape ships in `tckdb-schemas`. A
conformer primary of type `composite` now passes the builders' primary-type
check (the rule is `tckdb-schemas`'). The typed builders still build only
`opt`, `freq` and `sp` calculations; send a composite calculation as a payload.
Nothing else in the client changes.

## 0.108.0 - 2026-10-01

`RejectionCode` gains `sp_energy_component_not_on_sp`,
`sp_energy_components_require_energy`, `sp_energy_component_duplicate`, `sp_energy_component_total_mismatch` and
`sp_energy_components_do_not_sum`, regenerated from the server's catalogue
(ADR 0021, `tckdb-schemas` 0.69.0). They are the refusals for the new
`sp_energy_components[]` field on single points. Reads of a level of theory now carry
`core_treatment` (`frozen_core`, `all_electron` or `null`) and a single point's
result carries `energy_components`; both arrive in the existing JSON shapes. The
client's request builders are unchanged.

## 0.107.0 - 2026-10-01

`Client.get_composite_scheme(ref)` reads `GET /scientific/composite-schemes/{ref}`
(ADR 0021, `tckdb-schemas` 0.68.0): the recipe behind a composite level of
theory such as CBS-QB3, with its internal levels, terms and the levels of theory
bound to it. A level of theory's own summary now carries `composite_scheme`
(`composite_scheme_ref`, `kind`, `name`, or `null` for an ordinary level); pass
that ref here. New typed shapes `CompositeSchemeRecord` and
`CompositeSchemeDetailResponse`. Nothing else in the client changes.

## 0.106.0 - 2026-10-01

Docs and help text only: README, `examples/query_cookbook.py` and
`examples/scientific_reads.py` now say that `temperature_min` / `temperature_max`
on the thermo and kinetics reads only fill each record's coverage field (they are
not filters) and that the top record is chosen by review status, then newest. No
API change.

## 0.105.0 - 2026-10-01

Docstring-only: the thermo search recipe in `examples/query_cookbook.py` now
describes the server's thermo order (review status, then newest). No API change.

## 0.104.0 - 2026-10-01

`RejectionCode` gains `level_of_theory_method_is_compound`, regenerated from the
server's catalogue (ADR 0021, `tckdb-schemas` 0.67.0). A level of theory whose
`method` contains `//` (`"x//y"`, an energy level and a geometry level written as
one name) is now refused: send the single-point and the optimization levels as
separate calculations, each with its own level of theory. Nothing else in the
client changes.

## 0.103.0 - 2026-10-01

`RejectionCode` gains `energy_correction_scheme_frequency_level_not_applicable`
and `energy_correction_scheme_frequency_level_without_energy_level`, the two
refusals for `EnergyCorrectionSchemeRef.frequency_level_of_theory`
(`tckdb-schemas` 0.66.0). No client behaviour changed.

## 0.102.0 - 2026-09-30

`Kinetics.modified_arrhenius(..., T0=...)` carries the Arrhenius reference
temperature (#620), matching `tckdb-schemas` 0.63.0, which now requires
`>=0.63.0`. `T0` is the temperature `A` was fitted at, in K, so the rate is
`A (T/T0)^n exp(-Ea/RT)`; leave it out for the plain `A T^n` form. It is sent
as `t0_k` and only when it is not 1 K, so a payload built without it is
byte-identical to one built before. Pass the fit's own T0 instead of folding
`A / T0**n` into `A`: the server then stores what was fitted. The bundle
kinetics block also accepts `interpretation_assignments`,
`tunneling_application` and `network_kinetics_ref` now, but these builders do
not emit them yet: they cite records that must already exist by public ref.

## 0.101.0 - 2026-09-30

`RejectionCode` gains three members, regenerated from the server's catalogue:
`transport_source_calculation_owner_mismatch`,
`scf_stability_source_calculation_owner_mismatch` and
`scf_stability_source_geometry_mismatch`. The first two are a cross-species
source calculation on a computed-reaction bundle's transport or SCF-stability
block; the third is a stability verdict whose measuring job is on another
conformer than the calculation carrying it (#622, `tckdb-schemas` 0.61.0). The
first was already a code the server could raise; it is now one a depositor can
receive, so it is exported. Nothing else in the client changes.

## 0.100.0 - 2026-09-30

`upload_artifacts(batch_by_calculation=True)` no longer keeps only the response
body. Each `ArtifactUploadBatchResult` now also carries `status_code`,
`request_id` (the server's `X-Request-ID`), `replayed` (the server answered from
a stored `Idempotency-Key` receipt) and `warnings` (the body's `warnings`, as a
tuple). `response` is unchanged, so existing callers keep working; the four new
fields have defaults, so code that builds the dataclass itself keeps working
too. `TCKDBResponse` gains a `request_id` property. An adapter that called
`request_json` itself to get these can go back to `upload_artifacts`.

## 0.99.0 - 2026-09-30

A single atom may anchor its conformer on an `sp` (#610), matching
`tckdb-schemas` 0.59.0, which now requires `>=0.59.0`. `ComputedSpeciesUpload`
and `ComputedReactionUpload` no longer refuse a non-`opt` primary
calculation outright: they apply the schema's own rule to the conformer
geometry they will send, so an `sp` primary is accepted when that geometry is
one atom, and refused (with the atom count) otherwise. With no explicit
`primary_calculation=` and no `opt`, the first `sp` is the candidate. Anything
with two or more atoms is unchanged. Producers should stop relabelling an
atom's single point as an `opt`.

## 0.98.0 - 2026-09-30

Adds `RejectionCode.BUNDLE_TOO_LARGE` (HTTP 413) and
`RejectionCode.BUNDLE_TOO_MANY_RECORDS` (HTTP 422). `POST /api/v1/bundles/dry-run`
and `/submit` now refuse a bundle whose request body exceeds the server's
`BUNDLE_MAX_BODY_BYTES` (default 5 MiB) or that carries more than
`BUNDLE_MAX_RECORDS` (default 500) thermo plus kinetics records; split the
bundle. The dry run also has a tighter rate limit than uploads.

## 0.97.0 - 2026-09-29

Artifact uploads address a calculation by its `calc_` ref. Upload results now
carry `calculation_ref` beside `calculation_id` (and `calculation_key_refs`
beside `calculation_keys` on a computed reaction), and `submission_ref` beside
`submission_id`. `PlannedArtifactUpload` gains an optional `calculation_ref`,
filled from those responses, and `upload_artifacts` / `upload_artifact` send it
in the path when they have one, falling back to the integer against a server
that predates it. `upload_artifact` now accepts either form. Nothing existing
is removed: plans built without refs behave as before.

Retry caveat: idempotency keys are scoped by URL path. An artifact upload
committed by 0.96 through `/calculations/{integer}/artifacts` whose response
was lost, then retried by 0.97 with the same key, goes to the `calc_` path, is
not treated as a replay, and attaches duplicate artifacts. Finish in-flight
uploads before upgrading.

## 0.96.0 - 2026-09-29

Adds `RejectionCode.SUBMISSION_SUPERSEDE_NOT_OWNER` (HTTP 403). The server now
refuses `POST /api/v1/submissions/{submission_ref}/supersede` unless the caller
created both submissions or holds the curator or admin role. The client has no
typed method for this route; a raw `post_json` caller can branch on the code.

## 0.95.1 - 2026-09-29

The parity table follows the server renaming the submission supersede route
(issue #571): it is now `POST /api/v1/submissions/{submission_ref}/supersede`,
and its body is `{"new_submission_ref": "sub_..."}`. Both submissions are named
by their public ref (`public_ref` on a submission read), and a row id is
refused with 422 in the path and in the body. The client has no typed method
for this route and never sent the integer, so nothing here changes behaviour;
a raw `post_json` caller must send the refs.

## 0.95.0 - 2026-09-29

A 2xx response that is not the API's answer now raises the new
`TCKDBUnexpectedResponseError` instead of being returned (issue #568). Before,
a JSON endpoint that answered 200 with a body that is not JSON handed that
body back as a string in `data`; the usual cause is a `base_url` naming the
site root (`https://host`) rather than the API root (`https://host/api/v1`),
where the web app answers every path with its HTML page, and callers then
failed later with a bare `TypeError`. The message names the request
(`GET https://host/scientific/calculations/... returned HTTP 200 with an
HTML page`) and, for HTML, the base URL to use instead. The client still
does not append `/api/v1` itself: `base_url` is documented as the API root.

`TCKDBUnexpectedResponseError` subclasses `TCKDBHTTPError`, so existing
`except TCKDBHTTPError` handlers catch it; it carries `url`, `content_type`,
`status_code`, `response_text` and `headers`, and `code` is `None` because
no server sent one. The NDJSON and Chemkin exports refuse an HTML 200 the
same way. `download_artifact` does not: the server labels a download by its
stored filename, so an uploaded `.html` file is a legitimate `text/html` 200.
An empty success body (204) is still `data=None`.

## 0.94.0 - 2026-09-29

`RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` now also covers ARC
and RMG (`RMG-Py`), not only Arkane: the server refuses a calculation whose
`software_release.name` is any of them (issue #305, owner decision), in any
case or spelling, with or without a trailing version (`ARC 1.1.0`,
`ARC-1.1.0`, `rmgpy`, `rmg_py`, `RMG Py`, `RMG-Py 3.2.0`). A
calculation's software is the electronic-structure program that ran the job;
put ARC in `workflow_tool_release`. No enum member changed. A product's
`analysis_software_release` is still unaffected, so RMG remains valid there.

The server also now fills a missing version from an uploaded output log: a
calculation declared with no `software_release.version` whose `output_log`
banner names the same program with a version is re-pointed at that versioned
release, and the upload returns an informational
`software_release_version_filled_from_artifact` warning.

## 0.93.0 - 2026-09-28

Adds `RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` (HTTP 422). The
server now refuses a calculation whose `software_release.name` is a workflow
tool (Arkane): a calculation's software is the electronic-structure program
that ran it. Declare that program instead, and put Arkane in
`workflow_tool_release` if it orchestrated the job. A thermo, statmech or
kinetics record's `analysis_software_release` is unaffected (issue #305).

## 0.92.0 - 2026-09-27

Requires `tckdb-schemas>=0.51.0`. The thermo builder (`Thermo`,
`Thermo.points`) now refuses tabulated Gibbs energies (`g_kj_mol`) without
`enthalpy_reference_kind`, with `enthalpy_declaration_absent`, and accepts
them when `formation_298k` is declared, even with no point enthalpy. A
stored G sits on the record's enthalpy zero, so it needs the same
declaration an enthalpy does. This matches the server, which applies the
same shared rule; with an older `tckdb-schemas` the builder would pass a
deposit the server refuses.

## 0.91.0 - 2026-09-23

**Breaking:** `tckdb` (the `get reaction`/`download artifact` CLI) no longer
defaults `--base-url` to a hardcoded deployment. Pass `--base-url`, or set
`TCKDB_BASE_URL`; omitting both is now a clear error naming both ways to
supply it, instead of a silent request to that host. If you relied on the
default, add `--base-url` or `TCKDB_BASE_URL` to your invocation. See issue
#521.

## 0.89.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.
