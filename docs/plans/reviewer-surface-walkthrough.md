# One real review, walked end to end

The first step #222 asks for, done. Not a design — a walkthrough of a single
deposited reaction, listing every record a reviewer would have to accept and
what they would need in front of them for each. The point is to derive the
read-API requirements from **what the judgement needs**, rather than from what
happens to be queryable today, and to turn "does acceptance carry a scope?"
into a question with a concrete answer attached.

Everything below was measured on 2026-09-14 against the live Pi instance
(alembic `e3a7c1f9b2d4`) and the code at `90fe0aa0`, then independently
re-measured in review. The Pi is the working playground, not a paper corpus —
these numbers describe *this deployment* and are here to make the exercise
concrete, not to characterise the archive.

Claims are measured unless marked **[inferred]**.

## Method

Read-only SQL against the deployed database for the supporting set, and the
live HTTP read API for what a reviewer can actually retrieve. Nothing was
written. Queries are reproducible from the refs in the appendix.

---

## 1. The reaction

```
rxn_om6l6zmb6p365llhryzyx3glsy
rxe_qpwvjtgnwvyer2zdytemwib3te

    [CH2]C(C=C)OS  ->  C=CC1CO1  +  [SH]
```

A doublet radical closing to an epoxide with a thiyl radical leaving —
unimolecular, one transition state, three participating species. Chosen
because it is a complete computed entry with a TS, an IRC, corrections and
uncertainty: the *easy* case. A harder one would not have fewer gaps.

The claim under review:

| | |
|---|---|
| ref | `kin_2uhinwoeibtynxycriehpcnkcq` |
| model | modified Arrhenius |
| A | 4.878 × 10¹² s⁻¹ |
| n | 0.334322 |
| Ea | 85.9891 kJ/mol |
| range | 300–3000 K |
| tunnelling | Eckart (see §3 — the word, and nothing else) |
| uncertainty | A ×1.406 (multiplicative), n ± 0.0444, Ea ± 0.254 kJ/mol |

---

## 2. The supporting set: 36 frozen, 44 in a queue

ADR 0016 predicted "a reaction with fifty supporting records needs fifty
acceptances". Measured, for this one:

| reviewable type (ADR 0016) | rows |
|---|---:|
| kinetics | 1 |
| transition_state_entry | 1 |
| statmech | 3 |
| thermo | 3 |
| conformer_observation | 3 |
| calculation | 17 |
| applied_energy_correction | 8 |
| **total** | **36** |

The 17 calculations break down as 7 `opt`, 4 `sp`, 4 `freq`, 1 `irc`, 1 `scan`,
all at one level of theory (`b3lyp/def2tzvp`) and one software release
(Gaussian 16), with 27 stored artifacts.

**Two numbers, and the difference matters.** 36 is the ADR 0016 set — the types
whose acceptance *freezes* science. But `record_review` also carries rows for
`species_entry`, `conformer_group`, `reaction_entry` and `transition_state`,
and `PATCH /record-reviews/{record_type}/{record_id}` accepts any
`SubmissionRecordType`. For this reaction that is 8 more rows, so **a queue
built naively on `record_review` shows 44, not 36**. Deciding which of those
two sets a reviewer is shown is a product decision that falls out of §6.

Closure checks, so the set can be trusted: every calculation cited by
`kinetics_source_calculation` (6 rows) and `statmech_source_calculation`
(9 rows) is inside it; each species has exactly one species entry; and **no
other reaction entry shares these species**, so the acceptance scope really is
local to this reaction.

**Nothing in this set has been reviewed.** Database-wide there are 1,299
`record_review` rows and every one is `not_reviewed`, except a single
`network_solve` sitting at `under_review`. The review machinery has never been
exercised in anger, which means no habit has formed yet — this is the moment to
choose the shape.

---

## 3. What a reviewer needs, record by record

### The rate constant (1 row)

**The judgement:** is this fit sensible over the stated range, is the barrier
plausible for this chemistry, is Eckart the right tunnelling treatment?

**What they get today — and it is a lot.** `GET
/api/v1/scientific/reaction-entries/{ref}/kinetics` returns, per record: the
Arrhenius parameters, uncertainty, `temperature_coverage`, `supersession`, full
`provenance`, a `review` block, an envelope-level `review_summary`, and an
`evidence_completeness` checklist:

