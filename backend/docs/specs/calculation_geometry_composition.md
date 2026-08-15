# A calculation's geometries must be made of its subject's atoms

## The gap

A **conformer** geometry is compared against the species entry it is deposited
under by `assert_geometry_composition_matches_identity`
(`backend/app/services/species_resolution.py:364`). A **calculation's**
geometries were compared against nothing, on any path, for any subject. That
function's own docstring said so, and called the input half "a genuine open
gap" (`species_resolution.py:414`).

Two deposits, both accepted on `main` before this change:

```
=== UPLOAD ACCEPTED ===
species_entry smiles declared: C  (methane, CH4)
calc type=opt owner_species_entry=True input_geometry_formulas=['C 6H 6'] output_geometry_formulas=['C 1H 4']
calc type=sp  owner_species_entry=True input_geometry_formulas=['C 1H 4'] output_geometry_formulas=['C 6H 6']
```

```
=== UPLOAD ACCEPTED ===
reaction: [CH]=O + C -> C=O + [CH3]   (reactant sum = C2H5O)
TS-owned calc type=opt  input_geometry_formulas=['C 2H 5O 1']
TS-owned calc type=freq input_geometry_formulas=['C 1H 4']     <-- methane
TS-owned calc type=sp   input_geometry_formulas=['C 2H 5O 1']
TS-owned calc type=irc  input_geometry_formulas=['C 2H 5O 1']
```

The second reached the database by a different route than the first, and that
route is what shaped the design — see "Two routes, not one" below.

## The rule

> **Every geometry linked to a calculation must be made of the atoms of the
> subject that calculation is filed under.**

`calculation` carries a `one_owner` CHECK constraint
(`app/db/models/calculation.py:302`): exactly one of `species_entry_id` and
`transition_state_entry_id` is set. So "which subject do I compare against?"
has exactly two answers, and both references already exist in the codebase:

| Owner | Reference composition | Already used by |
|---|---|---|
| species entry | element counts of `species.smiles` | `assert_geometry_composition_matches_identity` |
| transition-state entry | **sum of the element counts of the reaction's reactants** | `validate_transition_state_composition` |

`transition_state.reaction_entry_id` is `NOT NULL`, so the reactant sum is
always reachable from a TS-owned calculation.

What is compared, and what deliberately is not — inherited verbatim from the
conformer rule so the two cannot drift:

* **Elements, not nuclides.** `D` and `T` in the XYZ element column count as
  `H` (`resolve_element_symbol`); `[2H]` in a SMILES counts as `H`
  (`element_counts_from_smiles`). Isotopologues pass. Isotope agreement is a
  separate rule with its own code.
* **Counts, not positions.** No connectivity, no RMSD, no geometric predicate
  of any kind.
* **Absence never blocks.** A `pseudo` owner, a TS whose reaction records no
  reactants or records a `pseudo` reactant, or a stored SMILES RDKit will not
  parse — all return without judging. Incompleteness is not contradiction.
