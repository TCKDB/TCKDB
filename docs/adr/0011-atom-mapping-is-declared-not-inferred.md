# Atom mapping across a reaction is declared, not inferred

**Status: accepted 2026-08-05.** Geometry-relative indexing was chosen over
canonical ordering; retroactive mapping of existing reactions is explicitly out
of scope.

A reaction in TCKDB records which species react, which transition state sits between them, and what rate results. It does not record which *atom* is which. Nothing anywhere states that the hydrogen leaving carbon 3 of the reactant is the hydrogen half-transferred in the saddle point and the hydrogen bonded to oxygen 1 of the product. That correspondence — the atom map — is absent, and its absence is invisible, because an unmapped reaction looks exactly like a mapped one that nobody asked about.

There is an `atom_mapping` column already, on `calc_geometry_validation`, and it is a false friend. It maps an input geometry to an output geometry for the *same species*, beside `is_isomorphic`, `rmsd` and `n_mappings`: it answers "did the optimiser hand back the molecule I gave it". That is conformer alignment. It says nothing across a reaction, and reusing the name for both would guarantee somebody eventually reads one as the other.

## Why this is worth schema, not a note in a docstring

The transition-state warning `transition_state_missing_irc_evidence` already says the quiet part: a deposit can assert that a saddle point connects its declared reactants and products with nothing in the record supporting it. An atom map is what converts that assertion into something a reader can check, because it names the bonds that break and form. Without one, "this TS connects A and B" is a claim of the same character as an unsourced number.

The isotope work makes the gap sharper. Isotope identity is already atom-resolved: `isotope_key` holds the canonical SMILES of the labelled molecule, so the database can say a species is `[2H]C([2H])([2H])O`. But a kinetic isotope effect is a statement about *what happens to a particular atom*, and the label cannot be followed from reactant to saddle point. Half of a KIE-capable schema is in place, and the missing half is this.

Two consumers want it directly. Reaction machine learning takes atom-mapped reactions as its input format — templates, fingerprints, condition models are all built on the mapping — and `export_ml_reactions` currently emits reactions without it. Reaction families are atom-mapped templates by construction, so family assignment and reaction-path degeneracy are both underivable from what is stored today.

## The decision: the depositor states it

**A map is supplied by whoever deposits the reaction. TCKDB does not silently derive one.**

This follows the rule the validation tiers already run on. An atom map is a scientific claim about a mechanism, and a mechanism is not a thing to guess: for anything but the simplest abstraction there are several chemically distinct maps consistent with the same reactants and products, and picking one by algorithm and storing it unlabelled would manufacture provenance exactly as duplicating a network-wide ⟨ΔE⟩down across wells did in [0009](0009-record-what-energy-transfer-was-specified-over.md). The depositor ran the calculation and followed the intrinsic reaction coordinate; they know which atom went where. The database's job is to record that, not to reconstruct it.

Inference is permitted, but only as a *labelled and separable* thing — a map carries how it was obtained (`declared` versus `inferred`), and an inferred map is never presented as though a human asserted it. If an inference engine is wanted, the algorithm is implemented here or vendored deliberately; TCKDB does not import a workflow tool to answer a question about its own records. **TCKDB defines this contract. Producers conform to it.** A convenient mapper existing in some upstream tool is a reason to study that algorithm, never a reason to make the schema depend on that tool being in the room.

## Absence warns; contradiction blocks

Under [0008](0008-validation-tiers-definitions-block-expectations-warn.md) the tiers follow without further argument.

An unmapped reaction is an **incomplete** record, not a false one. The rate constant is still the rate constant; what is missing is the mechanistic detail. So a deposit without a map is accepted and annotated, in the same way a deposit without literature provenance is. Refusing it would reject correct science over evidence the depositor may not have, which is the failure that rule exists to prevent — and would make every historical reaction in the database undepositable.

A map that **contradicts itself** is a different matter and blocks: an element that changes on the way across (carbon becoming nitrogen), an atom claimed twice, an atom of the reactants unaccounted for in the products where no species is missing. Those are not incompleteness; they are records that cannot be what they say they are.

## The hard part: indices relative to what

Atom indices are not a property of a species. `geometry_atom.atom_index` is a property of a **geometry**, and a geometry is reached through provenance: `ConformerObservation ← Calculation → Geometry`. A conformer observation has no geometry of its own; it is the basin assignment, and the coordinates hang off the calculation anchored to it. So "reactant atom 3" is meaningless until it is said *which geometry* is being counted.

Two shapes are available, and the choice is the substance of this decision.

**Geometry-relative.** The map names the geometries it is written against and indexes into them. Simple, exact, and immediately verifiable — every index can be checked against a real row at upload. Its defect is that the map is then welded to those geometries: deposit a second conformer of the same reactant, re-optimise at another level of theory, and the map does not carry across, because nothing guarantees the new geometry lists its atoms in the same order.

**Canonical-order-relative.** The map is written against a canonical per-species atom ordering, derived the way `canonical_isotope_key` derives isotope identity. Conformer-independent and survives re-optimisation, at the cost of requiring the canonical ordering to be defined, stable across RDKit versions, and reproducible by a depositor who must produce indices in that order.

**Decided: geometry-relative, with the geometries named explicitly in the map.** It is checkable at deposit time, which is worth more than portability for a first version, and it cannot silently mean the wrong thing — the failure mode of the canonical scheme is a map that looks fine and refers to a different atom order than the depositor intended. Portability can be added later as a derived view; correctness cannot be retrofitted onto records nobody can verify.

## Shape

The map belongs to the **micro reaction** — the object that already ties reactants, transition state and products together. Not to a species, which is deduped and shared and maps differently in every reaction it appears in; not to a geometry, which knows nothing of the reaction.

Two maps are stored, both *toward the transition state*: reactants→TS and products→TS. The saddle point is the physical pivot, both legs are what the IRC actually traverses, and composing them gives reactants→products for free. Storing reactants→products directly instead would discard the very thing that makes the map worth having.

Symmetry means a valid map is often not unique — `calc_geometry_validation.n_mappings` exists because this problem was already met once, on the easier isomorphism case. A deposit records the map it used and, where it knows, how many equivalent maps exist; this decision does not attempt to canonicalise among them, and a later decision about reaction-path degeneracy will need to.

## What this does not decide

Whether an inference engine is built at all, and if so on what algorithm. Whether degeneracy is computed from the map. Whether `export_ml_reactions` emits mapped SMILES once maps exist — that is a contract change for a published surface and deserves its own argument. Whether the existing `calc_geometry_validation.atom_mapping` is renamed to stop the collision; it is deployed, so renaming it costs a migration and buys clarity, and that trade is not obviously worth making.

## Consequences

Reactions deposited before this exists have no map and cannot be given one retroactively without the depositor's knowledge, so the corpus is split between mapped and unmapped records. **This is accepted rather than solved.** The deposited corpus is small and its provenance is known, so the reactions that matter can be re-uploaded with maps; and if a mapping algorithm is later implemented here, it can propose maps for the rest — labelled `inferred`, as this decision requires, never presented as though a human asserted them. What is not acceptable is quietly backfilling guesses, which would put unattributed mechanism claims into records whose depositors never made them.

The warning tier therefore has to be loud enough that a depositor who *has* the mapping notices they are being asked for it. A warning nobody reads would leave the corpus splitting for no reason.
