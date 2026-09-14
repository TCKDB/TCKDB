# One real review, walked end to end

The first step #222 asks for, done. Not a design — a walkthrough of a single
deposited reaction, listing every record a reviewer would have to accept and
what they would need in front of them for each. The point is to derive the
read-API requirements from **what the judgement needs**, rather than from what
happens to be queryable today, and to turn "does acceptance carry a scope?"
into a question with a concrete answer attached.

Everything below was measured on 2026-09-14 against the live Pi instance and
the code at `90fe0aa0`. The Pi is the working playground, not a paper corpus —
these numbers describe *this deployment* and are here to make the exercise
concrete, not to characterise the archive.

## Method

Read-only SQL against the deployed database for the supporting set, and the
live HTTP read API for what a reviewer can actually retrieve. Nothing was
written. Queries are reproducible from the refs quoted throughout.

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
| tunnelling | Eckart |
| uncertainty | A ×1.406 (multiplicative), n ± 0.0444, Ea ± 0.254 kJ/mol |

---

## 2. The supporting set: 36 acceptances for one rate constant

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

Not reviewable, but needed to *judge* the above: 3 species entries, 1 level of
theory, 27 stored artifacts.

The 17 calculations break down as 7 `opt`, 4 `sp`, 4 `freq`, 1 `irc`, 1 `scan`.

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
/api/v1/scientific/reaction-entries/{ref}/kinetics` already returns, per
record: the Arrhenius parameters, uncertainty, `temperature_coverage`,
`supersession`, full `provenance`, a `review` block, a `review_summary`
roll-up across the entry, and an `evidence_completeness` checklist:

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

The barrier of a rate constant taken from a B3LYP single point is a
judgement call a reviewer would want to make deliberately, and the read
surface puts it in front of them without being asked. That is the shape the
rest of this should follow.

**The two false items in the checklist are the interesting ones.**
`has_scf_stability` is false for a system whose reactant is a doublet radical
— exactly where an unstable-wavefunction check matters. The checklist is
already pointing at the right gap; nothing yet makes a reviewer look.

### The transition state (1 `transition_state_entry`, plus its calculations)

**The judgement:** is this a first-order saddle point, and is it the saddle for
*this* reaction?

Half of that is answerable and half is not.

Answerable: the frequency calculation records `n_imag = 1` at
**−719.4703 cm⁻¹**, with `imaginary_mode_tau_cm1 = 30` on basis
`assumed_analytic_default`. One imaginary mode, comfortably large — a reviewer
can see it is not a numerical artefact.

**Not answerable: is that mode the reaction coordinate?** ADR 0013 decided the
archive stores no eigenvectors and that the mode's disposition is *declared*
instead. Measured database-wide: **0 of 124 `calc_freq_result` rows declare
`reaction_coordinate_mode_index`, and all 26 imaginary modes have
`imaginary_disposition` NULL.** The declaration mechanism exists and has never
been used. The single most important question about a transition state has no
recorded answer anywhere in this deployment.

**Also not answerable: does the IRC connect the right endpoints?** There is an
IRC calculation (`calc_ovr6um4pxqmd7iatdkogsgtmqy`), and database-wide there
are 17 IRC results and 1,424 IRC points. But
**`transition_state_validation_evidence` has 0 rows**. The path was computed
and stored; the *conclusion* — this saddle connects these reactants to these
products — is recorded nowhere. Note that `evidence_completeness` reports
`has_path_search_or_irc_evidence: true`, which is true about the calculation
existing and says nothing about connectivity being established. A reviewer
reading that checklist would reasonably believe the question was settled.

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
reviewed, and it is not in the archive. Whatever produced these rate constants
computed it and did not deposit it.

This is the single largest finding of the walkthrough. A reviewer asked to
accept `kin_2uhinwoeibtynxycriehpcnkcq` cannot check the term that most
determines it.

`uses_projected_frequencies` is NULL on all three rows — for a species with a
hindered rotor, whether the rotor mode was projected out of the vibrational
set is a real double-counting question, and "not recorded" is not the same as
"not done".

### Energy corrections (8 rows)

Two schemes: an atom-energy correction (`aec_total`, hartree) and a bond
additivity correction (`bac_total`, kcal/mol), applied to each species and to
the TS.

A worked reviewer check, to show the surface can support one: the `aec_total`
for the reactant `[CH2]C(C=C)OS` and for the transition state are *identical* —
630.0442600297998 hartree. That is not a copy-paste error; it is what an
atom-count-based correction must do for a unimolecular TS, which is isomeric
with its reactant. A reviewer can confirm it at a glance and move on. The BAC
for the TS is exactly 0, which is a different kind of statement and one worth
asking about.

**But none of these eight rows can be named to a reviewer.**
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

One wrinkle: the review PATCH is addressed by **internal row id**, not public
ref. A UI that follows DR-0028 and never shows a row id still needs one to
write. Resolvable — accept a ref and resolve server-side — but it is a
decision, and it is the same seam #478/#479 have been working along.

---

## 5. The read-API gap list, ranked by what it costs a reviewer

| # | Gap | Cost |
|---|---|---|
| 1 | No statmech for any transition state (0/101) | The term most directly under a TST rate constant is absent |
| 2 | Reaction-coordinate mode never declared (0/124); `imaginary_disposition` always NULL (0/26) | "Is that the right saddle?" is unanswerable from the archive |
| 3 | `transition_state_validation_evidence` empty, despite 17 IRC results | IRC connectivity computed, conclusion not recorded — and the checklist reads as if it were |
| 4 | `applied_energy_correction` has no `public_ref` | 8 of 36 rows in this set cannot be linked |
| 5 | `uses_projected_frequencies` NULL | Double-counting question unanswerable for hindered-rotor species |
| 6 | `has_scf_stability` false on a doublet radical | The check that matters most here was not run, and nothing escalates that |

Gaps 1–3 are **deposit-side**: the data was computed and not sent, or the field
exists and nothing fills it. No UI fixes them. They are the honest answer to
"what does the backend need first" — and they are not what #222 guessed, which
was more read routes.

---

## 6. The scope question, in its concrete form

#222 asks whether acceptance should carry a scope, framed around a reviewer's
honest position: *"the rate constant is sound; I did not examine the
conformers."* The walkthrough makes it sharper than that.

Under ADR 0003 accepted science is frozen by `trg_as_*` triggers. Measured:
each child trigger is parameterised with **one** root type and the FK reaching
it, so it consults that root's acceptance and no other. Read off the live
database:

```
calc_freq_result          trg_as_child_*  ->  ('calculation', 'calculation_id')
kinetics_arrhenius_entry  trg_as_child_*  ->  ('kinetics',    'kinetics_id')
```

So accepting `kin_2uhinwoeibtynxycriehpcnkcq` freezes the Arrhenius row and
leaves the frequency calculation it was computed from fully editable. The
freeze groups are disjoint by construction, not by omission.

**The real question is therefore not "should acceptance cascade?" but "what
does an accepted number mean when its inputs can still change underneath it?"**
Three coherent answers, and they lead to different products:

- **(a) Accept the claim only.** Honest about what was judged; an accepted rate
  constant can silently stop matching its inputs. Needs, at minimum, a way to
  detect and surface that drift — which is close to what the reproducibility
  assessment machinery already does.
- **(b) Accept the claim, freeze the closure.** Freeze what the number depends
  on without asserting anyone reviewed it. Distinguishes *frozen* from
  *approved* — which the current model does not, and which would need a new
  state rather than a new cascade.
- **(c) Scoped acceptance.** The reviewer states what they examined. Most
  faithful, most expensive, and it changes `record_review`.

This is the decision to take before any UI. A queue built against (a) and a
queue built against (c) are different products, and #222 already says building
against the wrong one means building twice.

---

## 7. What I would do next, in order

1. **Settle §6.** It is a decision, not work, and everything else depends on it.
2. **Deposit-side gaps 1–3.** A reviewer surface over an archive that cannot
   say whether a saddle is the right saddle will produce confident-looking
   acceptances of unexamined claims — the exact failure ADR 0016's asymmetry
   argument is about. Gap 1 (TS statmech) is the one to take first; it is
   almost certainly an upload-path omission rather than a missing capability.
3. **Give `applied_energy_correction` a public ref.** Small, unblocks 8 of 36
   rows here, and also unblocks its supersession notices and any public
   projection of machine review. One fix, three consumers.
4. **Only then**, the surface — and the first version should be a *list with
   evidence*, not a form. The `evidence_completeness` checklist is already the
   right spine: show the 36 rows, show each one's score and its two or three
   damning facts, and let the reviewer accept one at a time.

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
