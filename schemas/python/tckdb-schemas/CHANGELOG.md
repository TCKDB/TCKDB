# Changelog

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
