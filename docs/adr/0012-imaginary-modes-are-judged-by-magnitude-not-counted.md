# Imaginary modes are judged by magnitude, not counted

**Status: proposed 2026-08-08.** Supersedes the `n_imag == 1` blocking rule for transition states in `tckdb_schemas.stationary_point`. Does not change the rules for minima.

A first-order saddle point has exactly one negative eigenvalue of the mass-weighted Hessian. TCKDB has enforced that literally: a transition state whose frequency evidence reports anything other than `n_imag == 1` is refused. The rule is easy to state, easy to check, and — for a large class of correct calculations — wrong.

The case that forces the question is ordinary. A transition state optimises cleanly, the reaction coordinate comes out at −1300 cm⁻¹, and the frequency job also reports −42 and −13 cm⁻¹. The chemistry is right, the barrier is right, the IRC connects the intended minima. TCKDB refuses it, and ARC — along with most of the field — does not.

## The definition does not survive the translation to a database row

"Exactly one negative eigenvalue" is a statement about the exact Hessian, at the exact stationary point, on a continuous and analytic potential energy surface. A deposit contains none of those. It contains a finite-precision Hessian, evaluated at an approximately converged geometry, on a surface that is only piecewise smooth. Five things separate the two, and they are not all the same kind of thing.

The geometry is not quite at the stationary point; along a stiff mode that is irrelevant, and along a mode of near-zero curvature the residual displacement is enough for cubic anharmonicity to flip the sign of the second derivative. Hessians built from gradient differences inherit SCF noise divided by the displacement step. DFT exchange-correlation quadrature is not a smooth function of nuclear coordinates on a finite grid, which is the single most-cited source of spurious small imaginary modes — moving from Gaussian's `FineGrid` to `UltraFine` routinely shifts low-frequency modes by 5–30 cm⁻¹ and flips the sign of anything below about 40. Rotational invariance of the Hessian is exact only *at* a stationary point, so projection of translations and rotations leaves residue that surfaces as vibrational modes at ±5 to ±30 cm⁻¹.

The fifth is not numerical error at all, and it is the one that decides this record. **A harmonic model is simply inapplicable to a torsion, a hindered rotor, a ring pucker, or an intermolecular mode in a loose complex.** A transition state can sit at a maximum of a torsional profile while being a perfectly correct reactive bottleneck, in which case the extra negative eigenvalue is not an artefact — it is exactly right, and the structure genuinely is a higher-order saddle. Treated as a hindered rotor, the partition function integrates over the whole torsional profile and never asks about the curvature sign at that one point.

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

τ is **protocol-dependent**, because §2 says it must be:

| protocol | τ / cm⁻¹ |
|---|---|
| analytic Hessian, ultrafine grid, tight optimisation | 15 |
| analytic Hessian, default grid and tolerances | 30 |
| finite-difference Hessian from gradients, default settings | 50 |
| finite-difference from energies; semi-empirical; numerical composite | 80 |
| protocol not recorded | 50 |

The motivating record — −1300, −42, −13 — is accepted with a warning under every τ.

## Prefer determinations to thresholds

Threshold tuning is the least interesting part of this decision, and two cheap projections replace judgement with fact.

Project each imaginary eigenvector onto the six rigid-body vectors: more than about 90% overlap means the mode is projection residue and nothing else. That very likely settles the −13 cm⁻¹ mode outright, with no threshold involved. Project against dihedral-rotation vectors about each rotatable bond: more than about 70% identifies a torsion, whose correct treatment is a hindered rotor, which makes the sign moot.

Both are deterministic, cheap, and computed from the eigenvectors the record should carry anyway. **They should be implemented before τ is tuned**, because a determination beats a threshold wherever one is available.

## Why not refuse, when refusing is cheaper

The case for keeping the block is not weak, and it deserves stating.

A small imaginary mode can be a genuine symmetry-breaking coordinate, in which case the true transition state has a different symmetry number and a different count of equivalent structures — an integer factor on the rate, silently wrong. It can be a torsional maximum, in which case the deposited electronic energy is too high by the torsional barrier, 2–15 kJ/mol, which at 298 K is up to a factor of 55 in *k*; "it's just a soft torsion" is a diagnosis, not an absolution. It can be the signature of a valley–ridge inflection, downstream of which the IRC bifurcates and transition-state theory is qualitatively inapplicable — storing that as a TST-usable transition state is a category error about the mechanism, not a quality issue. And `n_imag == 1` is the one cheap universal check every referee applies; downgrading it risks a transition-state table that is an unseparable mixture of first-order saddles, torsional maxima and symmetry-constrained higher-order saddles, harming exactly the automated consumers least likely to read a warning field.

Three things answer it.

**A hard block's cheapest workaround is deleting a line from the frequency list.** For a floppy intermolecular transition state or a torsional maximum, no grid setting makes the extra modes disappear, so a depositor holding a correct record faces two options: abandon the deposit, or edit the output. Some will edit, and nothing downstream will ever know which records those were. A rule that converts visible ambiguity into invisible falsehood is worse than no rule — the same argument [ADR 0009](0009-record-what-energy-transfer-was-specified-over.md) made about fabrication by duplication.

