# Relative conformer energies

One page for whoever wants to know how far above or below a chosen conformer the other conformers of one molecule lie,
using what TCKDB has stored. The contract is `backend/docs/specs/relative_conformer_energy.md`.

In plain terms: a molecule can sit in several shapes ("conformers"; TCKDB stores each shape as a conformer group, a
`cg_...` ref). You pick **one shape as the reference** and the endpoint returns, for every shape, its energy minus the
reference's, in kJ/mol. A shape that sits lower than your reference gets a **negative** number; nothing is re-measured from
the lowest shape behind your back, because published tables do not always measure from the lowest one.

## What you must say

There are four required choices, and none has a default:

- the **reference conformer** (`reference_conformer_ref`);
- the **energy** (`electronic_energy`, or `zero_kelvin_energy`, which includes zero-point vibration);
- the **level of theory** (`level.level_of_theory_ref`, or `level.composite_scheme_ref`); TCKDB never mixes two levels in one answer;
- the **certificate**, how strongly each conformer must be shown to be a true minimum
  (`local_minimum_any_surface` or `local_minimum_on_energy_surface`).

Enthalpy and Gibbs energy are recognised but not supported yet: they come back as `relative_conformer_energy_unsupported`,
and so does `certificate: "none"`.

## Reading the answer

- `members` is ordered by conformer ref. **The order is not a ranking** and there is no rank field.
- `status` is `reference`, `present`, `absent` or `unresolved`. An absent conformer has a coded `absent_reason` and
  `null` numbers. **Missing is never zero.**
- Two stored values for one conformer that disagree are `unresolved` (`repeat_values_disagree`) with both values listed;
  TCKDB does not pick the lower one.
- `outcome` says what was found; read it with `basis`, which states the rule in one sentence.
- A conformer's number comes only from records that are explicitly about that conformer. Nothing is borrowed from a
  neighbour.
- Stored uncertainties are shown as stored and are not combined.
- A high-accuracy single point does not inherit the "this is a true minimum" check of the cheaper calculation that gave
  its geometry. Each member's `certificate` says which level the minimum was checked at.

## Example 1: one conformer has no value

Three conformers; you name `cg_a` (energy -76.400 hartree) as reference.

```json
{
  "reference_conformer_ref": "cg_a",
  "energy_kind": "electronic_energy",
  "level": {"level_of_theory_ref": "lot_x"},
  "certificate": "local_minimum_any_surface"
}
```

The answer has `outcome: "relative_energies"`. `cg_b` (-76.401) is `present`; `cg_c` has no determination and is
`absent` with `absent_reason: "no_structure_determination"` and `relative_energy_kj_mol: null`. Only two of three
conformers could be compared, and the answer says exactly that.

## Example 2: the reference is not the lowest

Same data. `cg_b` is 0.001 hartree below the reference, so its `relative_energy_kj_mol` is about **-2.63**: negative,
kept as it is. The reference row itself is `reference` with `0.0`.

## Getting a replayable record

`POST .../relative-energies/manifest` returns the same answer as a downloadable JSON manifest with a checksum. Anyone with
the server code can recompute the answer from it. The checksums detect edits; they are not signatures.

## Comparing two stored protocols

`POST .../relative-energies/compare` takes two protocols (`candidate` and `reference`, each a level and an optional
recipe), the same named reference conformer, and a required `pairing` (`same_geometry` or `same_basin`). For each other
conformer it takes the candidate's relative energy minus the reference protocol's, and summarises the deviations (mean
signed, mean absolute, root-mean-square, and the largest with its conformer). The reference conformer is not counted:
its deviation is zero by construction. A conformer missing on one side, or evaluated at different geometries under
`same_geometry`, is listed with a reason and left out, never counted as zero.

The result is a description. It never says one protocol is more accurate: `claims.superiority` is `not_licensed` and the
seven things such a claim would need (E1 to E7: reference uncertainty, independence, a resolved margin, coverage and sameness, domain, established protocol facts, and owner acceptance of a rule) are listed as missing. A
preference is only ever an audited rule edge through the selection kernel, never a by-product of this comparison. The figures depend on which conformer you named as
the reference; compare them only with figures measured from the same one. `.../compare/manifest` downloads a replayable
record of both runs.

## The old route

`GET /api/v1/species-entries/{id}/conformer-observations/lowest-sp` is removed. It collapsed several single points on
one observation to the lowest, which this operation deliberately does not do. Use the route on this page.

See also: `docs/guides/selecting_structures.md` for choosing a conformer basin (a different question).
