# A `D` or `T` element spelling is an isotope declaration

**Status: accepted 2026-10-03** (owner decision, after a three-member council).
Issue: [#672](https://github.com/TCKDB/TCKDB/issues/672). Constrained by
[0008](0008-validation-tiers-definitions-block-expectations-warn.md) (a check may
block only a definition or a contract) and
[0017](0017-a-refusals-code-belongs-to-the-check-not-the-layer.md) (a refusal's
code belongs to the check). Reverses the earlier documented position that a
`D`/`T` spelling is "composition-neutral and isotope-silent".

In an XYZ element column, `D` means deuterium (mass number 2) and `T` means
tritium (mass number 3), in every layer of TCKDB.

## The problem

Two layers read the same token two ways.

- **Identity treated `D`/`T` as plain hydrogen.** `resolve_element_symbol` called
  the spelling "composition-neutral and isotope-silent", `HYDROGEN_ISOTOPE_SYMBOLS`
  was "deliberately not wired into `validate_isotope` or `parse_xyz`", and isotope
  identity was carried only by `geometry.isotopes` and by SMILES isotope notation.
- **Masses treated `D`/`T` as deuterium and tritium.** `normal_modes.atomic_mass`
  gives a `D` atom mass 2.014 and a `T` atom mass 3.016 unless an explicit mass
  number overrides it.

So a geometry spelling `D` under a protium species passed every identity and
isotope check, and every mass-weighted quantity computed from it (normal modes,
ZPE, moments of inertia) described deuterium. Three accepted-by-test cases showed
it: D2O under SMILES `O`, CH3T under `C`, CD4 under `C`. A harmonic ZPE of about
4710 cm-1 for H2O against about 3430 cm-1 for D2O is a difference of roughly
15 kJ/mol in the 0 K enthalpy, filed under the wrong molecule with nothing to flag
it. Hessian reanalysis even *agreed* with it, because both sides used deuterium
masses.

The reverse failed too. A `[2H]` species whose geometry spelled `D` was refused
(the spelling carried no isotope, so it never matched a labelled SMILES), and
`D` plus an `isotopes` entry failed as "unknown element symbol 'D'" because
RDKit's periodic table has no `D`. There was no accepted way to deposit a
correctly labelled isotopologue spelled with `D`.

## The options

- **(a) `D`/`T` stay isotope-silent; make `atomic_mass` use protium masses.**
  Discards a stated nuclide and guesses `H` for a symbol whose only defined
  meaning is `2H`. The deposited frequencies and ZPE were computed by the ESS with
  whatever masses it used and stay attached to the protium species, so
  reanalysis would then *disagree* and the error would surface in the wrong
  place. An ESS run that really was deuterated would be re-analysed with protium
  masses. The only option that keeps a silent wrong answer.
- **(b) `D`/`T` declare an isotope everywhere.** The one meaning the symbol has.
  Closes the wrong-identity case and refuses nothing correct.
- **(c) Refuse `D`/`T` outright.** Closes the same case, and ADR 0008 allows a
  blocking contract. But it refuses an unambiguous spelling that IUPAC sanctions
  and that the read contract already documents as a nuclide, while `CL` (just as
  unambiguous) is canonicalised, not refused. Hand-written decks would have to be
  respelled.

The council converged on (b), in a variant (b-prime): the spelling is read at
parse time, stored as an element plus a mass number, and the existing isotope
check then does the refusing.

## What the council found (measured, 2026-10-03)

- Wherever `D` means anything it means deuterium. IUPAC Red Book IR-3.3.2 permits
  `D` and `T` as symbols for 2H and 3H; RDKit's molfile reader turns a `D` atom
  into `[2H]`; InChI expresses isotopes only in its `/i` layer on element H; no
  convention uses `D` for "a hydrogen of unspecified mass".
- Every ESS documents an isotope as a mass attached to an element, not as a
  `D` element: Gaussian `H(Iso=2)`, ORCA `M = ...`, Molpro a `MASS` card, Psi4
  `H@2.014101779`, Q-Chem a `$isotopes` section. The docstring claim that
  "Gaussian, ORCA, Molpro and CFOUR all emit or accept" `D`/`T` was uncited and
  is removed. A `D` reaches TCKDB from a hand-written xyz or input deck. The
  server's own Gaussian parser builds symbols from atomic numbers and always
  emits `H`.
- The test suite pinned the contradiction: six xyz geometries spell `D`/`T` in
  five test files (three protium-identity acceptances, one wrong-count refusal,
  one `[2H]` transition state, one hash/storage invariant). No data fixture
  (`.xyz`, `.log`, `.out`, `.yml`, `.json`) spells `D` or `T`.
- Three other layers already read `D` as a nuclide, so identity was the odd one
  out: the read contract (`scientific_geometry.py`: "a `D`/`T` symbol already
  implies a non-standard nuclide"), the ORM comment on `geometry_atom.element`
  and the isotope module docstring.
- The Pi held no row spelling `D` or `T`, measured 2026-10-03. Self-hosted
  instances are unknown.

## The decision

1. **Parsing.** `parse_xyz` reads a `D` or `T` element token as `H` with an
   implied mass number of 2 or 3. `geometry_atom.element` is stored as `H`, with
   `isotope_mass_number` 2 or 3. It is `H`, not `D`: keeping `D` in the column
   breaks `ck_reaction_atom_map_pair_element_matches`, which compares the stored
   element on both ends of a mapped pair, so a `D`-spelled reactant mapped onto an
   `H`-spelled saddle point (correct chemistry) would be refused as
   `atom_map_element_not_conserved`. `geometry.xyz_text` keeps the deposited `D`,
   following the `CL` precedent: the parsed index is canonical, the deposited text
   is evidence.
2. **Hash.** The implied isotope goes into `hash_text` exactly as an explicit
   `geometry.isotopes` entry would (the `ISOTOPES` suffix). A redundant explicit
   entry that equals the implied one adds nothing, so `D` alone and `D` with a
   redundant entry hash identically, while `D` against `H` plus `isotopes` gives two
   rows (the `CL`/`Cl` precedent). New `D` files therefore get fresh, correctly indexed
   rows and never dedupe onto a legacy `D`/NULL row (whose atoms, by construction,
   sit under a protium entry). **No stored `geom_hash` changes, nothing is
   rewritten and no backfill runs.** One consequence is accepted and stated: a
   legacy `D` file and the same file deposited now are two rows. That duplicates a
   row; it never merges two different molecules, and `CL` against `Cl` is an
   existing instance of the same pattern.
3. **Conflicts.** An explicit `geometry.isotopes` entry that contradicts the
   spelling (`D` with mass 3, `T` with mass 2, `D` with mass 1) is refused with a
   new code, `geometry_isotope_symbol_conflict` (ADR 0008 block, 422). It is a
   contract: the spelling and the map state two different nuclei for one atom, no
   correct deposit does that, and the message names the mechanical fix.
4. **Validation.** `validate_isotope` resolves `D`/`T` to `H` before it asks
   RDKit, so `D` plus `{i: 2}` is no longer "unknown element symbol".
5. **Identity.** A `D`/`T` geometry on a protium species is refused by the
   existing `species_geometry_isotope_mismatch` (and, for calculation geometries,
   by the calculation-geometry isotope rule that reads
   `geometry_atom.isotope_mass_number`, which then sees 2H automatically). A
   `[2H]` species with a `D`-spelled geometry is accepted.
6. **Masses.** `atomic_mass` is unchanged and pinned by a test. It already treats
   `D`/`T` as 2/3, so a `D`-spelled row whose masses are correct today stays
   correct, and the new `H` + 2 storage form weighs identically.
7. **Element counting is unchanged.** `resolve_element_symbol` still resolves
   `D`/`T` to `H` for composition and graph matching, and for legacy rows. The
   wire package's `parse_xyz_elements` does the same, so the wire-level
   element-conservation check agrees with the stored element.

## Legacy rows

Rows deposited before this decision hold `D`/`T` in `geometry_atom.element` with a
NULL `isotope_mass_number`. They are not rewritten: `trg_as_geometry_atom` forbids
updates, accepted science is immutable (ADR 0015), and a backfill is not needed
(the Pi has none). Three read-time rules apply instead, each reading the row's own
symbol and borrowing nothing from a sibling record:

- A legacy `D`/`T` row with a NULL mass counts as 2/3 wherever isotopes are
  compared (the single-atom structure key of the no-optimisation duplicate rule
  reads it so; the calculation-geometry isotope rule's counter takes the same
  rule when it lands).
- **Hessian reanalysis** on a geometry that declares a non-standard nuclide (any
  element, not only hydrogen: the contradiction and the wrong masses are the same
  for a `13C` label under a protium entry) under a protium species entry returns the status `isotope_identity_conflict` instead of
  frequencies. The recovered spectrum would otherwise describe an isotopologue
  under a protium label and agree with a list computed the same way.
- A **review-tier advisory check** (`--check isotope-identity`, a species-entry
  reference) records one `record_machine_review` row listing, for a protium entry,
  each calculation whose geometry declares a non-standard nuclide of any element. It has no status, selection,
  trust or approval effect. It exists for self-hosted instances; the Pi has none.

The `ck_geometry_atom_element_canonical` CHECK is deliberately **not** tightened
to exclude `D`/`T`. That is a separate decision, to take once the legacy count is
zero everywhere.

## Consequences

- **A deposit accepted before is now refused**: a `D`/`T`-spelled geometry filed
  under a protium species. The repair is to label the species (`[2H]O[2H]`, same
  geometry) or to spell the atoms `H` with `isotopes`. This is a behaviour change
  and is stated at the top of the changelogs.
- A correctly labelled isotopologue can be deposited with `D`, which it could not
  before.
- `ParsedXYZ.atoms` holds `H` for a `D`, so every caller that reads parsed
  elements already counts it as hydrogen; the ones that read the nuclide read
  `ParsedXYZ.isotopes`.
- Input-deck extraction still accepts `D`/`T` tokens without a periodic-table
  lookup; they now become `H` + 2/3 when the extracted geometry is stored.
  Accepting Gaussian's `H(Iso=2)` syntax there is the honest follow-up and is not
  part of this change.
- ARC and the adapters never emit `D`, so no producer changes.

## Not decided here

- Tightening `ck_geometry_atom_element_canonical` to exclude `D`/`T`.
- A refusal status for the imaginary-mode projection on a legacy `D`/`T` row
  (reanalysis has one; the projection keeps its public status set).
- Atom-level (not multiset) agreement between a SMILES and a geometry, which the
  repository still has no correspondence for.
