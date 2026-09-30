# Changelog

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
