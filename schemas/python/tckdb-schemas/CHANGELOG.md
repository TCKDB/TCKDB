# Changelog

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