```
score 7/9
  has_source_calculations            true
  has_transition_state_entry         true
  has_ts_opt_evidence                true
  has_ts_freq_evidence               true
  has_ts_sp_evidence                 true
  has_path_search_or_irc_evidence    true
  has_uncertainty                    true
  has_geometry_validation            FALSE
  has_scf_stability                  FALSE
```

It also returns `levels`, which is where a reviewer's eye should go first:

```
geometry   b3lyp/def2tzvp
frequency  b3lyp/def2tzvp
energy     b3lyp/def2tzvp     energy_source: sp
```

The barrier of a rate constant taken from a B3LYP single point is a judgement
call a reviewer would want to make deliberately, and the read surface puts it
in front of them without being asked. That is the shape the rest of this
should follow.

**A worked check that succeeds**, to show the surface can support real
arithmetic: the TS single point (−630.021756791 Ha) minus the reactant single
point (−630.054478594 Ha) is 0.032722 Ha = 85.9 kJ/mol electronic; applying
ΔZPE (0.095932 − 0.0968327 Ha) gives ≈ 83.6 kJ/mol at 0 K, against a fitted Ea
of 85.99 kJ/mol. Consistent. A reviewer can do that from what is returned.

**The tunnelling treatment is not reviewable.** `kinetics.tunneling_model`
stores the enum `eckart` and `kinetics_tunneling_application` has **0 rows** —
the read API's `tunneling` block is `null`. The forward and reverse barriers,
the energy-zero convention and the frequency calculation the correction was
built from all live in that table, and none of it was deposited. So of the
three questions above, the third cannot be answered at all: a reviewer can see
*that* Eckart was claimed, never *what it was applied to*.

**The two false checklist items are TS-scoped, not reactant-scoped.** Both are
computed against the TS calculations (`ts_opt_calc_id`, `ts_sp_calc_id`). Six
`calc_geometry_validation` rows do exist for the species optimisations — all
`is_isomorphic = true`, RMSD ≤ 1.2 × 10⁻⁴ — and none for the TS.
`calc_scf_stability` has **0 rows database-wide**, so that item can never
currently be true for anything.

### The transition state (1 `transition_state_entry`, plus its calculations)

**The judgement:** is this a first-order saddle point, and is it the saddle for
*this* reaction?

Half of that is answerable and half is not.

Answerable: the frequency calculation records `n_imag = 1` at
**−719.4703 cm⁻¹**, with `imaginary_mode_tau_cm1 = 30` on basis
`assumed_analytic_default`. One imaginary mode, comfortably large — a reviewer
can see it is not a numerical artefact. (The same tau and basis are stamped on
all four frequency calculations in the set, including the three minima with
`n_imag = 0`, which is worth a reviewer's raised eyebrow about what "assumed"
covers.)

**Not answerable: is that mode the reaction coordinate?** ADR 0013 decided the
archive stores no eigenvectors and that the mode's disposition is *declared*
instead. Measured: **0 of 124 `calc_freq_result` rows declare
`reaction_coordinate_mode_index`, and all 26 imaginary modes (of 2,049 modes)
have `imaginary_disposition` NULL.** The declaration mechanism exists and has
never been used. The single most important question about a transition state
has no recorded answer anywhere in this deployment.

**Nor does the IRC settle it.** There is an IRC calculation, and database-wide
there are 17 IRC results and 1,424 IRC points. But
**`transition_state_validation_evidence` has 0 rows**, and
**`reaction_atom_map` has 0 rows for this TS entry**. The path was computed and
stored; the *conclusion* — this saddle connects these reactants to these
products — is recorded nowhere, by either mechanism. Note that
`evidence_completeness` reports `has_path_search_or_irc_evidence: true`, which
is true about the calculation existing and says nothing about connectivity. A
reviewer reading that checklist would reasonably believe the question was
settled.

### Statistical mechanics (3 rows) — and the one that is missing

The three statmech rows are for the three species, and a reviewer can check
them properly: treatment (`rrho`, and `rrho_1d` for the one with a hindered
rotor), external symmetry, optical isomers (1, 2, 2), point group (`Cinfv` and
linear for `[SH]`, `C1` for the others), and whether a frequency scale factor
was applied (all three: yes).

**There is no statmech for the transition state.** Not for this one, and not
for any: **0 of 101 statmech rows in the database attach to a
`transition_state_entry`.** A TST rate constant is Q‡/(QA·QB) — the TS
partition function is the quantity most directly under the number being
reviewed, and it is not in the archive.

