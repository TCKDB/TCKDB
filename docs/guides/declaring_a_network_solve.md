# Declaring what a network solve is

One page for whoever is building a pressure-dependent network deposit. A solve can state what its outputs
are outputs *of* (`target`), the recipe it used (`protocol`) and the evidence it cites (`validation`); each fit can state which
**determination** it is a representation of. All are optional, attributed claims. TCKDB stores what you state,
never guesses, and anything not stated reads `null`, which means "not stated": not "valid everywhere" and not "independent".

Nothing here changes how networks are browsed or evaluated. The declarations let a later, opt-in selection establish
that two solves answer the same question before it compares them.

## Determinations and their fits

A determination is one channel's coefficient from one solve. A PLOG fit and a Chebyshev fit of the same solve output
are two representations of one determination, and they are not independent evidence for each other.

```json
{
  "channel_key": "association_path",
  "model_kind": "plog",
  "plog": {"entries": [ ... ]},
  "determination": {
    "key": "assoc",
    "representation_role": "complete",
    "observable": {"version": 1, "observable": "product_resolved_coefficient", "coefficient_basis": "kernel",
                   "reaction_order": 2, "degeneracy_applied": true}
  },
  "representation": {"version": 1, "key": "plog1", "fit_origin": "solver_output"}
}
```

- Send `determination` and `representation` together. Fits that state the same `key` share the determination and
  must state the same channel and observable. Each fit has its own `representation.key`.
- `reaction_order` must equal the number of species of the channel's source state, and the fit's own rate units
  (`rate_units`, and each PLOG entry's `a_units`) must be of that order: a unimolecular channel is `per_s`.
- A determination is addressed by `channel_key`; a fit named only by `source_state_key` and `sink_state_key` cannot
  declare one.
- A fit that states neither keeps the old rule: one entry per channel and model kind. Where a channel and model
  kind occur more than once, *every* entry of that group must declare its determination and representation key.
- `representation_role`: `complete` (a whole representation), `additive_component` (one term of a sum) or
  `overlapping_contribution` (contains part of what another fit contains, never summed).

## The target

States are named by your local `key`; what is stored is each state's composition hash. A stated partition places
every state of the network exactly once (retained, eliminated, or in one lump). `bath_scope` must agree with the
solve's bath gas. `outputs` lists directed channels: `supplied` needs a determination of that channel, `unavailable`
says the solve does not give it (never zero), and `declared_zero` needs a `zero_basis`. `product_sets` name declared
complete sets of determinations; each is pinned when stored and several sets are separate competitors.

## What a declaration cannot contradict

The solve's own columns and rows stay authoritative, and a declaration that disagrees with one is refused, never
reconciled: `bath_scope` against the bath gas; a declared `validity` against the solve's temperature and pressure
range (it cannot exceed them); `protocol.barrier_basis` against the correction convention the stated state energies
and channel barriers carry (a classical electronic basis against one that includes zero-point energy, and the
converse; rows that state no convention contradict nothing); an output the catalog calls `unavailable` or
`declared_zero` against a fit that declares a determination of that channel. A validation entry's
`reference_solve_ref` must name a solve you may see; one that does not exist and one you may not see get the same
`unknown_network_solve_ref` (404).

## Refusals

`network_declaration_invalid` (`context.field` names the declaration) and `network_declaration_version_unsupported`
(only version `1`). A declaration that contradicts the network or a column of the solve is refused, never reconciled.
