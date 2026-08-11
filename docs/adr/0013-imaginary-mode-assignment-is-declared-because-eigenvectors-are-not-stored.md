# Imaginary-mode assignment is declared, because TCKDB does not store eigenvectors

**Status: proposed 2026-08-08; amended 2026-08-11 — its central factual claim
was wrong, and the projections shipped.** Records a conflict surfaced by
implementing
[ADR 0012](0012-imaginary-modes-are-judged-by-magnitude-not-counted.md). Decides
nothing about the schema; it states precisely what would have to change, and
what that change costs, so the decision can be taken deliberately rather than
inside a feature branch.

ADR 0012 is implemented, and one of its recommendations is not. This ADR exists
because the reason is not an oversight or a scoping call — it is a second
recorded decision pointing the other way, and reversing it silently would have
been the worse of the two errors available.

> **The premise below is false, and the correction is not a detail.** This
> document reasoned that the projections were uncomputable because
> `calc_freq_mode` stores no displacement-vector column. That is true and it is
> not the question. `calc_hessian` stores the packed lower triangle of the full
> symmetric 3N × 3N Cartesian force-constant matrix, in fixed units of
> hartree/bohr², bound to a mandatory `geometry_id` — and the eigenvectors are
> that matrix's eigenvectors. Mass-weighting it with the per-atom masses
> `geometry_atom` already carries and diagonalising returns the frequencies and
> the displacements together. **The projections were never uncomputable; they
> were unattempted.** They are now implemented, at read time, storing nothing.
> [What was actually wrong](#what-was-actually-wrong) has the measurement and
> the corrected reasoning; [What shipped](#what-shipped-2026-08-11) has the
> design. Everything this ADR says about the *schema* question — whether a
> displacement vector is a record TCKDB stores — stands unaltered and
> undecided. What changed is that the projections no longer wait on the answer.

## What ADR 0012 asks for

> Project each imaginary eigenvector onto the six rigid-body vectors: more than
> about 90% overlap means the mode is projection residue and nothing else. That
> very likely settles the −13 cm⁻¹ mode outright, with no threshold involved.
> Project against dihedral-rotation vectors about each rotatable bond: more than
> about 70% identifies a torsion, whose correct treatment is a hindered rotor,
> which makes the sign moot.
>
> Both are deterministic, cheap, and computed from the eigenvectors the record
> should carry anyway. **They should be implemented before τ is tuned**, because
> a determination beats a threshold wherever one is available.

The argument is right, and it is the strongest paragraph in ADR 0012. τ is a
threshold standing in for a fact. A −13 cm⁻¹ mode that is 97% rotation about the
molecular *z* axis is *known* to be projection residue; calling it "below τ, sign
indeterminate" is strictly weaker, and a database that can compute the former and
reports the latter is throwing information away. ADR 0012 further makes the
projections load-bearing for the rule as shipped: the `imaginary_disposition`
field exists precisely to hold what a projection would otherwise determine.

## What forbids it

`backend/app/db/models/transition_state.py`, on
`TransitionStateValidationEvidence`:

> Normal-mode-displacement ("nmd") evidence is deliberately absent: reading an
> imaginary mode's displacement vectors is a producer-side heuristic, not a
> database record, and TCKDB stores only the reconstructed-path evidence an IRC
> calculation actually produces.

This is not a gap. It is a decision with a stated rationale, and the rationale is
about the *kind* of thing a TCKDB row is. An IRC calculation reconstructs a path
and reports where it lands; that is an observation with a result a depositor
either has or does not have. Reading displacement vectors and concluding "this
mode is a torsion" is an inference a producer draws, with a tolerance the
producer chose, from data the producer holds — and TCKDB's recurring rule is that
it records observations and refuses to grade them
([ADR 0005](0005-record-environments-do-not-grade-them.md)) or to infer what a
depositor should declare ([ADR 0011](0011-atom-mapping-is-declared-not-inferred.md)).

The mechanical consequence is simple: `calc_freq_mode` stores
`frequency_cm1`, `reduced_mass_amu`, `force_constant_mdyne_angstrom`,
`ir_intensity_km_mol`, `raman_activity` and `symmetry_label`. It stores no
displacement vectors. ~~There is nothing in the database to project. The
projections are not unimplemented; they are uncomputable on the data TCKDB
holds.~~

**Struck 2026-08-11: false.** Everything before the strikethrough is correct —
`calc_freq_mode` really does store no displacement vectors — and the conclusion
drawn from it does not follow, because that table is not the only place the
information lives. See [What was actually wrong](#what-was-actually-wrong).

## How the conflict was resolved for now

τ is implemented and the projections are not. Each extra imaginary mode carries
an `imaginary_disposition` the **depositor declares** — `rigid_body_residue`,
`torsion`, `ring_pucker`, `intermolecular`, `symmetry_breaking`, or an explicit
`unassigned` — and TCKDB cannot check it. That is recorded as a divergence on the
register entry rather than left implicit, because a reader must not mistake a
declared assignment for a verified one.

Two things make this tolerable rather than merely expedient.

The disposition is **never the thing that decides acceptance on its own**. It
lifts the ambiguity block only in the case where a mode at least as stiff as the
reaction coordinate has been *named*, and naming it is a claim the depositor is
answerable for, exactly as an atom map is under ADR 0011. A depositor who writes
`torsion` on a genuine second reaction coordinate has falsified a record, not
found a loophole — and, unlike deleting a line from a frequency list, has left
the evidence in place and signed it.

The evidence needed to decide again later is preserved. The complete signed
frequency list is stored, the τ that was applied and the reason it was chosen are
stored, and the structural flag is stored. If the projections are implemented in
five years, every record in the corpus can be re-decided against them.

## What would have to change

Recording eigenvectors is the whole of it, and it is not small.

**Schema.** A `calc_freq_mode_displacement` table, or a packed array column on
`calc_freq_mode`: 3N floats per mode, N modes per calculation, so O(N²) numbers
per frequency job — for a 30-atom transition state, roughly 8,100 floats where
the mode list today holds 84. The `calc_hessian` precedent (a packed lower
triangle in fixed units, geometry-bound) is the right shape to copy, including
its mandatory geometry binding: displacement vectors are meaningless without the
atom ordering and orientation they are expressed in, and the same is true of
every projection computed from them.

**Parsers.** Gaussian prints displacement vectors in the frequency block only
under `Freq=HPModes` or high print; ORCA prints them by default in the normal
modes block. Neither is currently parsed. Sign and normalisation conventions
differ between packages and would have to be canonicalised, or the projections
would silently disagree across software.

**The decision itself.** Whether a displacement vector is an observation TCKDB
records or a producer-side artifact is the question `transition_state.py:159`
answers, and this ADR does not reopen it. Note that the answer is not obvious:
the argument that TCKDB stores an IRC's *result* rather than a producer's
inference does not obviously extend to the eigenvector, which is a direct output
of the diagonalisation the frequency job performed — as much a datum as
`reduced_mass_amu`, which TCKDB already stores. It is the *projection* that is
the inference, not the vector. A future decision could reasonably store the
vectors and still refuse to store a computed assignment, in which case the
projections would run at read time and the register would gain a determination
where it currently has a threshold.

**Cost of getting it wrong.** If eigenvectors are added and the projections
implemented, the declared `imaginary_disposition` becomes checkable, and a
corpus of records deposited under the declared regime becomes a corpus of
unverified claims that can now be audited. That is a good outcome, but it is a
migration with a curation consequence — some deposits will be found to have
declared wrongly — and it needs planning rather than a column addition.

## What this ADR decides

Only this: the projections are **deferred, not rejected**, and the reason is a
conflict between two recorded decisions rather than a judgement about their
value. ADR 0012's claim that a determination beats a threshold stands unchallenged
and unimplemented. ~~The next person to pick this up should start from the schema
question — *is a normal-mode displacement vector an observation TCKDB records?* —
because everything else follows from the answer, and nothing else can proceed
before it.~~

**Struck 2026-08-11.** The instruction was wrong in the one way that mattered:
the schema question was not a prerequisite. Starting from it would have led to a
`calc_freq_mode_displacement` table and two new parsers before a single
projection ran. Starting from *what does TCKDB already hold* produced the
projections in a read-time module with no migration at all. The schema question
is still open and still worth answering; it is simply not on the critical path
of anything.

## What was actually wrong

The error is a single unexamined step, and it is worth naming precisely because
the reasoning around it is sound.

This ADR asked "does TCKDB store normal-mode displacement vectors?", found that
`calc_freq_mode` has no such column, and concluded that the projections were
uncomputable. The first two steps are right. The conclusion needed a third
question that was never asked: *is a displacement vector recoverable from
something TCKDB does store?*

It is, from `calc_hessian` (`backend/app/db/models/calculation.py:1632`), and
this ADR cites that table twice without noticing. `calc_hessian` stores the
packed lower triangle of the full symmetric 3N × 3N Cartesian force-constant
matrix, in fixed units of hartree/bohr², bound to a **mandatory** `geometry_id`.
The normal modes are the eigenvectors of that matrix mass-weighted by the atomic
masses, and TCKDB holds those too, atom-resolved: `geometry_atom.element` and
`geometry_atom.isotope_mass_number`, whose own docstring says the column "is
what makes isotope-specific frequencies, rotational constants, ZPE and Hessian
reuse reconstructible: it is the per-atom mass that a downstream normal-mode
analysis needs." The schema had already written down that this analysis was
possible.

The recovery is exact, not approximate. Against three transition states on the
live deployment it returned the stored reaction-coordinate frequency to the
stored precision:

| calculation | atoms | stored ω_imag / cm⁻¹ | recovered |
|---|---|---|---|
| 111 | 13 | −719.5 | −719.5 |
| 206 | 15 | −1743.2 | −1743.2 |
| 145 | 9 | −1074.2 | −1074.2 |

and against the ORCA `.hess` fixture in the test tree — which prints its own
`$normal_modes` alongside its `$hessian`, so the recovery can be checked against
the program's own diagonalisation rather than against itself — the recovered
reaction-coordinate eigenvector agrees with ORCA's to `|cos| = 0.999998`.

Two smaller things follow from the same correction. This ADR's cost estimate for
storing vectors — "roughly 8,100 floats where the mode list today holds 84" —
is an argument for *not* storing them that it did not make, because the Hessian
already sitting in the row is 4,095 floats for that molecule and carries the same
information. And its observation that the `calc_hessian` shape is "the right
shape to copy, including its mandatory geometry binding" was closer to the answer
than it knew: the right thing to copy was not the shape but the table.

## What shipped (2026-08-11)

**Read-time projections, computed from the stored Hessian, persisting nothing.**
No table, no column, no cache, no migration.

`app/chemistry/normal_modes.py` is the numerics: unpack the triangle,
mass-weight, diagonalise, build the rigid-body subspace at the centre of mass,
build a dihedral-rotation vector about each perceived rotatable bond, and report
each imaginary mode's overlap with both.
`app/services/scientific_read/imaginary_mode_projection.py` is the database
glue. The read surface is `include=imaginary_mode_projections` on
`GET /api/v1/scientific/calculations/{ref}`.

This is the design this ADR described without recognising it as available. §"The
decision itself" says: *"It is the projection that is the inference, not the
vector. A future decision could reasonably store the vectors and still refuse to
store a computed assignment, in which case the projections would run at read time
and the register would gain a determination where it currently has a threshold."*
Every clause of that is what shipped, minus the storing.

**A determination and a declaration are reported side by side, and neither is
preferred.** Each entry carries the depositor's `imaginary_disposition`, the
projection's own determination, the raw overlaps, the thresholds applied, and an
`agreement` field taking `not_declared` / `agrees` / `conflicts` /
`inconclusive`. TCKDB does not resolve a conflict: the depositor saw the output
file and the projection saw a matrix, and which is right is a curation question.
Under [ADR 0008](0008-validation-tiers-definitions-block-expectations-warn.md) a
projection is an expectation about a record rather than an assertion of a
definition or a contract, so it may inform a reader and may never block a
deposit.

**The determination's vocabulary is narrower than the declaration's, on
purpose.** `imaginary_disposition` has six values; the projections can
positively identify two of them — `rigid_body_residue` and `torsion` — and
otherwise return `internal_vibration`, which is a real statement (this is
internal motion that is not a rotation about any acyclic bond, which is what a
reaction coordinate looks like) but not a claim about which internal coordinate
it is. Ring puckers, intermolecular modes and symmetry-breaking modes land
there undistinguished, and against those declarations the answer is
`inconclusive` rather than manufactured agreement. Reusing the declared enum
would have claimed a resolution the measurement does not have.

**Where there is no Hessian, the answer is "not determinable", never "clean".**
On the live corpus, 84 of 132 frequency calculations carry a Hessian (64%), and
18 of the 34 records with `n_imag = 1` (53%). The other half is not a set of
records where no residue was found; it is a set that was not checked, and the
block's `status` says which — `hessian_not_stored` for that case, plus distinct
statuses for a geometry that cannot be mass-weighted, one whose atom count
disagrees with the matrix, and one whose frame the matrix does not recognise.
Merging those into an empty result is the defect
`backend/scripts/ops/verify_artifact_integrity.py` was rebuilt to close, and it
was not going to be reintroduced here.

### Two limits the implementation found, which this ADR did not anticipate

**The frame is load-bearing and can be silently wrong.** Gaussian prints its
force constants in one orientation and its geometry in two. Binding the matrix
to the wrong one still yields numbers: on `freq_g09.log` the rigid-body overlaps
of the six residue modes fall from 0.9985–1.0000 to as low as 0.44, and a
110 cm⁻¹ mode that is 98.6% a torsion reads as 4.4%. Nothing raises. The
detection is that translation and rotation are null directions of a Hessian at a
stationary point, so the curvature along them is a direct test — measured at
0.8–49.4 cm⁻¹ across three correctly framed fixtures against 419–793 cm⁻¹ for
deliberately mis-framed ones. Above 100 cm⁻¹ the block refuses. This ADR's
insistence that the geometry binding is what makes a Hessian meaningful was
right, and understated.

**A degenerate pair has no per-mode answer.** Within a degenerate subspace the
individual eigenvector is arbitrary — any rotation of the pair diagonalises the
Hessian equally well — so an overlap computed for one member is a property of
the diagonaliser rather than of the molecule. Those modes are reported with no
determination and an explicit reason, rather than with a number that would
change if LAPACK did.

### On the thresholds

ADR 0012 proposed ~90% for rigid-body residue and ~70% for a torsion, and both
survive the measurement — the first emphatically, the second provisionally.

Across the three real ESS Hessians in the test tree, the eighteen rigid-body
directions all score **≥ 0.9985** and no genuine vibration scores above
**0.0022**. The threshold names a point inside an empty interval three orders of
magnitude wide; anywhere between 0.01 and 0.99 would decide every one of those
modes identically. That also settles ADR 0012's underlying claim in a way its
own τ table cannot: those rigid-body directions come out anywhere from −10.8 to
**+49.6** cm⁻¹, so a wavenumber threshold has to guess about them and the
projection does not.

The torsion figure rests on one molecule, because only one fixture has a
rotatable bond at all. Its 110 cm⁻¹ mode projects onto a C–C rotation at 0.986;
the next most torsional mode in the whole spectrum reaches 0.472. 0.70 separates
them by a factor of two either way, which is enough to keep ADR 0012's number
and not enough to justify inventing a different one. Every overlap is on the
wire alongside the threshold that was applied to it, so a reader who disagrees
can re-decide without re-running anything.

### What was deliberately not built

**No trust-rubric check.** The obvious next step is a check on
`COMPUTED_TRANSITION_STATE_V2` that warns when a determination contradicts a
declaration. It was not added, because `imaginary_disposition` is null on every
record in the corpus — `n_imag` is only ever 0 or 1, so no deposit has ever had
an extra imaginary mode to declare — and adding a check to a rubric bumps its
version, which restales every transition-state machine review. Restaling the
corpus for a check that would evaluate `not_applicable` everywhere is a bad
trade today and an obvious one the first time a record carries a declared
disposition.

**No stored τ was recomputed and no accepted record was re-decided.** The
projections are additive: they say something new next to what was already there.

## What is still open

The schema question, unchanged: **is a normal-mode displacement vector an
observation TCKDB records?** Everything §"What would have to change" says about
its cost, its parsers and its curation consequence stands. What has changed is
that it is no longer blocking anything, and that the case for answering "no" is
stronger than it was — the vectors are recoverable from a matrix already stored
in fewer numbers, so storing them would be storing a derivative of a record
TCKDB already has, which is the kind of duplication this repository generally
refuses.

Also open: whether the frame check should run at *upload* time rather than only
at read time, so a Hessian bound to the wrong orientation is caught when it is
deposited instead of when someone asks; and whether the projections should
eventually inform the structural flag rather than sitting beside it.
