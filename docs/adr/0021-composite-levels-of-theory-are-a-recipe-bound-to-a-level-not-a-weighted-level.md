# Composite levels of theory are a recipe bound to a level, not a weighted level

**Status: accepted 2026-10-01.** Constrained by
[0008](0008-validation-tiers-definitions-block-expectations-warn.md) (block what
a definition forbids, warn what an expectation misses) and
[0017](0017-a-refusals-code-belongs-to-the-check-not-the-layer.md) (a refusal's
code belongs to the check). Implemented in phases; this record covers the whole
model, and the first two phases (this record, and the named-method catalogue
with aliases) ship with it. The other phases are listed under "Phasing" and are
not built.

The word "composite" names three different things in computational chemistry,
and TCKDB handled none of them honestly. The model below says what each is,
where it lives, and what is refused, so that the phases that build it argue
against a decision instead of rediscovering the problem.

## The three cases

| | What it is | Who runs it | How the energy is produced |
|---|---|---|---|
| **A. Named composite method** (CBS-QB3, CBS-APNO, CBS-4M, G3, G3B3, G4, G4MP2, W1U, W1BD, ...) | A fixed recipe: internal opt/freq level, several single points, an extrapolation and empirical terms (G4's higher-level correction; CBS-QB3's spin and empirical terms) | One program run, keyword-driven | The program prints the final number |
| **B. User-built scheme** (CCSD(T)/CBS from TZ/QZ; focal-point CBS + core-valence + (Q) + relativistic + DBOC) | A recipe the user chose: which levels, which formula, which deltas | Separate calculations, or one program run (Psi4 `cbs()`, ORCA `Extrapolate`, MRCC/Molpro scripts) | Arithmetic over component energies |
| **C. `energy//geometry`** (ARC `level_of_theory: "x//y"`) | Two ordinary levels: a single point at x, geometry and usually frequencies at y | Separate jobs | One single point; no arithmetic |

Two axes describe them: the **recipe** is named (implicit) or parameterised
(explicit), and the **execution** is one program run or assembled from deposited
calculations. Case C is input shorthand: ARC splits it into an `sp_level` and an
`opt_level`, and Arkane keys corrections on the pair. TCKDB does not call it
composite, and does not store it as one thing.

## What was wrong

- **No honest deposit for a composite energy.** `CalculationType` is `opt`,
  `freq`, `sp`, `irc`, `scan`, `path_search`, `conf`. A CBS-QB3 energy is
  deposited as an `sp` or an `opt`; either is the wrong type, and nothing says
  which energy it is. Gaussian's `CBS-QB3 (0 K)` includes the recipe's scaled
  zero-point energy; Arkane uses `E0 - E(ZPE)`, which is electronic-equivalent
  and still carries the empirical terms. TCKDB cannot tell which was stored.
- **A user-built scheme has no spelling at all.** Two single points (TZ, QZ) on
  one optimisation are refused as a duplicate role or as an ambiguous energy
  level. The workaround, a fake `CCSD(T)/CBS` single point, invents a
  calculation, hides the formula, and merges different recipes into one level of
  theory.
- **The same recipe spells as different levels.** Arkane's `cbsqb3` and ARC's
  `cbs-qb3`, or `G4(MP2)` and `g4mp2`, are different method keys and so
  different `level_of_theory` rows. Reproduced on `main`: `CBS-QB3` and `cbsqb3`
  hash differently and make two rows.
- **`energy//geometry` is accepted as a method name.** `method` is not checked
  for `//`, so a level of theory named by two levels, which no program ran, is
  stored.
- **The docs claim a type that does not exist.** `core_concepts.md` listed a
  `composite` calculation type; there is none.

## The decision

A composite energy is a **recipe identity, bound to an ordinary level of
theory, evidenced by a `composite` calculation.** Nothing about a level of
theory becomes a tree.

