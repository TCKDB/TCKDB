# Declaring what a calculation ran and what it is evidence for

One page for whoever builds a conformer, transition-state or computed-species deposit. Two things can be stated,
both optional and both attributed claims: the **actual protocol** of a calculation (the recipe that was really
run), and a **structure determination** (which calculations, in which roles, support a claim about one geometry,
conformer basin or saddle point). TCKDB stores exactly what you state, never infers it, and a record that
states nothing reads `null`. `null` means "not stated": not a standard recipe, not gas phase, and not "the
same as another record's null".

Nothing here changes how calculations, conformers or transition states are browsed or ordered. The declarations
exist so that a later, opt-in energy selection can establish that two numbers answer the same question before it
compares them.

## What a calculation actually ran

A level of theory names a method and a basis. It does not say which electronic state or root was computed, which
reference was used, whether a scalar-relativistic Hamiltonian or an effective core potential was involved, which
electrons were correlated, which numerical approximations were material or which corrections the stored number
already includes. State those on the calculation:

```json
{
  "type": "sp",
  "software_release": {"name": "Orca", "version": "6.0.1"},
  "level_of_theory": {"method": "DLPNO-CCSD(T)", "basis": "def2-tzvp"},
  "sp_result": {"electronic_energy_hartree": -76.43},
  "actual_protocol_declaration": {
    "version": 1,
    "source": {"origin": "producer_declared", "producer": "ARC", "producer_version": "1.1.0"},
    "electronic_state": {"state": "known", "root": 0},
    "relativistic_treatment": {"state": "known", "value": "none"},
    "effective_core_potential": {"state": "not_applicable"},
    "solvation": {"state": "known", "kind": "gas_phase"},
    "numerical_approximations": [{"kind": "pair_natural_orbital", "setting": "tightpno"}],
    "included_corrections": []
  }
}
```

- Every single fact is `known` (with its value), `unknown` (you say you do not know) or `not_applicable` (the recipe
  has no such thing, for example no effective core potential). A fact you leave out is *not stated*.
- A list is omitted (not stated) or a list. An empty list is the claim "none".
- At least one statement is required, `version` is `1`, and an unknown field is refused.
- Operational settings that do not change the number (threads, memory, scratch) have no place here.
- Where the declaration restates a level-of-theory field (auxiliary basis, dispersion, core treatment), a selection
  compares the two. A disagreement is a finding; neither side wins by upload order.
- It is read back on the calculation detail as `provenance.actual_protocol_declaration`, with
  `actual_protocol_declaration_state` (`absent`, `valid`, `unreadable`).

## What a determination claims

A determination pins this upload's own calculations to the roles they play in a claim about one geometry:

```json
{
  "structure_determinations": [{
    "key": "basin-1",
    "target_kind": "conformer_basin",
    "quantity": "electronic_energy",
    "evaluated_geometry": {"calculation_key": "opt", "side": "output"},
    "sources": [
      {"role": "geometry_optimization", "calculation_key": "opt"},
      {"role": "energy", "calculation_key": "sp"},
      {"role": "curvature", "calculation_key": "freq"}
    ],
    "workflow_tool_release": {"name": "ARC", "version": "1.1.0"}
  }]
}
```

- **Where.** On a conformer upload (`target_kind` `geometry` or `conformer_basin`; the basin is the observation the
  upload creates), a transition-state upload (`geometry` or `saddle_point`), and inside the computed-species and
  computed-reaction bundles: each conformer carries its own `structure_determinations` (a basin claim over that
  conformer's calculations) and a reaction bundle's transition state carries its own (a saddle claim). In a bundle a
  calculation is named by its bundle-global `key`. A transition-state upload accepts an optional `key` on `primary_opt`
  and each additional calculation, unique within the request, for this purpose only. A calculation can also be named by
  `calculation_ref` (a `calc_` ref) when it was deposited earlier, but it must belong to the same species entry or
  transition state entry.
- **A basin is about one observation.** Every calculation a `conformer_basin` claim names (and the one its geometry is
  read from) must be anchored to that conformer's observation; one anchored to another observation, or to none, is
  refused (`context.reason` `observation`). A `geometry` or `saddle_point` claim has no such requirement.
- **Roles.** `energy`, `geometry_optimization`, `curvature`, `correction`, `connectivity`,
  `alternative_characterization`. One calculation can play several roles (one source entry each). Different
  claims are different determinations: TCKDB never builds the combinations of an owner's attachments for you.
- **Quantity.** `electronic_energy`, `zero_kelvin_energy` (a supplied E0, which needs `energy_convention`) or omitted
  for an evidence-only determination. TCKDB never derives one from the other, never adds a zero-point energy and
  never falls back from a stated quantity to another.
- **Evaluated geometry.** The named calculation's one `output` or `input` geometry. A calculation with none or several
  on that side does not pin one and is refused (`structure_determination_mismatch`, `context.reason` `geometry`).
- **Source attribution.** `literature` or `workflow_tool_release` is required: the key is scoped to a source.
- **The key is an identifier.** The owner (for a basin, its observation), the source attribution and the `key` name one
  determination. Stating the key again with the same content resolves to the existing determination, so a repeat is
  never an additional determination. That holds without an Idempotency-Key **only for a claim whose calculations are
  named by `calculation_ref`** (calculations already deposited). Re-sending a whole conformer or transition state upload
  whose claims pin calculations by *local key* creates new calculations each time, so the restated claim pins different
  calculations and is refused with 422 `structure_determination_mismatch` (`context.reason` `content`). To retry such an
  upload, send it again with the **same Idempotency-Key** (the retry then replays the first response and writes nothing).
  Stating the key with different content (target kind, quantity, convention, recipe, evaluated geometry or pinned
  calculations) is refused the same way: a determination is immutable, and its pinned calculations are fixed when it is
  created. State a different key for a different claim.
- **A basin cannot be restated across uploads**, because each conformer upload creates a new observation. A `geometry` or
  `saddle_point` claim can be restated, over calculations already deposited and named by `calculation_ref`.
- **Frozen with its owner.** Once the transition state entry or conformer observation a determination belongs to is
  accepted, nothing can be added to, changed on or removed from its determinations or their sources.

## Refusals

| Code | Meaning |
| --- | --- |
| `structure_declaration_version_unsupported` | `version` is not `1`. |
| `structure_declaration_invalid` | A declaration reached the server without passing validation and fails it. |
| `structure_determination_invalid` | The determination contradicts itself (quantity and convention, no energy source for a stated quantity, a repeated source, no source attribution). |
| `structure_determination_mismatch` | `context.reason` is `target` (the kind does not fit the upload), `owner` (a pinned calculation belongs to another subject), `observation` (a basin claim pins a calculation anchored to another observation), `geometry` (no single geometry on the side named, or the geometry is read from a calculation the determination does not pin) or `content` (the key was already stated with different content). |
| `calculation_key_undeclared` | A pin names a `key` this upload never declared; `context.declared_keys` lists the ones that would work. |
| `unknown_calculation_ref` | A `calculation_ref` names no calculation. |

## What this does not do yet

Reading, ordering and selecting by these declarations is separate work. A declaration is never used to rank or to
fill in another record, and stored findings (confirmed identity, state or path incompatibility, adjudication) have no
upload path.