This is the single largest finding of the walkthrough. A reviewer asked to
accept `kin_2uhinwoeibtynxycriehpcnkcq` cannot check the term that most
determines it.

`uses_projected_frequencies` is NULL on all three rows, and on **65 of 101
database-wide**. For a species with a hindered rotor, whether the rotor mode
was projected out of the vibrational set is a real double-counting question,
and "not recorded" is not the same as "not done".

### Spin contamination — deposited, and invisible

`calc_spin_diagnostic` has 40 rows database-wide, including three in this set:
the reactant SP (⟨S²⟩ 0.7549), `[SH]` (0.754), and **the TS single point, at
⟨S²⟩ = 0.7816 before annihilation and 0.7502 after**. Roughly 4% contamination
on the barrier calculation of a doublet reaction is exactly the fact a reviewer
wants in front of them.

It is in the database and `evidence_completeness` has no item for it. The
checklist is not merely missing checks that were never run — it is silent about
a diagnostic that *was*.

### Energy corrections (8 rows)

Two schemes: an atom-energy correction (`aec_total`, hartree) and a bond
additivity correction (`bac_total`, kcal/mol), applied to each species and to
the TS.

A partial reviewer check, and its limit. The `aec_total` for the reactant
`[CH2]C(C=C)OS` and for the transition state are *identical* —
630.0442600297998 hartree. That is not a copy-paste error: measured from
`geometry_atom`, both are C₄H₇OS at 13 atoms, so an atom-count correction must
give the same total, and the component rows agree (C×4, H×7, O×1, S×1). A
reviewer can confirm the *identity* at a glance.

They cannot confirm the *derivation*. On every component,
`contribution_value ≠ multiplicity × parameter_value` — for carbon,
4 × (−37.8656) against a stored +152.5445, a per-atom difference of +0.2705 Ha
(≈ 170 kcal/mol, about the atomic heat of formation of carbon); hydrogen,
oxygen and sulfur differ by 0.0807, 0.0923 and 0.1029 Ha. The contributions
evidently fold in atomic heats of formation, and probably spin-orbit
corrections, while `parameter_value` carries only the atomic energy at the
level of theory. The totals are internally consistent; a reviewer trying to
reproduce a component from the published parameter cannot. The BAC for the TS
is exactly 0, with zero component rows — a different kind of statement, and one
worth asking about.

**And none of these eight rows can be named to a reviewer.**
`applied_energy_correction` has no `public_ref` column. This was already
recorded as the residual on ML-plan R2 and in `app/services/record_refs.py`;
here it stops being abstract — **8 of the 36 rows in this supporting set cannot
be linked to from a queue.**

### Calculations (17) and conformer observations (3)

These are the ones a reviewer accepting a *rate constant* has almost certainly
not examined. Per-calculation reads are comprehensive — `freq-result`,
`sp-result`, `opt-result`, `irc-points`, `scan-points`, `constraints`,
`parameters`, `geometry-validation`, `scf-stability`, `artifacts` — so the
information is reachable. The problem is not availability. It is that
accepting 17 calculations is not a thing a person does honestly in one sitting,
and ADR 0016 is explicit that the alternative — cascading — is worse.

---

## 4. Three things #222 assumed were missing and are not

The task was written cautiously about backend readiness. Measured:

1. **The acceptance write path exists.** `PATCH
   /api/v1/record-reviews/{record_type}/{record_id}`, with `GET
   /api/v1/record-reviews` and `GET /api/v1/submissions/for-review` beside it.
   A reviewer surface does not need a new write API.
2. **The read surface is rich.** `evidence_completeness`, `levels`,
   `review_summary`, `supersession`, `temperature_coverage` and full provenance
   already ship on the kinetics read. Much of "what does a reviewer need to
   see" is answered.
3. **The public/authenticated split is already drawn.** `/api/v1/scientific/*`
   serves anonymously; the legacy entity reads and all review routes require
   authentication, with an explicit message pointing at the public surface.

One wrinkle: the review PATCH is addressed by **internal row id**
(`record_id: int`), not public ref. A UI that follows DR-0028 and never shows a
row id still needs one to write. Resolvable — accept a ref and resolve
server-side — but it is a decision, and it is the same seam #478/#479 have been
working along.

**Updated by #484.** Half of that is now closed: every record-review *response*
— the list, the single read, and the PATCH reply — carries `record_public_ref`
beside `record_id`, so a UI can name and link the record it is asking somebody
to review. The other half stands: the PATCH is still *addressed* by row id in
the path, so a page reads a ref and writes an id. That asymmetry is tolerable
while the surface is admin/curator-only and the id never reaches the screen,
and it is the thing to revisit if these routes ever serve a wider audience.

