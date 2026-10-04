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
- `reaction_order` must equal the number of species of the channel's source state.
- A fit that states neither keeps the old rule: one entry per channel and model kind.
- `representation_role`: `complete` (a whole representation), `additive_component` (one term of a sum) or
  `overlapping_contribution` (contains part of what another fit contains, never summed).

## The target

States are named by your local `key`; what is stored is each state's composition hash. A stated partition places
every state of the network exactly once (retained, eliminated, or in one lump). `bath_scope` must agree with the
solve's bath gas. `outputs` lists directed channels: `supplied` needs a determination of that channel, `unavailable`
says the solve does not give it (never zero), and `declared_zero` needs a `zero_basis`. `product_sets` name declared
complete sets of determinations; each is pinned when stored and several sets are separate competitors.

## Refusals

`network_declaration_invalid` (`context.field` names the declaration) and `network_declaration_version_unsupported`
(only version `1`). A declaration that contradicts the network or a column of the solve is refused, never reconciled.