**"Warnings get ignored" is a surfacing problem, and gating is the wrong instrument for it.** Make the flag structural rather than advisory: exclude flagged records from default query results and from bulk transition-state exports unless explicitly opted into, and propagate the flag onto every derived rate-constant and thermochemistry record. That delivers the protection the objection wants without destroying information and without the falsification incentive. A database can do this; a referee cannot.

**Refusal destroys the evidence needed to decide again later.** A record carrying all three frequencies, their eigenvectors and the full protocol can be reassessed under a better rule, or by an automated re-run, in five years. A refused deposit leaves nothing behind. That asymmetry is decisive on its own.

## What a record must carry

Accepting these structures is only defensible if the record lets a reader reach their own verdict. The frequency list must be complete, signed and unrounded, never filtered; the six translation/rotation eigenvalues must be present so contamination is directly assessable; and `n_imag` must be accompanied by the count above τ, the τ used, and how it was chosen.

The mode identities matter more than the numbers. The designated reaction coordinate needs its index, frequency, reduced mass and **displacement vector** — without the eigenvector nobody can confirm that −1300 cm⁻¹ is the intended chemistry rather than a different reaction entirely. The extra imaginary modes need their vectors too, with an assignment (`rigid_body_residue`, `torsion`, `ring_pucker`, `intermolecular`, `symmetry_breaking`, `unassigned`) and the overlaps supporting it.

The numerical protocol must be recorded, because the classification is undecidable without it: integration grid, SCF threshold, whether the Hessian was analytic or finite-difference and with what step, the optimisation criteria *actually achieved*, the coordinate system, whether translations and rotations were projected, and whether the frequency job used the same grid as the optimisation — a mismatch there is a classic and otherwise invisible source of spurious modes. Point group and whether symmetry was enforced belong here too, since they are what make the symmetry-breaking risk assessable.

Because two records with identical geometries and different thermochemical treatments are different scientific objects, the record must also state what was done with each imaginary mode — dropped, taken as \|ω\|, floored, or replaced by a hindered rotor — along with rotor potentials, symmetry numbers, scaling factors, and **the imaginary frequency actually used for the tunnelling correction**. That last one is not bookkeeping: if a downstream tool takes −42 cm⁻¹ instead of −1300, Wigner κ falls from 2.64 to 1.002, a 2.6× error in the rate, silently.

## Downstream, and where this actually bites

The extra modes barely touch the zero-point energy — 42 and 13 cm⁻¹ contribute 0.33 kJ/mol together, against a chemical-accuracy target near 4. They land on the partition function instead. For the 42 cm⁻¹ mode at 298 K: discarding it gives q = 1.00, treating it harmonically at \|ω\| gives 5.45, flooring to 100 cm⁻¹ gives 2.61, and a free rotor gives 3.72. Since the free rotor is the *upper* limit for any bounded torsion, the harmonic treatment is provably an over-count; discarding is worse still. A hindered rotor from an explicit scan is least wrong, and quasi-RRHO or a 100 cm⁻¹ floor is the least-wrong cheap option.

Much of that error cancels in Q‡/Q_R when the reactant carries a corresponding soft mode. **It does not cancel for bimolecular reactions**, where five new low-frequency modes appear that have no counterpart in the separated reactants — precisely the modes that come out at 10–80 cm⁻¹ or imaginary. Uncancelled, across several modes, that is an order of magnitude in *k*. Molecularity is already stored, so this case can be flagged specifically rather than left to the reader.

## What this does not decide

Whether TCKDB computes the rigid-body and torsion overlaps itself or requires them from the depositor. Whether the structural flag suppresses records from `export_ml_reactions` as well as from bulk transition-state exports. Whether τ should eventually be derived from the recorded protocol by formula rather than by table. Whether a valley–ridge inflection deserves its own declared kind rather than living under `symmetry_breaking`/`unassigned`.

## Consequences

Transition states deposited before this exists were filtered by `n_imag == 1`, so the corpus contains no accepted higher-order saddles and the flag is absent everywhere rather than false anywhere. Records refused under the old rule were never stored and cannot be recovered; depositors holding them can now re-upload.

The remediation ladder should be named in the warning text, because the third rung turns this from a nuisance into an instrument. Re-run the frequency job on a tighter grid — most modes between −10 and −40 cm⁻¹ vanish. Re-optimise more tightly and repeat. **If they persist, displace along each extra imaginary mode by 0.1–0.3 Å and re-optimise: if the energy drops and `n_imag` falls to 1, the original was a genuine higher-order saddle, and the warning has just caught a real scientific error rather than a numerical one.** If the mode is a torsion, scan it and treat it as a hindered rotor. If it is an intermolecular mode in a loose complex, RRHO is the wrong model and variational or VRC-TST is the right tool.

Finally, and most importantly: **a clean IRC settles what `n_imag` is only a proxy for.** With intrinsic-reaction-coordinate evidence linking a transition state to its intended minima, a −13 cm⁻¹ residue is irrelevant. Without it, no eigenvalue count saves the record. `transition_state_missing_irc_evidence` already exists as a warning; this decision raises its value, because IRC evidence is now the strongest single thing a depositor can supply to make a soft-mode warning moot.
