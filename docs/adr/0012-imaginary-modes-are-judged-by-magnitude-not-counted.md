# Imaginary modes are judged by magnitude, not counted

**Status: accepted 2026-08-08.** Implemented in `tckdb_schemas.stationary_point`, which supersedes the `n_imag == 1` blocking rule for transition states. Does not change the rules for minima or van der Waals complexes. Three things this document asserted before implementation turned out to be wrong or unbuildable; each is corrected in place below and listed under [What implementation changed](#what-implementation-changed).

A first-order saddle point has exactly one negative eigenvalue of the mass-weighted Hessian. TCKDB has enforced that literally: a transition state whose frequency evidence reports anything other than `n_imag == 1` is refused. The rule is easy to state, easy to check, and — for a large class of correct calculations — wrong.

The case that forces the question is ordinary. A transition state optimises cleanly, the reaction coordinate comes out at −1300 cm⁻¹, and the frequency job also reports −42 and −13 cm⁻¹. The chemistry is right, the barrier is right, the IRC connects the intended minima. TCKDB refuses it, and ARC — along with most of the field — does not.

## The definition does not survive the translation to a database row

"Exactly one negative eigenvalue" is a statement about the exact Hessian, at the exact stationary point, on a continuous and analytic potential energy surface. A deposit contains none of those. It contains a finite-precision Hessian, evaluated at an approximately converged geometry, on a surface that is only piecewise smooth. Five things separate the two, and they are not all the same kind of thing.

The geometry is not quite at the stationary point; along a stiff mode that is irrelevant, and along a mode of near-zero curvature the residual displacement is enough for cubic anharmonicity to flip the sign of the second derivative. Hessians built from gradient differences inherit SCF noise divided by the displacement step. DFT exchange-correlation quadrature is not a smooth function of nuclear coordinates on a finite grid, and both major codes' own documentation says grid choice bears on exactly the modes at issue: Gaussian recommends `UltraFine` (99,590) over `FineGrid` (75,302) *"for computing very low frequency modes of systems"* and for optimising molecules with many soft modes, and notes that larger grids have better rotational-invariance properties; ORCA raises the Hessian's XC grid one step above the SCF grid by default because "second derivative terms" want it tighter. Foresman & Frisch call the integration grid *"an essential component of the model chemistry"* and one of the largest sources of numerical error, and give a worked case where merely reorienting a molecule shifts its energy by 0.31 kJ/mol on `FineGrid` against 0.02 on `UltraFine`. Rotational invariance of the Hessian is exact only *at* a stationary point, so projection of translations and rotations leaves residue that surfaces as vibrational modes at ±5 to ±30 cm⁻¹.

The fifth is not numerical error at all, and it is the one that decides this record. **A harmonic model is simply inapplicable to a torsion, a hindered rotor, a ring pucker, or an intermolecular mode in a loose complex.** A transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck, in which case the extra negative eigenvalue is not an artefact — it is exactly right, and the structure genuinely is a higher-order saddle. Treated as a hindered rotor **from an explicit scan**, the partition function integrates over the whole torsional profile and never asks about the curvature sign at that one point.

> **Qualified 2026-08-15.** That is true of a scanned rotor and false of the scan-free methods. MS-T infers each torsion's effective barrier from the force constant *at the stationary point itself* (Zheng & Truhlar, *J. Chem. Theory Comput.* **2013**, *9*, 1356, eq 10), so a negative force constant does not become irrelevant — it makes the reference partition function diverge. The escape from the curvature sign is bought by the scan, not by the hindered-rotor model.

The consequence is that `n_imag` is not a property of a structure. It is a property of the structure *together with* the method, basis, integration grid, Hessian algorithm, optimisation tolerance and coordinate treatment. Two scientifically correct calculations of the same transition state can return `n_imag == 1` and `n_imag == 3`. **A gate a depositor can pass by changing `Int=UltraFine` is not a gate on science.**

## The noise floor is flat in ω², not in ω

This is why a fixed threshold in wavenumbers is the wrong shape, and why the classification cannot be made from the frequency list alone.

For a mode of reduced mass μ, ω = √(k/μ), so a fixed absolute error δk in the force constant gives a constant error in ω² and an error in ω of δ(ω²)/(2ω), which diverges as ω → 0. Taking a representative δ(ω²) ≈ 625 cm⁻² — that is ±25 cm⁻¹ at zero:

| ω / cm⁻¹ | uncertainty in ω | sign |
|---|---|---|
| 300 | ±1.0 | never in doubt |
| 100 | ±3.1 | safe by 30σ |
| 50 | ±6.3 | determined; magnitude ±13% |
| 20 | ±16 | **indeterminate** |

Now apply it to the case above. At −42 cm⁻¹, ω² = 1764 cm⁻² sits 2.8× above that noise level: under a clean protocol this is **real negative curvature, not noise**. Under a numerical Hessian on a default grid, where δ(ω²) ≈ 2500 cm⁻² is entirely plausible, the same number is indistinguishable from zero.

**So −42 cm⁻¹ cannot be classified from the frequency list. It can only be classified against the protocol that produced it.** A database that does not record the protocol cannot make this judgement; one that does can make it well. That is the decisive argument, and it is an argument about tiers: refusing the deposit would assert a determination the deposit does not contain the information to support. Under [ADR 0008](0008-validation-tiers-definitions-block-expectations-warn.md) that is an expectation about numerical quality wearing the costume of a definition.

## What is actually definitional

Narrower than `n_imag == 1`, and it survives the translation intact:

1. **At least one imaginary mode exists.** Otherwise there is no reaction coordinate and the structure is not a transition state at all.
2. **Exactly one mode is designated the reaction coordinate**, and is removed from the partition function. This is the contract every transition-state-theory code actually enforces.
3. **Every other imaginary mode large enough to matter has a declared disposition.**

Magnitude does not turn an expectation into a definition. It decides whether claims 2 and 3 can still be honestly made. At −13 cm⁻¹ a depositor can say "one reaction coordinate, plus projection residue" and mean it. At −800 cm⁻¹ nothing honest can be said without declaring what that mode is.

## The decision

**Judge imaginary modes by magnitude against the protocol that produced them. Do not count them.**

| condition | tier |
|---|---|
| a transition state with `n_imag == 0` | **block** — no reaction coordinate |
| `n_imag > 1` with no designated reaction coordinate | **block** — the contract in claim 2 is unmet |
| an undeclared extra imaginary mode with \|ω\| ≥ \|ω_RC\| | **block** — the reaction coordinate is genuinely ambiguous |
| extra imaginary modes, all below τ | **warn** — sign indeterminate at this protocol |
| an extra imaginary mode at or above τ, with a declared disposition | **warn**, plus a structural flag |
| a single imaginary mode below 100 cm⁻¹ | **warn** — unchanged, suspiciously soft |

**τ = 100 cm⁻¹, one constant, for every protocol.**

> **Amended 2026-08-15.** τ was originally a five-row table keyed on the
> Hessian algorithm, integration grid and optimisation tightness. The table
> is withdrawn. Three findings retired it, in increasing order of weight.
>
> **It was never calibrated.** The table's rows rest on the claim in §2 that
> changing grid "routinely shifts low-frequency modes by 5–30 cm⁻¹ and flips
> the sign of anything below about 40". That sentence has since been hunted
> through six documents — the four papers this ADR draws on, Foresman &
> Frisch 3rd ed. (551 pages, index read), Gaussian's `Int` documentation and
> both ORCA manuals. **No wavenumber-valued figure for any grid change exists
> in any of them.** Every accuracy number in the vendor documentation is an
> energy. The rows were plausible, and that is all they were.
>
> **It had no consequence to be precise about.** The paragraph below already
> established that τ never decides between blocking and warning. A wrong τ
> costs a differently-loud warning and nothing else. Precision without
> consequence is complexity without benefit, and it was buying a dependency
> on parsing each program's frequency method.
>
> **The premise behind its ordering is unsourced.** The table ranked analytic
> Hessians above finite-difference ones. That ordering is probably right —
> finite differencing divides the underlying noise by the displacement step —
> but ORCA's thermochemistry documentation never compares the two for
> accuracy, only for cost, and no source we read quantifies the difference.
> The distinction is preserved as *provenance* rather than as *judgement*:
> `freq.hessian_method` is still parsed and stored whenever a job states it,
> so a consumer who believes analytic Hessians deserve more trust can filter
> on it. The argument is kept at
> `paper/notes/orca_vs_gaussian_hessian_provenance.md`, including the open
> question of whether "method unstated" should be read as "possibly
> numerical" at all, given Gaussian computes analytic second derivatives by
> default for most functionals.
>
> **Why 100 rather than a rounder guess.** It is the one value in this region
> with independent published anchors, all three verified against their primary
> sources: it is the quasiharmonic floor of Ribeiro, Marenich, Cramer &
> Truhlar (*J. Phys. Chem. B* **2011**, *115*, 14556), it is Grimme's
> quasi-RRHO crossover ω₀ (*Chem. Eur. J.* **2012**, *18*, 9955), and it is
> ORCA's `QRRHORefFreq` default. Three independent groups placed a
> soft-mode boundary at the same wavenumber. A constant we can cite beats a
> table we calibrated by assertion.
>
> Records deposited before this amendment retain the τ they were judged
> under, which is why §"What was actually built" stores τ per record rather
> than recomputing it. Changing this rule does not re-decide history.

The motivating record — −1300, −42, −13 — is accepted with a warning under every τ.

**τ never decides between blocking and warning.** This was not stated when the table was written, and it turns out to be the property that makes the whole design safe. Every blocking row above is a contract about what the record *says* — that a reaction coordinate exists, that exactly one is designated, that no undeclared mode is stiffer than it — and none of them consults a magnitude except relative to the designated coordinate itself. τ separates a quiet warning from a flagged one and nothing else. So a payload whose protocol was never recorded is judged for *acceptance* identically to one that recorded everything; missing provenance changes how loudly a record is flagged, never whether it is taken. Without that property, τ would have to be resolvable at validation time from data a payload may not carry, and the rule would be refusing deposits over the absence of a parameter row.

The consequence for reading the table: τ resolves from `freq.hessian_method` first, and the grid and optimisation columns only refine the analytic row. An unrecorded Hessian method takes the last row whatever else is present — a tight grid and a tight optimisation cannot buy the 15 cm⁻¹ line on their own, because the frequency job's own method is the term that dominates the noise floor.

## Prefer determinations to thresholds

Threshold tuning is the least interesting part of this decision, and two cheap projections replace judgement with fact.

Project each imaginary eigenvector onto the six rigid-body vectors: more than about 90% overlap means the mode is projection residue and nothing else. That very likely settles the −13 cm⁻¹ mode outright, with no threshold involved. Project against dihedral-rotation vectors about each rotatable bond: more than about 70% identifies a torsion, whose correct treatment is a hindered rotor, which makes the sign moot.

Both are deterministic, cheap, and computed from the eigenvectors the record should carry anyway. **They should be implemented before τ is tuned**, because a determination beats a threshold wherever one is available.

> ~~**Not implemented, and blocked rather than deferred.** The clause "the eigenvectors the record should carry anyway" is the error in this section: the record does not carry them and, by an existing decision, is not supposed to. `backend/app/db/models/transition_state.py` states that normal-mode displacement evidence is deliberately absent because "reading an imaginary mode's displacement vectors is a producer-side heuristic, not a database record". There is nothing in the schema to project, so the projections are uncomputable rather than merely unwritten, and this section's instruction to implement them *before* τ could not be followed.~~
>
> ~~τ therefore shipped alone, and the assignment on each extra imaginary mode is **declared by the depositor** and unverifiable by TCKDB.~~
>
> **Struck 2026-08-11: implemented.** "The record does not carry them" was wrong. `calc_hessian` stores the full Cartesian force-constant matrix bound to a mandatory geometry; the eigenvectors are its eigenvectors, and mass-weighting with the per-atom masses `geometry_atom` already carries returns them exactly — verified against three live transition states and against an ORCA `.hess` that prints its own eigenvectors (`|cos| = 0.999998`). The projections now run **at read time**, computed from the stored matrix and persisting nothing, under `include=imaginary_mode_projections` on the scientific calculation read. Both this section's thresholds survived the measurement; see [ADR 0013 §"On the thresholds"](0013-imaginary-mode-assignment-is-declared-because-eigenvectors-are-not-stored.md#on-the-thresholds) for the evidence, and §"What was actually wrong" for how the premise failed.
>
> The declared `imaginary_disposition` remains what a depositor deposits, and is now *checkable* rather than unverifiable: a determination and a declaration are surfaced together and a disagreement is reported as one, never silently resolved. τ still shipped first, so this section's "before τ" is the one instruction that was genuinely not followed.

## Why not refuse, when refusing is cheaper

The case for keeping the block is not weak, and it deserves stating.

A small imaginary mode can be a genuine symmetry-breaking coordinate, in which case the true transition state has a different symmetry number and a different count of equivalent structures — an integer factor on the rate, silently wrong. It can be a torsional maximum, in which case the deposited electronic energy is too high by the torsional barrier, 2–15 kJ/mol, which at 298 K is a factor of 2.2 to 425 in *k*; "it's just a soft torsion" is a diagnosis, not an absolution. It can be the signature of a valley–ridge inflection, downstream of which the IRC bifurcates and transition-state theory is qualitatively inapplicable — storing that as a TST-usable transition state is a category error about the mechanism, not a quality issue. And `n_imag == 1` is the one cheap universal check every referee applies; downgrading it risks a transition-state table that is an unseparable mixture of first-order saddles, torsional maxima and symmetry-constrained higher-order saddles, harming exactly the automated consumers least likely to read a warning field.

Three things answer it.

**A hard block's cheapest workaround is deleting a line from the frequency list.** For a floppy intermolecular transition state or a torsional maximum, no grid setting makes the extra modes disappear, so a depositor holding a correct record faces two options: abandon the deposit, or edit the output. Some will edit, and nothing downstream will ever know which records those were. A rule that converts visible ambiguity into invisible falsehood is worse than no rule — the same argument [ADR 0009](0009-record-what-energy-transfer-was-specified-over.md) made about fabrication by duplication.

**"Warnings get ignored" is a surfacing problem, and gating is the wrong instrument for it.** Make the flag structural rather than advisory: exclude flagged records from default query results and from bulk transition-state exports unless explicitly opted into, and propagate the flag onto every derived rate-constant and thermochemistry record. That delivers the protection the objection wants without destroying information and without the falsification incentive. A database can do this; a referee cannot.

**Refusal destroys the evidence needed to decide again later.** A record carrying all three frequencies, their eigenvectors and the full protocol can be reassessed under a better rule, or by an automated re-run, in five years. A refused deposit leaves nothing behind. That asymmetry is decisive on its own.

## What a record must carry

Accepting these structures is only defensible if the record lets a reader reach their own verdict. The frequency list must be complete, signed and unrounded, never filtered; the six translation/rotation eigenvalues must be present so contamination is directly assessable; and `n_imag` must be accompanied by the count above τ, the τ used, and how it was chosen.

The mode identities matter more than the numbers. The designated reaction coordinate needs its index, frequency, reduced mass and **displacement vector** — without the eigenvector nobody can confirm that −1300 cm⁻¹ is the intended chemistry rather than a different reaction entirely. The extra imaginary modes need their vectors too, with an assignment (`rigid_body_residue`, `torsion`, `ring_pucker`, `intermolecular`, `symmetry_breaking`, `unassigned`) and the overlaps supporting it.

The numerical protocol must be recorded, because the classification is undecidable without it: integration grid, SCF threshold, whether the Hessian was analytic or finite-difference and with what step, the optimisation criteria *actually achieved*, the coordinate system, whether translations and rotations were projected, and whether the frequency job used the same grid as the optimisation — a mismatch there is a classic and otherwise invisible source of spurious modes. **Record the direction, not merely the inequality:** ORCA raises the Hessian's XC grid one step above the SCF grid *by default*, on the stated grounds that second derivatives want the tighter grid, so a bare "grids differed" flag would fire on every default ORCA deposit while missing the case that matters — a frequency job run *looser* than the optimisation that produced its geometry. Point group and whether symmetry was enforced belong here too, since they are what make the symmetry-breaking risk assessable.

Because two records with identical geometries and different thermochemical treatments are different scientific objects, the record must also state what was done with each imaginary mode — dropped, taken as \|ω\|, floored, or replaced by a hindered rotor — along with rotor potentials, symmetry numbers, scaling factors, and **the imaginary frequency actually used for the tunnelling correction**. That last one is not bookkeeping: if a downstream tool takes −42 cm⁻¹ instead of −1300, Wigner κ falls from 2.64 to 1.002, a 2.6× error in the rate, silently.

## Downstream, and where this actually bites

The extra modes barely touch the zero-point energy — 42 and 13 cm⁻¹ contribute 0.33 kJ/mol together, against a chemical-accuracy target near 4. They land on the partition function instead. For the 42 cm⁻¹ mode at 298 K, all referenced to the zero-point level: discarding it gives q = 1.00, treating it harmonically at \|ω\| gives 5.45, and flooring to 100 cm⁻¹ gives 2.61. The spread across treatments is the point — a factor of five on one mode, chosen rather than computed.

> **Corrected 2026-08-15.** This paragraph previously added "a free rotor gives 3.72" and concluded that "since the free rotor is the *upper* limit for any bounded torsion, the harmonic treatment is provably an over-count". Both the number and the inference were wrong.
>
> The **bound itself holds**: a hindered rotor's partition function never exceeds the free rotor's, by the min–max principle — but only for the *same* moment of inertia, the *same* symmetry number, and both referred to the potential minimum. **The inference does not follow.** q_free is fixed by I and σ, which a 42 cm⁻¹ frequency does not determine. The quoted 3.72 assumed I = 3.2 amu Å² and σ = 3, an assumption stated nowhere — and those values actually give 3.71; 3.72 needs I = 3.225. For a heavy top of the same 42 cm⁻¹ curvature (I = 20 amu Å², σ = 3) the free-rotor value is 9.26, above the harmonic value, so the ceiling constrains nothing; a worked case there gives a hindered rotor at 5.71 against harmonic 5.45, harmonic **under**-counting. The crossover is at I = 5.673 amu Å².
>
> The comparison also mixed energy zeros — 5.45 counted from the zero-point level, 3.72 from the potential minimum. Referenced consistently the harmonic value is 4.93, which removes a third of the claimed gap before any physics is argued. Fixing only that does not rescue the claim.
>
> What survives, and is enough: **the treatment of a soft mode is a choice, it moves the partition function by a factor of several, and the record must therefore say which choice was made.** A hindered rotor from an explicit scan is the best of the cheap options; quasi-RRHO (Grimme, *Chem. Eur. J.* **2012**, *18*, 9955 — entropy only) and a 100 cm⁻¹ floor (Ribeiro et al., *J. Phys. Chem. B* **2011**, *115*, 14556) are the two documented cheap ones. Neither is claimed here to be nearer the truth in any given case; a floor bounds a mode's contribution, not the error.

Much of that error cancels in Q‡/Q_R when the reactant carries a corresponding soft mode. **It does not cancel for bimolecular reactions**, where five new low-frequency modes appear that have no counterpart in the separated reactants — precisely the modes that come out at 10–80 cm⁻¹ or imaginary. Uncancelled, across several modes, that is an order of magnitude in *k*. Molecularity is already stored, so this case can be flagged specifically rather than left to the reader.

## What implementation changed

Three corrections, all folded into the text above.

~~**The eigenvector projections could not be built.** §"Prefer determinations to thresholds" assumed the record carries displacement vectors. It does not, by an existing decision.~~ **Corrected 2026-08-11: they could, and now are.** The record does not carry a displacement-vector *column*, and it does carry the Hessian those vectors are the eigenvectors of. The projections ship as a read-time determination that stores nothing. See the note in that section and [ADR 0013](0013-imaginary-mode-assignment-is-declared-because-eigenvectors-are-not-stored.md).

**The analytic-versus-numerical axis was not recorded anywhere, and now partly is.** τ's table keys on the Hessian algorithm, and nothing in TCKDB recorded it — `opt.initial_hessian` is the optimiser's starting Hessian, a different object from the one the frequency job diagonalised, and using it would have been a guess wearing the costume of provenance. Rather than fall back to the conservative τ for every record, the Gaussian and ORCA parameter parsers were taught to read the frequency job's own method (Gaussian `Freq=Numer` and `Freq=EnOnly`, ORCA `NumFreq` and `AnFreq`) into a new `freq.hessian_method` canonical parameter. Only explicit statements are recorded: Gaussian does not name its default, so an unqualified `Freq` leaves the key absent and takes the conservative row. The practical effect is that ORCA jobs can reach the 15 cm⁻¹ line and Gaussian jobs generally cannot, which is an honest description of what each output says rather than an assumption about what each program did.

**τ had to be stored, not recomputed.** The requirement that "`n_imag` must be accompanied by the count above τ, the τ used, and how it was chosen" reads like a reporting obligation; it is a storage one. τ is derived from parsed provenance, so recomputing it at read time would let a parser improvement silently re-decide every historical record — the opposite of letting a reader re-decide deliberately. `calc_freq_result` therefore stores the τ applied, the row of the table it came from, and the resulting structural flag, alongside the designated reaction coordinate.

## What this does not decide

Whether the structural flag suppresses records from `export_ml_reactions` as well as from bulk transition-state exports — it is recorded but not yet consumed by any export path. Whether τ should eventually be derived from the recorded protocol by formula rather than by table. Whether a valley–ridge inflection deserves its own declared kind rather than living under `symmetry_breaking`/`unassigned`. Whether an extra imaginary mode at or above τ carrying no declared disposition should eventually block rather than warn — it currently warns and flags, because the table above admits no such block and inventing one during implementation would have been a decision taken in the wrong place.

## Consequences

Transition states deposited before this exists were filtered by `n_imag == 1`, so the corpus contains no accepted higher-order saddles and the flag is absent everywhere rather than false anywhere. Records refused under the old rule were never stored and cannot be recovered; depositors holding them can now re-upload. The migration adds only nullable columns and backfills nothing, because there is nothing true to backfill: a record judged under the counting rule was never judged under this one, and writing `false` into the flag would claim otherwise.

The read-time trust rubric also stopped re-deriving the count. ADR 0008 §9 recorded that `HardFailReason.frequency_source_has_multiple_imaginary_modes_for_validated_ts` duplicated the upload-time rule; under this decision the duplicate became a contradiction, because the motivating record would have been accepted with a warning at upload and hard-failed at read time by the surviving copy of the retired gate. The rubric now cites the persisted designation instead of recounting, and the hard-fail reason was replaced by `frequency_source_reaction_coordinate_not_designated_for_validated_ts` — a question about whether the record carries what the blocking tier required, which anything that passed upload validation does. The rubric is bumped to `computed_transition_state_v2`, which restales machine reviews performed under the counting rule; they are genuinely stale.

The remediation ladder should be named in the warning text, because the third rung turns this from a nuisance into an instrument. Re-run the frequency job on a tighter grid — most modes between −10 and −40 cm⁻¹ vanish. Re-optimise more tightly and repeat. **If they persist, displace along each extra imaginary mode by 0.1–0.3 Å and re-optimise: if the energy drops and `n_imag` falls to 1, the original was a genuine higher-order saddle, and the warning has just caught a real scientific error rather than a numerical one.** If the mode is a torsion, scan it and treat it as a hindered rotor. If it is an intermolecular mode in a loose complex, RRHO is the wrong model and variational or VRC-TST is the right tool.

Finally, and most importantly: **a clean IRC settles what `n_imag` is only a proxy for.** With intrinsic-reaction-coordinate evidence linking a transition state to its intended minima, a −13 cm⁻¹ residue is irrelevant. Without it, no eigenvalue count saves the record. `transition_state_missing_irc_evidence` already exists as a warning; this decision raises its value, because IRC evidence is now the strongest single thing a depositor can supply to make a soft-mode warning moot.
