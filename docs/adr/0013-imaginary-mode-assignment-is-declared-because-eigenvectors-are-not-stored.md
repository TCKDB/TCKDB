# Imaginary-mode assignment is declared, because TCKDB does not store eigenvectors

**Status: proposed 2026-08-08.** Records a conflict surfaced by implementing
[ADR 0012](0012-imaginary-modes-are-judged-by-magnitude-not-counted.md). Decides
nothing about the schema; it states precisely what would have to change, and
what that change costs, so the decision can be taken deliberately rather than
inside a feature branch.

ADR 0012 is implemented, and one of its recommendations is not. This ADR exists
because the reason is not an oversight or a scoping call — it is a second
recorded decision pointing the other way, and reversing it silently would have
been the worse of the two errors available.

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
displacement vectors. There is nothing in the database to project. The
projections are not unimplemented; they are uncomputable on the data TCKDB holds.

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
and unimplemented. The next person to pick this up should start from the schema
question — *is a normal-mode displacement vector an observation TCKDB records?* —
because everything else follows from the answer, and nothing else can proceed
before it.