**Identity (deduplicated).** `composite_scheme` holds the recipe: its `kind`
(`named_method`, `extrapolation`, `additive`), name, a `definition_hash`, the
recipe-internal geometry and frequency levels (NULL when not stated), the
recipe's own ZPE scale factor (NULL unless cited) and the defining literature.
`composite_scheme_term` holds ordered terms (operation, energy component,
formula, exponent) and `composite_scheme_term_input` the levels each term reads
(never itself composite) with the declared cardinal number.
`level_of_theory_composite` is a side table, like `level_of_theory_merge`,
binding a level-of-theory row to a scheme.

**Provenance and results (append-only).** A `calculation` of type `composite`
is one composite energy at the scheme-bound level. `calc_composite_result` (1:1)
holds `assembly` (`program_run` or `assembled`), the ZPE-free electronic energy
with all recipe terms included, the 0 K energy including the recipe's scaled
ZPE, and the recipe ZPE; all NULL when not stated. `calc_composite_term` is the
optional breakdown, and `calc_composite_input` mirrors each input in a
`calculation_dependency` edge with a new `composite_input` role.
`calc_sp_energy_component` holds the SCF and correlation parts of a single point.

**Two kinds share it.** A *named method* is a curated catalogue entry
(`app/chemistry/composite_methods.py`): the key, the defining paper, the
internal levels, the recipe ZPE scale factor and the program scope, every value
cited and NULL where the source is silent. Its scheme is created lazily with a
hash over the kind and method key; its terms are descriptive and never checked.
The level of theory is the ordinary row (`method = 'CBS-QB3'`) plus a
`named_method_catalogue` binding. A *user scheme* sends its definition inline;
the server resolves the input levels, canonicalises and hashes it, and the level
of theory has a server-generated `method` label and a `declared` binding. One
rule joins them: a `composite` calculation is always at a scheme-bound level; an
`assembled` composite must be a user scheme; a `program_run` may be either.

**Formula, exponent and cardinals are identity.** Anything that changes the
number for fixed component energies is part of what the level of theory is: TZ/QZ
with exponent 3 and with exponent 3.4 are two levels. Literature and notes are
provenance.

**Empirical terms are not corrections.** A recipe's own empirical terms (the
higher-level correction, the spin and empirical terms, atomic spin-orbit in G4)
are already in the number the program prints; they are recorded when reported
or parsed and are never applied corrections, because that would count them
twice. Atom-equivalent, bond-additivity, spin-orbit on a non-SOC recipe,
isodesmic and frequency-scaling corrections stay in the applied layer, keyed on
the composite's level of theory. Focal-point deltas become scheme terms.

**`x//y` is never stored.** It is two levels, deposited as two calculations.
A `//` in `method` is refused (`level_of_theory_method_is_compound`); reads
derive the notation. R1 reports geometry from the optimisation and energy from
the single point, as it does today.

**TCKDB never stores a total it computed itself.** The deposited total is
checked by recomputation from its inputs (block on mismatch beyond the
tolerance below), and is `unverifiable` when an input energy is missing. Checks follow ADR 0008:
a missing input slot, an input at the wrong level, an input of the wrong type,
inputs on different entries or geometries, a total that does not recompute and
terms that do not sum block; an undeclared geometry or a log that disagrees with
the deposited value warn.

