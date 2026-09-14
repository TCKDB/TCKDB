# Transition-state browsing — implementation plan

Status: draft for builders and reviewers. Base: `worktree-agent-af7f9877ee5355589` at
`7a6fa0b6` (== `main`). Plan siblings: `docs/plans/reaction-entry-page.md` (the reaction
chooser this plan builds on), `docs/plans/provenance-first-website.md` (house rules,
slices 14/29/30 cover the transition-state pages this plan touches).

## 1. The question

Owner, on `/transition-states`: a reaction can carry several entries, each with its own
transition-state entry, so a reader searching transition states will hit multiple
near-identical rows with no way to tell which one is relevant. He has already ruled the
analogous case for reactions: `/reactions/:rxn_` is a chooser over `rxe_…` deposits, and
`/species/:spc_` is a chooser over `spe_…` entries. Does the transition-state surface need
the same treatment?

**Answer: no new chooser route.** `transition_state` is not the transition-state analogue
of `chem_reaction` — it groups candidates *within one deposit*, not across deposits, and
today it never has more than one. The duplication the owner is describing is the reaction
identity showing through, and the reaction already has a chooser. What is missing is (a)
the browse listing does not use that identity to group its rows, and (b) three of every
four rows in a duplicate group are indistinguishable even after #356's level-of-theory/
software columns. Both are fixed inside the existing `/transition-states` browse page,
with a reused component pattern (`SpeciesBrowseRow`'s grouped-row shape) and no new route,
no new backend endpoint, and no schema change. A genuinely new axis — grouping *within*
one `transition_state` concept when it eventually holds more than one entry — already has
a backend surface (`GET /scientific/transition-states/{ref}`) built and unused; this plan
says why that is a different, currently-empty case and defers its frontend consumption
until data exists.

## 2. Measured facts (verified against the live archive, 2026-09-08, anonymous HTTP only)

Numbers below were re-measured through `GET /api/v1/scientific/transition-states/browse`
and `GET /api/v1/scientific/reactions/browse`, both anonymous, both paginated at
`limit=200` (above every total in scope, so no page-boundary artifacts). No SSH or `psql`
against the Pi was used for this plan — HTTP only, per the task's read-only-access rule.

- `transition_state`: 34 rows. `transition_state_entry`: 34 rows. Confirmed 1:1 today —
  every `transition_state.id` in the browse payload appears with exactly one
  `transition_state_entry_ref`.
- `chem_reaction`: 24 rows. `reaction_entry`: 42 rows (both confirmed by
  `reactions/browse`'s `pagination.total`, grouped client-side by `reaction_ref`).
- 6 `chem_reaction` rows carry more than one `reaction_entry`, all exactly 4 each (not
  "up to 4" — measured, all six are exactly 4): `rxn_xj7yamh5drvxapzlaukpzndbbu`,
  `rxn_naeqmg4l5wyqex5cl5tir2vt2y`, `rxn_dafqi66x66df55rv7pprlaocfa`,
  `rxn_ss2j4rfyavzwq7oyapbb32oote` (all four entries under each carry a transition state),
  and `rxn_y3a5ha462p4rli4b7ha3w6gkdu`, `rxn_22dwjn4am4itjvpp4oe5z3o35i` (no entry under
  either carries a transition state — these two groups are irrelevant to this plan).
- The four TS-bearing groups are all hydrazine network reactions, all with `family: null`
  (matches `docs/plans/provenance-first-website.md` slice 14's "hydrazine reactions have
  no family"), all `not_reviewed`. Correction to the brief: only one of the four groups
  (`rxn_ss2j4rfyavzwq7oyapbb32oote`) has a uniform label across all four rows (`TS1`
  ×4); the other three mix labels within the group (e.g. `TS4, TS4, TS8, TS4`). The
  labels are not even internally consistent as a grouping cue, which is a second reason
  (beyond the no-labels ruling in §5) not to lean on them.
- Across the 34 TS-entry rows, grouping by `reaction.reaction_ref` (a field the browse
  payload already serves on every row) yields **22 distinct groups**: 18 singletons and
  the 4 groups of 4 above. Grouping is a 35% reduction in row count (34 → 22) and removes
  every duplicate-appearing row from the top-level list.
- Within each of the 4 groups, the LOT/software columns `/transition-states/browse`
  already serves (added in provenance-first-website.md slice 14, PR #356) distinguish
  only 1 of 4 rows: 3 entries per group are `wb97xd/def2tzvp` on `Gaussian 09`, 1 is
  `CCSD(T)-F12/cc-pVTZ-F12` on `ORCA` (version not recorded). All four rows in every
  group are `not_reviewed`. So **the distinguishing columns already shipped are
  necessary but not sufficient** — three rows per group still read identically apart
  from their `tse_…` ref and deposit date.
- `TransitionState.entries` (`backend/app/db/models/transition_state.py:29-58`) is a
  one-to-many SQLAlchemy relationship with no unique constraint capping it at one row;
  nothing in the schema, a migration, or a check constraint enforces the 1:1 seen today.
  It is incidental to what has been deposited, not a modelled invariant.

## 3. The crux: is there an identity worth choosing between?

**No, not the one the brief's framing suggested.** Read literally:

```python
# backend/app/db/models/transition_state.py:29-42
class TransitionState(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Reaction-channel-level transition-state concept.

    This groups candidate saddle-point structures that belong to the same
    reaction-channel interpretation.
    """
    __tablename__ = "transition_state"
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reaction_entry.id", ...), nullable=False,
    )
```

`transition_state.reaction_entry_id` points at a **reaction entry** (a deposit), not at
`chem_reaction` (the deduplicated identity). So a `TransitionState` concept is scoped
*inside one deposit* — it groups candidate saddle-point geometries a single depositor
submitted for the same reaction-channel interpretation (e.g. re-optimising the same TS at
a second level of theory, within the same upload lineage). It is **not** the
transition-state equivalent of `chem_reaction`; there is no table that groups
`transition_state_entry` rows *across* deposits the way `chem_reaction` groups
`reaction_entry` rows across deposits. `TransitionStateBrowseRow.tsx:113-116` already says
this in a code comment: "A transition state has no formula of its own — it is identified
by the REACTION it connects", and `TransitionStateEntryPage.tsx:352`'s section intro:
"A transition state is identified by the reaction it connects, not a molecular graph of
its own." Both predate this plan and both are correct: the identity a transition state
inherits is the reaction's, not one of its own.

That reframes the owner's question. The four indistinguishable-looking rows in
`/transition-states/browse` are not four candidates for "the same transition state" in
the sense `chem_reaction` dedupes reactions — they are four **separate `transition_state`
concepts**, one per `reaction_entry`, that happen to sit under the **same `chem_reaction`**.
The identity worth choosing between already exists, and it is `chem_reaction` — the exact
one `docs/plans/reaction-entry-page.md` built a chooser for. A transition state has no
identity of its own to add a second chooser on top of; it borrows the reaction's.

**The genuinely-TS-shaped identity question is different and currently moot.** Within
one `TransitionState` concept (one deposit, one reaction-channel interpretation),
`TransitionState.entries` is 1:many by the model, and `transition_state_selection`
(`backend/alembic/versions/b7e2d4f6a8c1_add_transition_state_selection.py`) exists
specifically to let a curator or workflow mark one entry `curator_pick` /
`lowest_barrier` / `representative_geometry` / etc. among several candidates under one
concept — "the transition-state analog of `conformer_selection`" per that migration's own
docstring. That is a real, modelled chooser mechanism, parallel to species' conformer
selection. It is simply **unpopulated today**: 0 of 34 `TransitionState` rows have more
than one entry, so `transition_state_selection` has nothing to select between yet. A
frontend page for it would ship with zero live examples to verify against. §6 defers it
rather than building it now; §7's recommendation does not depend on it.

## 4. What is the listing for, and does grouping serve it?

`/transition-states/browse` (`backend/app/api/routes/scientific/transition_states_browse.py`)
is the one identifier-free, unauthenticated catalogue over the TS-entry corpus — a reader
who does not already have a `ts_…`/`tse_…`/`rxn_…`/`rxe_…` ref lands here to explore "what
transition states does this archive have", filtered by status, evidence coverage, level
of theory, software, family, or participant SMILES (the route's own docstring names the
audience: "the caller who does not [have a ref]", as opposed to `/transition-states/search`
which is exact-lookup only). That reader is answering "what saddle points exist for
reactions like this one", not "give me every row for one specific reaction" — the second
question already has a dedicated page (`/reaction-entries/:ref`'s Transition states
section, `reaction-entry-page.md` §2 item 5) once a specific deposit is chosen.

A flat list serves a reader poorly the moment two rows describe the same reaction: they
cannot tell, without opening both, whether the second row is independent evidence or the
same computation redeposited. A **grouped listing** — one row per reaction, the
distinguishing entries underneath — directly answers "how many independent transition-
state studies exist for this reaction" at a glance, and STILL lets the reader open any
individual entry. This is exactly the shape `SpeciesBrowseRow.tsx` already uses for
species (one row per `species_ref`, entry chips inside, each entry chip its own link,
`frontend/src/components/SpeciesBrowseRow.tsx:18-51`) — a pattern already reviewed,
shipped, and tested, not a new interaction to invent. No new route is needed because the
browse payload already carries everything a client-side reshape needs: `reaction.reaction_ref`,
`reaction.equation`, `reaction.family`, `reaction.reaction_entry_ref` sit on every record
returned by `/transition-states/browse` today (verified in the 2026-09-08 payload, see §2).

**Why not group by `transition_state_ref` instead of `reaction_ref`.** Grouping by the
`transition_state` concept would produce 34 groups of 1 (today's 1:1 data), doing nothing
for the duplication the owner reported — it groups within the wrong scope (see §3). The
crux answer says the grouping key must be the reaction, not the TS concept, and this
section confirms that key is also the useful one for the listing's actual audience.

## 5. Labels: which side of the line each one falls on

The owner's ruling, verbatim from the task brief: "I kinda don't want labels almost in
general to never appear on the front end cause they make no sense like TS0 and TS1."

This widens `provenance-first-website.md` slice 17's open item ("Two fields really are
submitter-typed and still render, transition-state labels ('TS0') and conformer labels
('conformer_1'); the owner has not yet ruled on them.") into a ruling: **remove them.**
The exemption stays narrow — `species_entry_label` (e.g. "R", "Z") is **not** one of
these labels. It is server-**computed** by `species_entry_label()` from the entry's own
stereo/electronic-state facets (the uniqueness-constraint columns), not typed by a
depositor, and slice 12's own finding is precisely that treating it as a bare heading was
the mistake, not the computation itself — it stays on the exempt side of the line and this
plan does not touch it.

`transition_state.label` (ARC's own string, "TS0"/"TS1"/…) is depositor-typed with no
server derivation, and it currently renders in **three** places, none of them formerly
audited against this widened ruling:

1. `frontend/src/components/TransitionStateBrowseRow.tsx:165,207-209` — visible plain
   text in `.browse-row-meta` (`entryLabel` = `record.transition_state.label ?? "Unlabeled
   transition state"`), and folded into the link's `aria-label` at line 199
   (`` `${equation} (${entryLabel})` ``) — so it reaches both sighted and screen-reader
   readers.
2. `frontend/src/pages/TransitionStateEntryPage.tsx:200-209` — `identity.label = ts.label`,
   rendered by `RecordIdentityHeader`'s `.kv-list` as a plain identity fact (moved there
   from a pill in the #357 review specifically to de-emphasise it, per the comment at
   line 297-307 — a step in the right direction, but the ruling now says remove, not
   demote).
3. `frontend/src/pages/TransitionStateEntryPage.tsx:389` — the siblings ledger's link
   text: `{sibling.transition_state.label ?? "Unlabeled transition state"}`. This is the
   one place where removing the label without a replacement breaks usability: the
   ledger's whole job is letting a reader distinguish one sibling from another, and its
   own comment (`TransitionStateEntryPage.tsx:391-395`) already documents the exact
   failure mode this plan is about — "three siblings on one reaction ... all read 'TS4 ·
   MRCI+Davidson/... · Molpro (version not recorded) · NOT REVIEWED', indistinguishable
   without [ref and date]". The label was already doing approximately nothing there; the
   ref and date beside it are what actually disambiguates.

§6 slice 2 removes all three. None require a backend change — `label` stays in the wire
payload (other deployments/clients may still want it) and is simply not rendered.

## 6. Recommendation: numbered slices

**Slice 1 — group `/transition-states/browse` rows by reaction.** Files:
`frontend/src/components/TransitionStateBrowseRow.tsx` (becomes a group-row renderer,
mirroring `SpeciesBrowseRow.tsx`'s shape), a new small grouping helper (e.g.
`frontend/src/domain/transitionStateBrowseGroups.ts`, parallel to how
`reactionEquation.ts` isolates a pure function), `browse.css` (new `.ts-browse-row`
group-chip styling reusing `.browse-entry-chip`/`.browse-row-entries` primitives —
no new visual language). Client-side only: group the already-fetched
`records[]` by `reaction.reaction_ref` (falling back to grouping by
`transition_state_entry_ref` alone for the pathological case `reaction.reaction_ref` is
null, which does not occur today but must not crash if a future record lacks it); render
one row per group, headline = the group's equation (`ReactionEquation`-style rendering
already used elsewhere, or the raw `equation` string if that component is not reused),
review/family/evidence summarised across the group the way `ReactionOverviewPage`
summarises across entries; one entry chip per `transition_state_entry_ref` inside,
carrying status pill, review pill, deposited date, and the LOT/software line already
built (`provenanceSummary()`, unchanged) — each chip links to
`/transition-state-entries/:ref` exactly as today. A single-entry group renders with
one chip, visually indistinguishable from today's un-grouped row (no regression for the
common case — 18 of 22 groups are singletons today). Pagination caveat (§7): at today's
scale (34 rows, default `limit=50`) every row is on one page, so grouping is exact; this
is a stated, accepted limitation, not solved here.

**Slice 2 — strip depositor-typed labels from the transition-state surfaces.** Files:
`TransitionStateBrowseRow.tsx` (drop `entryLabel` from `.browse-row-meta` and from the
`aria-label`, per §5 item 1 — the accessible name becomes the equation alone, matching
`ReactionBrowseRow`'s pattern of never spelling out a submitter label), and
`TransitionStateEntryPage.tsx` (drop the `identity.label` kv-list row, §5 item 2, and
replace the siblings ledger's link text, §5 item 3, with the ref + deposited date it
already renders beside the label today — i.e. lead the sibling row with something like
`Saddle point deposited {date}` or simply make the existing `<code>{siblingRef}</code>`
the link's accessible content, so nothing is lost, only the meaningless "TS4" is dropped).
`ts.label`/`sibling.transition_state.label` stay in the parsed API response shape
(Zod schemas untouched) since other consumers of the wire format may still want the raw
producer string — only the rendering is removed. Both slices' tests assert the string
"TS0"/"TS1"/etc never appears in the rendered DOM of these three surfaces (the
`dead-css-class.test.ts`-style "assert absence, not just presence-of-replacement" pattern
`provenance-first-website.md` slice 16 calls out as the thing worth pinning).

**Slice 3 — no new route.** Explicitly do not add `/transition-states/:tsRef`. Record the
decision in this plan (§3) rather than as a silent omission, since the reaction precedent
makes "why doesn't TS get one too" a fair question to have answered in writing. The
existing `GET /scientific/transition-states/{transition_state_ref_or_id}` endpoint
(`backend/app/schemas/reads/scientific_transition_state.py:510-536`,
`ScientificTransitionStateRecord` with `entries[]`, `entries_summary`, concept-scope
`evidence_summary`) stays unconsumed by the frontend. This is a real, tested backend
capability with no matching UI — not a gap this plan closes, because there is nothing
live to show on it (§3, §2). Revisit only when a `TransitionState` concept with >1 entry
is deposited (watch for `entries_summary.total > 1` on any live record) or when
`transition_state_selection` gets its first row; at that point the concept detail page is
a bounded addition (a thin page over an endpoint that already exists, in the same shape
`ReactionOverviewPage` used over `reactions/search`) — not blocked on anything in this
plan.

**Slice 4 — leave the reaction-chooser interaction as-is, and confirm it already answers
the "which one is mine" question.** No code change; this slice is verification, folded
into slice 1's PR review. `ReactionOverviewPage.tsx` (`/reactions/:reactionRef`) already
lists every `rxe_…` deposit with a `Has TS` pill (`frontend/src/pages/
ReactionOverviewPage.tsx:127-135`, `record.availability.has_transition_state`), and
`TransitionStateEntryPage.tsx`'s siblings ledger (§5 item 3, `useTransitionStateSiblings`
at line 276, backed by `GET /transition-states/search?reaction_ref=…` — a
cross-deposit, TS-entry-grain query already scoped by the reaction identity) already lets
a reader who lands on one TS entry see every other TS entry under the same reaction. Both
paths existed before this plan (provenance-first-website.md slices 14 and 29). The
reviewer for slice 1's PR should confirm, by walking both live routes, that grouping the
browse listing does not duplicate or contradict either — it is a third, top-level entry
point into the same underlying fact (multiple TS entries share a reaction), not a
competing model of it.

## 7. Backend gaps

**None required to ship §6.** Every field slice 1's grouping needs
(`reaction.reaction_ref`, `.equation`, `.family`, `.reaction_entry_ref`) is already served
by `/transition-states/browse` (verified live, §2). Slice 2 is render-only. Slice 3 is
explicitly declining to build a frontend for an endpoint that already exists.

**One deferred, named gap:** `/transition-states/browse`'s pagination operates at
TS-entry grain (`offset`/`limit` slice individual `transition_state_entry` rows,
`backend/app/api/routes/scientific/transition_states_browse.py`'s `browse_transition_states`),
not at reaction grain. Client-side grouping (slice 1) is exact only when every entry of
a multi-entry reaction lands on the same page. At today's scale (34 total, default
`limit=50`) that is always true, so this is not a blocker. If the TS corpus grows past
one default page while duplicate-reaction groups remain common, the fix is a
`GET /scientific/transition-states/browse` variant (or a `group_by=reaction` toggle on
the existing route) that paginates at `reaction_ref` grain the way `reactions/browse`
already paginates at `chem_reaction`-adjacent `reaction_entry` grain — named here so a
future agent does not have to rediscover the mismatch, not because it needs building now.

## 8. Owner rulings (verbatim, for the record)

- On labels, widening `provenance-first-website.md` slice 17's open item: "I kinda don't
  want labels almost in general to never appear on the front end cause they make no
  sense like TS0 and TS1." Applied in §5/§6 slice 2. `species_entry_label` is exempt
  (server-computed, not depositor-typed) and unaffected.
- On the core question of this plan (paraphrased from the task, not yet an explicit
  owner ruling): whether transition states need a chooser mirroring `/reactions/:ref`.
  This plan's answer is no (§3) — the identity worth choosing between is the reaction's,
  and it already has one. This is a recommendation for the owner to confirm, not a
  ruling already made; flagged as the one open decision below.

## 9. Open decision for the owner

Confirm or reject §3's conclusion: no `/transition-states/:tsRef` chooser route, because
`transition_state` groups candidates *within one deposit* (currently always exactly one)
rather than *across* deposits the way `chem_reaction` does, and the cross-deposit case the
original question was actually about is already served by the reaction chooser plus (after
slice 1) a grouped browse listing. If the owner instead wants a dedicated `/transition-
states/:tsRef` page regardless — e.g. as a stable landing point independent of which
reaction entry a reader started from — slice 3 names the exact endpoint
(`GET /scientific/transition-states/{ref}`) and schema
(`ScientificTransitionStateRecord`) already available to build it from; it would be a
same-shaped addition to the ones `reaction-entry-page.md` PR 2 already shipped, not a new
kind of work.

## 10. Acceptance criteria

- `/transition-states/browse` at 34 live rows renders 22 group rows, 18 of them
  single-entry (visually unchanged from today) and 4 of them 4-entry groups whose
  headline states the shared equation once and whose four chips carry distinct refs,
  dates, LOT/software (3+1 split, per §2), status and review pills.
- No rendered DOM on `/transition-states/browse` or `/transition-state-entries/:ref`
  contains a bare depositor-typed transition-state label string ("TS0", "TS1", …,
  matched by the live archive's actual label set) after slice 2, verified by an absence
  assertion (not merely a presence-of-replacement one) mutated to confirm it is load-
  bearing — the "assert absence, watched failing" pattern from
  `provenance-first-website.md` slice 16.
- The siblings ledger on `/transition-state-entries/:ref` still lets a reader
  distinguish every sibling (ref, date, LOT, software, review all still present and
  visible) with the label gone — no information loss, only the meaningless string
  removed.
- `ReactionOverviewPage`'s `Has TS` column and `TransitionStateEntryPage`'s siblings
  ledger are unchanged by this plan and continue to work exactly as before (slice 4
  verification).
- No new route, no new backend endpoint, no migration. `git diff` for this work touches
  only `frontend/src/components/TransitionStateBrowseRow.tsx`,
  `frontend/src/pages/TransitionStateEntryPage.tsx`, a new small grouping helper module,
  and their tests/CSS.