---

## 5. The gap list, ranked by distance from the number under review

| # | Gap | Cost |
|---|---|---|
| 1 | **No statmech for any transition state** (0/101) | The term most directly under a TST rate constant is absent |
| 2 | **No tunnelling application recorded** (`kinetics_tunneling_application` 0 rows) | The Eckart correction's barriers and energy zero are unrecorded; only the word survives |
| 3 | **Reaction coordinate never declared** (0/124), `imaginary_disposition` always NULL (0/26), **`reaction_atom_map` and `transition_state_validation_evidence` both empty** | "Is this the right saddle?" is unanswerable by any of the three available mechanisms — while the checklist reads as if IRC settled it |
| 4 | **Spin diagnostics deposited but unsurfaced** (40 rows; TS ⟨S²⟩ 0.7816) | The contamination fact a doublet-barrier reviewer needs is present and not shown |
| 5 | `applied_energy_correction` has no `public_ref` | 8 of 36 rows cannot be linked |
| 6 | Correction components do not reconcile against published parameters | The identity check works; the derivation check does not |
| 7 | `uses_projected_frequencies` NULL (65/101) | Double-counting unanswerable for hindered-rotor species |
| 8 | `calc_scf_stability` empty (0 rows db-wide) | A checklist item that can never currently be true |

Gaps 1–4 are **deposit-side**: the data was computed and not sent, or the field
exists and nothing fills it, or it was sent and nothing surfaces it. No UI
fixes them. They are the honest answer to "what does the backend need first" —
and they are not what #222 guessed, which was more read routes.

---

## 6. The scope question, in its concrete form

#222 asks whether acceptance should carry a scope, framed around a reviewer's
honest position: *"the rate constant is sound; I did not examine the
conformers."*

**An earlier version of this section claimed the walkthrough made that question
sharper, and it was wrong.** It is recorded below rather than deleted, because
the reasoning that replaced it is the useful part.

Under ADR 0003 accepted science is frozen by `trg_as_*` triggers. Measured:
each child trigger is parameterised with **one** root type and the FK reaching
it, so it consults that root's acceptance and no other. Read off the live
database:

```
calc_freq_result          trg_as_child_05  ->  ('calculation', 'calculation_id')
kinetics_arrhenius_entry  trg_as_child_36  ->  ('kinetics',    'kinetics_id')
```

`tckdb_guard_accepted_child` resolves each named FK and asks
`tckdb_record_is_accepted(TG_ARGV[0], id)` — a single `(record_type,
record_id)` existence test against `record_review.first_approved_at`. There is
no walk. The root guard on `calculation` likewise tests only
`('calculation', OLD.id)`. At the application layer,
`set_record_review_status` stamps `first_approved_at` on one row and locks that
one ref; `accepted_science.py` has no cascade. `kinetics_source_calculation` is
a child of *kinetics*, so accepting the kinetics freezes the **link row**, not
the calculation it points at.

So accepting `kin_2uhinwoeibtynxycriehpcnkcq` freezes the Arrhenius row and
leaves the frequency calculation it was computed from unfrozen. The freeze
groups are disjoint by construction, not by omission.

### The retracted claim

This section previously read that sentence as a hole, and asked "what does an
accepted number mean when its inputs can still change underneath it?" That
framing does not survive contact with the rest of the design, for two reasons
established on 2026-09-14:

1. **A record that is not accepted being mutable is not a gap — it is what
   "not accepted" means.** The property is fully general: accepting any record
   leaves every other record mutable. Presenting it as specific to frequencies
   under a rate constant made a universal and intended behaviour look like a
   defect in one place.

2. **A repair that wants to reach accepted science is already refused, and
   already has a declared route.** `tckdb_raise_if_accepted` is a schema object
   present in every database built from the migration chain, and nothing under
   `backend/alembic/` disables triggers during a migration. ADR 0015 and the
   `accepted_science_repair` ledger (`e2c9a4f7b163`, first used by
   `b8e3f1a7c250`) are the sanctioned way through.

The one migration in the chain that rewrites a scientific *value* —
`a4f7c2e9d651`, converting 46 dihedral series to ADR 0020's contract — states
the position exactly, and anticipates the self-hosted case:

