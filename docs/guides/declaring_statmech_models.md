# Declaring the model a statmech record supplies

A statmech record is a container: rotational constants, a treatment label, source calculations, torsions. It does not say
whether it describes one atom, one conformational basin or an equilibrium set of several, which frequencies were scaled
for which purpose, or which symmetry and optical factors are already in the numbers. A **model declaration** says so. It is
optional, attributed and stored as you made it. A record deposited without one is read as it always was.

## What a declaration never does

- **An omitted fact is not stated.** Leaving out `scale_application` does not mean "unscaled" or "scaled by one". Leaving
  out `optical_state` does not mean the optical factor is counted, or not. Say `unknown` if you want to say you do not know.
- **A repeated record is not another state.** An ensemble cites each member record once. Counting two uploads of the same
  basin as two states would double its weight; a real multiplicity is stated as `counting_multiplicity`.
- **Nothing is inferred from the other fields.** Having several conformers on an entry does not make an ensemble, and a
  `torsions` block does not say which harmonic modes the rotors replaced.

## A basin model over this deposit's own calculations

Inside a conformer deposit an omitted `conformer_group_ref` is that conformer's own basin.

```json
{
  "model_declaration": {
    "version": 1,
    "scope_kind": "basin",
    "workflow_tool_release": {"name": "ARC", "version": "1.1.0"},
    "recipe": {
      "energy_zero": "e0",
      "zpe_included_in_energies": true,
      "scale_application": "applied",
      "scale_contributions": ["zero_point_energy", "vibrational_entropy"],
      "vibrational_scheme": "harmonic",
      "projected_mode_indices": [1],
      "rotors": [
        {"torsion_index": 1, "representation": "local_scan", "replaces_mode_indices": [3]}
      ],
      "external_symmetry_state": "counted",
      "optical_state": "not_counted",
      "electronic_levels_state": "counted"
    },
    "sources": [
      {"source_key": "curvature", "role": "curvature", "calculation_key": "freq0"},
      {"source_key": "energy", "role": "absolute_energy", "calculation_key": "sp0"}
    ]
  }
}
```

## An explicit equilibrium ensemble

An ensemble cites existing statmech records of the same species entry by public ref. It is the immutable statement that
these records, and no others, are the members; a filtered conformer list is not one.

```json
{
  "model_declaration": {
    "version": 1,
    "scope_kind": "explicit_equilibrium_ensemble",
    "literature": {"kind": "article", "title": "Conformer ensemble of the species", "year": 2026},
    "members": [
      {"member_key": "anti", "statmech_ref": "sm_abcdefghijklmnopqrstuvwxyz", "relative_energy_kj_mol": 0.0},
      {"member_key": "gauche", "statmech_ref": "sm_bcdefghijklmnopqrstuvwxyza", "counting_multiplicity": 2, "relative_energy_kj_mol": 2.9}
    ]
  }
}
```

## Sources

A source is exactly one of a calculation (`calculation_key` of this deposit, or a `calculation_ref` already stored,
optionally with the `geometry_ref` its result describes), imported `literature` parameters, or a `freq_scale_factor`. An
imported potential or a tabulated frequency set needs no fabricated calculation: cite the paper.

## Transition states

A transition-state record declares `scope_kind: ts_determination` with the `determination_key` of a structure determination
declared in the same saddle-point deposit, or the `determination_ref` of one already stored.

## What happens next

The declaration is scientific content of its statmech record. Once that record is approved it is frozen with it; correcting a
declared model means depositing a new, superseding record. Nothing is selected or ranked by the declaration alone.