**Arithmetic tolerance (amended 2026-10-01, #654).** Every arithmetic check on
a deposited composite (`composite_e0_inconsistent`, `composite_terms_do_not_sum`,
and the recomputed-total check of P5) uses `max(1e-6, 5e-7 * n)` hartree, where
`n` is the number of rounded quantities in the equation: 3 for
`e0 = electronic + zpe`, and the number of terms plus one for a sum of terms
against its total. *Why:* a program prints each number rounded to six decimals,
so each can be off by up to 5e-7 and `n` of them by up to `5e-7 * n`. A flat
1e-6 refuses real logs: a seven-term CBS-QB3 breakdown and its total can drift by
about 3.5e-6. The floor keeps the short equations as tight as before. The check is
there to catch a number that is wrong, not printed precision. The wire package
owns the function (`composite_arithmetic_tolerance_hartree`) so the wire
validators, the persistence seam and the log comparison cannot disagree.

## The twelve decisions

1. **A composite energy is a `composite` calculation with `calc_composite_result`**,
   not a separate result table and not level-of-theory components with weights.
   *Why:* a separate table duplicates the calculation hub (dependency graph, refs,
   reviews, role links, trust). Weighted components make level-of-theory identity
   a tree, so every snapshot goes unstable, a calculation's level could point at
   one no program ran, and weights hide the formula and cannot express non-linear
   forms.
2. **A user scheme's `method` label is server-generated and canonical.** *Why:* a
   depositor label inside the hash makes equal recipes differ by name; outside the
   hash it makes the displayed name unrelated to the identity. The server's label
   is a function of the definition.
3. **Named-method binding is a stored row, backfilled from the catalogue**, not
   a read-time lookup. *Why:* the binding is then queryable and survives a
   catalogue edit without silently re-binding old rows.
4. **R1 energy priority is composite > sp > opt > imported**, the thermo
   provenance filter is aligned to it, and a record that links both an sp and a
   composite as its energy is refused. *Why:* the composite is the more complete
   energy where one exists, and two energies for one record is the ambiguity the
   level rules exist to remove. More than one distinct composite level is
   ambiguous.
5. **The composite total must be deposited; TCKDB recomputes only to check.**
   *Why:* a stored derived total would be a number TCKDB asserts, not one a
   producer measured, and would go stale with its inputs.
6. **Legacy `opt`/`sp` rows at named-composite levels are left in place and
   annotated** (`legacy_composite_shape`), not superseded. *Why:* accepted science
   is not repaired without a declaration (ADR 0015); the hosted database holds
   none.
7. **Strictness for new named-method deposits typed `opt`/`sp`, and for role
   `composite` on a non-composite calculation: warn now, refuse once the ARC
   adapter ships the composite shape.** *Why:* refusing before the producer can
   send the right shape would block every real CBS-QB3 deposit.
8. **A core-treatment field (frozen-core or all-electron) is added to the level
   of theory, hashed only when stated.** *Why:* a core-valence delta needs two
   distinct identities; hashing it only when stated leaves every stored hash
   unchanged.
9. **`composite_delta` applied corrections warn and steer to scheme terms.**
   *Why:* a delta cited as one source cannot express a two-level difference and
   targets the entry rather than the energy; a new delta that duplicates a term of
   the linked scheme is a double count.
10. **The frequency level joins correction-scheme identity**
    (`energy_correction_scheme.frequency_level_of_theory_id`). *Why:* Arkane keys
    corrections on `energy//frequency`; a scheme holding one level loses half the
    key. It is registered as an unchanged default so existing whole-row digests
    stay valid.
11. **SCF and correlation parts are stored as `calc_sp_energy_component`**, not
    only the total. *Why:* an extrapolation extrapolates the parts, so a total
    alone cannot be recomputed.
12. **Correction-table names used as methods (`cbs-qb3-paraskevas`, `cbsqb32023`)
    warn, then are refused, and are never aliased.** *Why:* they select a set of
    correction parameters in Arkane, not a method; the calculation that ran is
    CBS-QB3. Aliasing them would erase the distinction the producer is
    confusing.

## Identity keys and aliases

Named methods keep their existing hash. Curated, cited aliases join spellings of
one recipe to the Gaussian / ARC spelling so rows stored that way keep their hash:
`cbsqb3`, `cbs4m`, `cbsapno`, `rocbsqb3` to the hyphenated forms, and `g4(mp2)`,
`g3(mp2)`, `g3(mp2)b3` to `g4mp2`, `g3mp2`, `g3mp2b3`. What stays apart on
purpose: `w1`, `w1u`, `w1bd`, `w1ro` (four recipes: UCCSD against ROCCSD against
Brueckner doubles, and a different scalar-relativistic correction), `cbs-qb3`
and `rocbs-qb3` (restricted-open-shell, no spin correction), and `w1-bd`, which
no source writes, so no alias is justified: a split is recoverable by the merge
script; a false join is not. A user scheme's `lot_hash` is a hash over its
definition hash, which cannot collide with the normal hash because that always
has a `method`. A named method combined with an inline definition, and a nested
composite, are refused.

`G3//B3LYP` and `G3(MP2)//B3LYP` are the literature names of G3B3 and G3MP2B3
(Baboul et al. 1999): one recipe that merely contains `//`. They are refused like
any `//`, with a message that names the method to send (`G3B3`, `G3MP2B3`) and
the context key `named_method`; they are not aliased, because an alias would
store a `//` in a method name. Year suffixes and the `paraskevas` label are
warned about after any known method stem (decision 12), not only composites.

## Rejected shapes

- **Components with role and weight on `level_of_theory`.** See decision 1.
- **A separate "energy scheme" table only.** Every "energy level" seam (R1-R6,
  thermo, kinetics, correction schemes) would become polymorphic.
- **A separate composite-energy result table.** See decision 1.

## Consequences

- Phase 1 (this change) adds the catalogue, the aliases with their SQL twin, one
  alias re-key revision, the `//` refusal and the correction-table warning. It
  changes no table. The re-key changes `lot_hash` only for rows spelled with a
  new alias, and it is deployed like the earlier alias re-key: the duplicate
  groups it prints are merged by the operator script.
- Producers that hash level-of-theory identity locally must adopt the aliases to
  agree with the server.
- The R2'-R5 rules, the thermo and kinetics seams and the read schemas all change
  in the later phases; each is a separate pull request with its own gate.

## Phasing

Each phase is independently mergeable.

- **P0.** This record, and the `core_concepts.md` correction.
- **P1.** Named-method catalogue, aliases and SQL twin, `//` refusal,
  correction-table warning, alias re-key revision.
- **P2.** Identity tables (`composite_scheme`, terms, inputs,
  `level_of_theory_composite`), lazy catalogue rows, binding at resolve time, the
  scheme read.
- **P3.** Program-run named composite, in two parts.
  - **P3a (built).** The `composite` type, `calc_composite_result` and
    `calc_composite_term`, the wire `composite_result`, a composite conformer
    primary, R1 and R2'-R5 (with `geometry_source` / `frequency_source`
    `composite_recipe`), the thermo provenance picker aligned to R1, widened
    dependency parents, kinetics lookup, and decision 7 as warnings
    (`named_composite_deposited_as_opt` / `_sp`,
    `composite_role_on_non_composite_calculation`). Only `assembly =
    program_run` is accepted; `assembled` is refused by name
    (`composite_assembled_not_accepted`) until P5.
  - **P3b (built).** The Gaussian composite summary-block parser
    (`gaussian_composite_parser`: CBS-QB3, ROCBS-QB3, CBS-4M and G3, each with a
    real log and each block checked against the manual's identity
    `Energy - E0 = E(Thermal) - E(ZPE)` and the archive entry; every other method is
    refused rather than guessed. G4 and G4MP2 are declined too: in the Gaussian 16
    Rev A.03 logs we hold, the printed labels are shifted by one pair, so the number
    under `G4(0 K)` is the 298 K energy and the number under `G4MP2(0 K)` is the
    298 K enthalpy, not E0) and
    reconciliation of a deposited `composite_result` against it, warning
    `composite_energy_log_mismatch` / `composite_log_method_mismatch`, and an
    informational `composite_energy_log_available` when nothing was deposited. Nothing is
    filled from the log: no block prints the ZPE-free energy, so filling would mean
    storing a number TCKDB computed. A composite route on an `sp` is still refused
    an sp energy, and the reason is recorded.
- **P4.** `calc_sp_energy_component` and the core-treatment field.
- **P5.** User schemes: the inline definition, the hash branch,
  `calc_composite_input`, the `composite_input` role, the checks.
- **P6.** Correction-scheme frequency level and the `composite_delta` warning.
- **P7.** Reads and trust (`composite_energy_verification`).
- **P8.** Producers (ARC exports the composite log path; the adapter sends the
  program-run composite and the correction frequency level).

P1, P2 and P6 are independent; P3 needs P2; P5 needs P2 and P3; P7 follows P3
and P5; P8 follows P3 and P6.