* **A free electron is not absence.** Its composition is empty, not unknown,
  so any geometry contradicts it. (In practice the wire schema refuses this
  earlier, since #151; the branch exists so the function is safe anywhere.)

Tier, in ADR 0008's terms: **block**. The ADR's test is whether the check
could plausibly fire on a correct novel result. It cannot. A structure that is
not made of its own subject's atoms is not a weaker record, it is a
contradictory one, and every number computed from it describes something
nobody deposited. Same tier as both of its neighbours.

## Case analysis

The three cases the design had to survive, plus the one found while checking.

### 1. A transition-state geometry spans the whole reacting system

For `CH3 + H -> CH4` the saddle point holds all five atoms and is neither
reactant nor product. Handled by the owner table above: a TS-owned calculation
is compared against the **reactant sum**, never against a participant. This is
not a new rule — `validate_transition_state_composition` has compared the
saddle point itself against exactly that sum since it landed.

Evidence, not assertion: the rule was run over every calculation geometry in
the thirteen ARC-derived fixture payloads under
`backend/tests/fixtures/arc_runs/`. All 31 are TS-owned — NEB path-search
images, IRC points, scan points, freq and sp inputs — and **none is refused**.
Had the reference been the TS's own `unmapped_smiles`, or any single
participant, all 31 would have gone red.

### 2. Isotopologues

CH4 and CD4 are both C + 4H by element. The check never sees the difference:
both sides fold nuclides onto elements before counting. `assert_geometry_isotopes_match_identity`
owns isotope agreement, blocks on it, and is untouched here — per ADR 0008 §9
the blocking tier owns a rule and the others cite it.

### 3. Scan and IRC points are deliberately not the equilibrium structure

They have the right atoms in a distorted arrangement. The rule counts atoms
and looks at no coordinate, so a scan point at 5 Å, an IRC endpoint in a
product well, and a dissociated fragment pair all pass. Nothing in the
implementation can drift toward plausibility, because no coordinate is read.

### 4. Ghost and dummy centres — found, and refused on purpose

`parse_xyz` accepts any one- or two-character token as an element symbol; `X`,
`Bq`, `Gh` and `Xx` all store successfully (verified by direct call). So a
counterpoise/BSSE geometry — a monomer in the dimer basis, with ghost centres
carrying basis functions and no nucleus — is storable today, and under this
rule it is refused, because `Bq` appears in no SMILES.

That refusal is correct, and the reason is worth writing down rather than
carving an exemption for. `geometry_atom` has `element`, `x/y/z` and
`isotope_mass_number` and nothing that says *no nucleus here*. A ghost centre
stored as an atom is already a misrepresentation: `geometry.natoms` counts it,
every degrees-of-freedom count downstream reads it as a nucleus, and the
frequency-count check would compare against the wrong `3N-6`. The conformer
rule has refused such geometries since it landed and nothing has complained.
Carving an exemption here would preserve a corrupt representation; the repair
belongs at the representation layer — either a `is_ghost` column or a refusal
at parse time — and deserves its own task.

### Considered and rejected as a legitimate case

**A microsolvated or clustered calculation filed under the bare species** —
ethanol's entry carrying an `sp` on ethanol plus three explicit waters. Real
science, wrong filing: the cluster is a different chemical species with its own
identity, and depositing it under ethanol makes "ethanol's energy" the
cluster's energy. That is precisely the corruption the rule exists to refuse.

## Two routes, not one — why the fallback path is checked

`attach_calculation_input_geometries` / `attach_calculation_output_geometries`
have two branches: a **producer-explicit** one that resolves
`input_geometries` / `output_geometries` from the payload, and a **fallback**
one that links a `fallback_geometry_id` the workflow passes in. It would be
tempting to check only the explicit branch, on the reasoning that the fallback
geometry is the conformer or saddle geometry that the owner-level check has
already validated.

That reasoning is wrong on two of the routes. On `/uploads/computed-reaction`
and `/uploads/networks/pdep` the fallback id is not fixed — a calculation may
name any geometry in the bundle through `geometry_key`, resolved against a
**single bundle-global map** (`computed_reaction.py:417`, populated with every
species' conformer geometry at :461 *and* the TS geometry at :646). For a
species' calculations the wire schema narrows that key to the species' own
conformers (`computed_reaction_upload.py:691`). For a **transition state's**
calculations it does not — `BundleTransitionStateIn` has no equivalent
validator — which is the second reproduction above: a TS frequency calculation
naming a reactant's geometry key and getting methane attached to a C2H5O
saddle point.

This is the same asymmetry already recorded for statmech torsion scan keys in
`app/services/calculation_ownership.py`. Narrowing the key in the wire schema
would close this instance; checking composition at the database seam closes it
for every route at once, including the ones nobody has thought of. Both
branches are therefore checked.

## Why output geometries are checked, contrary to the note that stood here

Two modules said closing the output half would be wrong:

> Closing that gap for *output* geometries would be wrong: an optimisation that
> dissociated is science to record. — `species_resolution.py:412`

> That is deliberate for an *output* geometry: an optimisation that drifted is
> science to record, not a payload to refuse. — `geometry_validation.py`

That argument is correct about **connectivity** and does not transfer to
**composition**. Every one of the seven `CalculationType` values — `opt`,
`freq`, `sp`, `irc`, `scan`, `path_search`, `conf` — is a map over a fixed set
of nuclei. No electronic-structure program adds or removes a nucleus during
one. Dissociation, isomerisation, proton transfer and ring opening all
*conserve* element counts.

`geometry_validation.py`'s own recorded evidence says exactly this, one
paragraph above the exemption it justifies:

> Methane with one hydrogen pulled out to 5 Å, i.e. a dissociated fragment
> pair — **passes**.

The case cited to justify exempting output geometries is a case the
composition rule accepts. The exemption protected no correct science, and it
was protecting the larger half: the live database holds 1806 calculation
output geometries against 326 inputs.

## What this does to the advisory row beside it

`calc_geometry_validation` records an advisory verdict on a species-owned
`opt` calculation's output geometry, with three outcomes: `passed`, `warning`
(RMSD above threshold) and `fail` (`is_isomorphic=False`). Its own module
docstring records, by direct call, that `is_isomorphic` is false **only** when
the per-element atom counts disagree — ethanol declared with dimethyl ether
deposited passes; methane with a hydrogen at 5 Å passes.

Combine that with the paragraph above and the conclusion is unavoidable: the
`fail` verdict was only ever reachable by depositing a geometry that no
calculation could have produced. It is now unreachable through any upload
path, because such a deposit is refused first. Per ADR 0008 §9 the blocking
tier owns the fact and the others cite it, so this is the rule working as the
ADR intends rather than a capability lost. What the row still says on its own
is the RMSD suspicion — a *correct-formula* structure that moved further than
expected — which is an expectation by construction and correctly non-blocking.

`tests/workflows/test_geometry_validation_wiring.py` was asserting the `fail`
row. It now asserts the refusal that replaced it, and separately pins the pure
chemistry seam's `is_isomorphic=False` verdict by direct call, so the
`passed` verdict on every accepted deposit does not become vacuous.

## Fixture rot the rule found on its first run

Three test fixtures were chemically incoherent in the way the conformer rule
found in the pressure-dependent fixtures ("ethyl as three atoms, HO2 as two"),
and nothing had looked:

* `tests/workflows/test_transition_state_upload.py` — the IRC endpoints and
  NEB images of an H + H2 → H2 + H saddle point were **single hydrogen atoms**.
  One atom on the reaction path of a three-atom system.
* `tests/workflows/test_computed_reaction_upload.py` — the `irc_reverse`
  endpoint of a CH3 + H → CH4 path was `_XYZ_CH3`, four atoms where every
  point on that path has five.

All were replaced with the whole reacting system at different path
coordinates. Every assertion in those tests — point counts, roles, orders,
linkage — is unchanged.

## Where the check runs

One function, `assert_calculation_geometry_composition`, called from every site
that inserts a `calculation_input_geometry` or `calculation_output_geometry`
row. There are eight, in four modules, and a guard test
(`tests/services/test_calculation_geometry_composition_guard.py`) fails if a
ninth appears without one — the same device the scientific-check register uses
to stop a declaration going unregistered.

Deliberately **out of scope**, and stated rather than left to be discovered:

* `calc_scan_point.geometry_id`, `calc_irc_point.geometry_id` and
  `calc_path_search_point.geometry_id` where they do *not* also produce an
  output-geometry row. IRC and path-search points do produce one and are
  therefore covered; a scan point's geometry is stored on the point row only.
* `calc_hessian.geometry_id`. It is resolved from its own payload and bound to
  the Hessian, not to the calculation's geometry lists.

Both are real remaining holes. They are narrower than the one closed here,
they reach different tables, and folding them in would mean four more call
sites checked less carefully rather than eight checked properly.

## Schema and migration impact

**None.** No model change, no migration, no new column, no new environment
variable. The rule reads `species.smiles`, `geometry_atom.element` and the
existing reaction-participant rows, and raises before the link row is added.

## Behavioural impact on existing deposits

The refusal is new, so a payload accepted yesterday can be refused today. On
the evidence available:

* The live instance holds 326 calculation input geometries and 1806 output
  geometries across 448 species-entry-owned and 124 TS-owned calculations, and
  no species entry's calculation geometries disagree among themselves. The
  check is preventative there.
* Every calculation geometry in the repository's thirteen ARC-derived fixture
  payloads passes (31 of 31, all TS-owned).
* The only deposits the rule refuses on re-run are the three incoherent test
  fixtures above and the deliberately-mismatched payload in the
  geometry-validation wiring test.

A backfill is neither required nor possible: the rule refuses at write time
and reads nothing that a stored row would have to be migrated to satisfy. A
survey of stored rows against the rule would be worth running against the live
instance before the next release; it is not attempted here because this branch
cannot reach that database.
