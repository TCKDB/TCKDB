# Review approves a claim, and does not cascade

Ten record types carry review state. Nothing else does, and approving one of
them approves nothing else. Both halves of that were arrived at implicitly —
the reviewable set is a tuple in `c6f2a9d4e7b1`, and the absence of cascade is
the absence of code — so this decision states what is true, says why it should
stay true, and marks the one place where the current shape is arguably wrong.

The question that forced it: *if a reviewer accepts the kinetics of a reaction,
then technically everything underneath it is right — so should acceptance flow
down?* It should not, and the reason is worth writing down, because the
argument for cascading is genuinely good and the argument against it is only
visible once you look at what acceptance costs.

## Review is an overlay, not a column

`record_review` holds one current-state row per `(record_type, record_id)`,
with a history table beside it. No science table carries a review column. That
is the curation role of the four-way split — identity dedupes, provenance and
results are append-only, curation is an overlay that says how much to trust
what the other three recorded.

The consequence that matters here: **a record can be reviewed without being
touched.** Approving does not write to the thing approved. That is what makes
per-record acceptance cheap to represent and what makes cascade look free.

## What may be reviewed

    calculation · thermo · statmech · kinetics · transport
    network · network_solve · applied_energy_correction
    transition_state_entry · conformer_observation

**The rule these follow, stated so the next addition has something to argue
against: review a claim, not a fact about provenance.**

- A **result** — a rate constant, an enthalpy, a partition function, a
  transport collision integral — asserts a number about the world. A person can
  agree or disagree with it. Reviewable.
- **Provenance** — which software, which level of theory, which basis set,
  which correction *scheme* — records what was run. There is nothing to agree
  with. A level of theory is not right or wrong; approving one is a category
  error, and a reviewer asked to do it would rightly ask what they were being
  asked.
- **Identity** — that this SMILES is this species, that this formula matches
  these atoms — is definitional. Checks enforce it and refuse deposits that
  break it. Approval adds nothing a constraint has not already guaranteed.

`applied_energy_correction` is on the list and its *scheme* is not, which looks
inconsistent and is not: applying a correction to a particular record changes a
particular number, and that is a claim. The scheme it came from is a citable
parameter set.

**Two entries sit awkwardly against the rule.** `conformer_observation` and
`transition_state_entry` are closer to identity than to claim. They are on the
list, this decision does not remove them, and the reason they are there has not
been reconstructed. Anyone extending the set should establish that reason first
— it is the kind of thing that is either a good argument nobody wrote down or
an accident nobody noticed, and the two need different responses.

## Acceptance does not cascade, and the reason is asymmetry

Accepting a kinetics record marks that row. It does not mark the statmech
beneath it, the calculations beneath that, or the conformers beneath those.

The case for cascading is real: if a rate constant is sound, the barrier is
sound, so the frequencies are sound, so the geometry is sound. Refusing to
propagate that seems to throw away information a reviewer really did supply.

It does not survive contact with **what acceptance costs**. Under
[0003](0003-freeze-ever-approved-science.md), accepted science is immutable —
the `trg_as_child_*` triggers refuse any change to a row belonging to an
accepted record, and [0015](0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md)
governs the narrow path back. So accepting is not a note in a margin. **It
freezes.**

Now the asymmetry:

- **Under-accepting** costs a second review later. Someone re-examines a record
  that was probably fine. Annoying, bounded, recoverable.
- **Over-accepting** freezes records nobody examined, with a reviewer's name
  against them and an audit trail asserting they were checked. The cost is
  unbounded, and it is paid by whoever later trusts the wrong number.

A reviewer approving a rate constant is judging *that rate constant* — is the
fit sensible, is the barrier plausible, is the tunnelling treatment
appropriate. They are not certifying that the third conformer of the second
reactant is a true minimum. Cascade turns a judgement about one thing into a
frozen assertion about a hundred, and the audit trail cannot tell the two
apart afterwards.

## The workload problem is real, and it is a workflow problem

Per-record acceptance means a reaction with fifty supporting records needs
fifty acceptances. That is a genuine cost and the objection to it is fair.

It is not an argument for cascade. It is an argument for **bulk acceptance
that is explicit**: a reviewer is shown the supporting set, sees what accepting
it would freeze, and accepts it deliberately. The database ends in the same
state — fifty rows in `record_review` — but every one of them records a
decision somebody made.

The difference is invisible in the data and decisive in the audit trail. Fifty
rows written by a cascade look exactly like fifty rows written by a reviewer,
and only one of those is true. **Represent the reviewer's actual act, not its
consequence.**

This wants tooling rather than schema: a reviewer-facing surface that assembles
the supporting set, shows what is about to be frozen, and records the
acceptance as one intent over many records. That is planned separately and is
the natural consumer of this decision.

## What this does not cover

**Whether acceptance should carry a scope.** A reviewer's honest position is
often *"the rate constant is sound; I did not examine the underlying
conformers."* Today acceptance is one flag and cannot say that, so a scrupulous
reviewer either accepts more than they checked or accepts less than they
believe. Representing scope — accepted, and here is what was and was not
examined — is a different data model and a larger piece of work. It is the
most likely successor to this decision.

**Whether machine review may accept.** `record_machine_review` and the
curator-task lifecycle exist alongside `record_review`; how an automated
verdict relates to a human one, and whether it may ever freeze, is governed
elsewhere and not settled here.

**Whether the reviewable set is right.** Only the rule it should be judged
against is settled. The two awkward entries above are named, not resolved.