> if a future or self-hosted deployment has approved one of these calculations,
> `tckdb_raise_if_accepted` refuses the `UPDATE` and the whole transaction rolls
> back — correctly: the premise this revision runs under does not hold there,
> and failing loudly is the right outcome, not a gap to route around.

One caveat worth recording, since it is the kind of thing a reader may check:
that migration's "every affected row is `not_reviewed`" is a **measurement of
this corpus, in prose** — it appears only in the docstring, and there is no
runtime `record_review` check in the migration body. It explains why that
revision did not need the repair ledger. It is not what protects another
deployment; the trigger is.

### What is actually left

ADR 0016's original question, unchanged and narrower than the retracted
framing: **acceptance is one flag, so a reviewer who means "the rate constant
is sound, I did not examine the conformers" must over-claim or under-claim.**
That is a record-keeping question about what an endorsement asserts, not a
data-integrity one. Two answers:

- **(a) Accept the claim only**, and accept that the endorsement says nothing
  about the supporting set. What the archive has today.
- **(b) Scoped acceptance.** The reviewer states what they examined. More
  faithful, and it changes `record_review`.

This still wants deciding before a queue with accept buttons is built, because
it decides whether `record_review` gains a column, and it settles the 36-vs-44
question in §2. It does **not** block the review packet recommended in §7,
which is read-only.

---

## 7. What I would do next, in order

1. **Settle §6.** It is a decision, not work. Note it gates a queue with
   accept buttons, not the packet in step 4 — those can proceed in parallel.
2. **Deposit-side gaps 1–4.** A reviewer surface over an archive that cannot
   say whether a saddle is the right saddle, what the tunnelling correction was
   applied to, or how contaminated the barrier wavefunction is will produce
   confident-looking acceptances of unexamined claims — the exact failure ADR
   0016's asymmetry argument is about. Gap 1 (TS statmech) is the one to take
   first. **[inferred]** it is likely an upload-path omission rather than a
   missing capability, since the rate constants could not have been computed
   without it; the database cannot distinguish "never sent" from "sent and
   dropped", so that should be confirmed against the producer before planning
   the fix.
3. **Give `applied_energy_correction` a public ref.** Small, unblocks 8 of 36
   rows here, and also unblocks its supersession notices and any public
   projection of machine review. One fix, three consumers.
4. **Only then**, the surface — and the first version should be a *list with
   evidence*, not a form. The `evidence_completeness` checklist is already the
   right spine; it needs items for tunnelling application and spin
   contamination before it can carry a reviewer's weight.

On the still-open "should this be a web app at all": the walkthrough suggests
the first useful artefact is a **generated review packet** — one page per
claim, with the supporting set and the evidence inline — because that is what
the reviewer actually reads, and it needs no interaction to be useful. The
accept action can stay a CLI call or a single button added later. That is much
cheaper than a queue UI and tests the content before committing to a product.

---

## Appendix: refs used

```
reaction          rxn_om6l6zmb6p365llhryzyx3glsy
reaction entry    rxe_qpwvjtgnwvyer2zdytemwib3te
kinetics          kin_2uhinwoeibtynxycriehpcnkcq
transition state  ts_t2kpzhx3iesspo6wtncggd7q7i
TS entry          tse_eahf5d2bj2me6xuy2mq7l5pqoy
TS freq calc      calc_lvbghv2rahnelughgbwk47erfi
TS IRC calc       calc_ovr6um4pxqmd7iatdkogsgtmqy
species entries   spe_dfcw4tvy6tkqxnyittmn6d3vdu  [CH2]C(C=C)OS
                  spe_suugmv6irq2hzc6ats4ylra5xy  C=CC1CO1
                  spe_ykkkixegdaamduuyekp7bcmq4q  [SH]
statmech          sm_gte23btixcfqj4ozllcxjhx4zi   [SH]     rrho, Cinfv, linear
                  sm_o65tpioqrf4hnekargaumretdm            rrho, C1
                  sm_ucoszpv4horbtmbcapzutnidym            rrho_1d, C1
level of theory   lot_rrmbqrod3suvzkez2ta76hj76u  b3lyp/def2tzvp
correction schemes ecs_q5potmkzrmm6ynh2behv5kbfdu (AEC)
                   ecs_5dzse4an2emubgyxge2dpj4ae4 (BAC)
```

Related: ADR 0016 (review approves a claim), ADR 0003 (freeze), ADR 0013
(imaginary-mode disposition), ADR 0015 (repair path), #222, #478, #479,
`docs/auth-and-roles-v1-spec.md`.
