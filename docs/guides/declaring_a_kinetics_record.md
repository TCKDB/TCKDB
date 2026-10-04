# Declaring what a kinetics record is

One page for whoever is building a kinetics deposit. A rate record can state three things about
itself, all optional and all attributed claims: which **determination** it belongs to, what the
coefficient **is**, and how the rate was **produced**. TCKDB stores exactly what you state, never
guesses, and a record that states nothing reads `null` for all three. `null` means "not stated": not
"a standalone rate" and not "valid everywhere".

Nothing here changes how kinetics is browsed. The declarations exist so that a later,
opt-in selection can establish that two records answer the same kinetic question before it compares them.

## A determination, and the records that share it

A determination is one complete determination of a rate: a measurement set, or one computed rate. An
Arrhenius fit and a Chebyshev fit of the same data are two representations of one determination, and
they are not independent evidence for each other. Say so:

```json
{
  "direction": "forward",
  "workflow_tool_release": {"name": "Arkane", "version": "3.2"},
  "determination": {"key": "run-1", "target_kind": "whole_reaction", "representation_role": "complete"}
}
```

- The record must state its own `direction` (never inferred) and a source: `literature` or
  `workflow_tool_release`. A determination key is scoped to a source.
- `target_kind` is `whole_reaction`, or `resolved_channel` naming exactly one of a
  `transition_state_entry_ref` or a `network_ref` with `channel_key`. A channel must belong to the record's
  reaction.
- `representation_role` is `complete` for a fit of the whole determination, or `additive_component` for one
  term of a determination that is a sum of records. An additive component is not a total-rate candidate on
  its own. One determination holds one role: a record of the other role is refused.
- **Sharing.** Every upload creates its own reaction entry, and a determination belongs to one entry. To add a
  second representation, cite the first one's ref: `{"determination_ref": "kdet_...", "representation_role":
  "complete"}`. The record is stored under the determination's own reaction entry, so its reaction content must
  be exactly that entry's, and its direction and source must match. In one reaction bundle the fits share the
  bundle's entry, and in one contribution-bundle import uploads that state the same determination content share
  one.

## What the coefficient is

```json
{"applicability": {
  "version": 1, "phase": "gas", "observable": "rate_coefficient",
  "coefficient_basis": "elementary_coefficient", "reaction_order": 2,
  "pressure_dependence": "fixed_pressure",
  "collider_kind": "specified_collider", "colliders": [{"species": {"smiles": "N#N", "charge": 0, "multiplicity": 1}}],
  "claim_origin": "source_publication"
}}
```

Every statement is optional; an omitted one is unknown. `claim_origin` is required. The columns you already
send stay authoritative, and a declaration that contradicts one is refused: a fixed-pressure claim needs
`pressure_context: apparent_at_pressure` and `pressure_bar`; a PLOG, Chebyshev, falloff or network-linked record
cannot be declared pressure independent; the coefficient basis must agree with `is_third_body`; the order must
match the reaction. A mixture's mole fractions must sum to one (within 1e-9) and are never renormalised.

## How the rate was produced

```json
{"protocol": {
  "version": 1, "method_kind": "saddle_point_tst", "barrier_basis": "zpe_corrected",
  "zero_point_treatment": "harmonic_scaled", "rotor_treatment": "hindered_rotor",
  "conformer_treatment": "single_conformer", "departures": []
}}
```

`departures` has three states: omitted is not stated, `[]` says there are none, a list names the parts
changed. Only `[]` says "standard". `supporting_calculations` name calculations by key or ref, each with a
`purpose`, and must belong to a participant of the reaction or its transition state. A protocol is a claim; it
is not checked against the linked calculations.

The full vocabulary, the refusal codes and the lifecycle (declarations are frozen with an approved record,
and corrected only by a replacement) are in `backend/schema_spec.md`.

## In the SDK

`Kinetics.modified_arrhenius(..., direction="forward", determination=..., applicability=..., protocol=...,
protocol_calculations=[("geometry", opt_calculation)])` checks the same rules before any request. A bundle fit
states `forward` (or `net`); a reverse-direction fit swaps its reactant and product keys.
