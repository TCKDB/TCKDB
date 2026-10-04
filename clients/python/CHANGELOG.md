# Changelog

## 0.131.0 - 2026-10-04

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
