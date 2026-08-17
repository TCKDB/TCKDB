# Atom maps that arrive after the deposit

**Status: descriptive.** This document decides nothing. It records what a
depositor can and cannot do about a reaction that was deposited before its
atom map existed, because every individual rule involved is working as
designed and their composition is still a surprise.

[ADR 0011](../../../docs/adr/0011-atom-mapping-is-declared-not-inferred.md)
tells a depositor without a map to deposit anyway: absence warns, it does not
block. That is the right rule -- refusing would reject correct science over
evidence the depositor may not have. This document is the other half of that
sentence: what the honest partial deposit costs, and what routes exist to
improve it afterwards.

It is written because the question is about to become real rather than
hypothetical. On the live deployment, measured 2026-08-12:

| | |
|---|---|
| `transition_state` | 34 |
| `transition_state_entry` | 34 |
| `reaction_atom_map` | **0** |
| `transition_state_entry` reviews ever approved | **0** |

Every transition state in the deployed corpus was deposited without a map,
and none of them has ever been approved. The ARC adapter is being taught to
emit maps; the moment it does, "what about everything already deposited?"
is a question someone will ask.

Every claim below is pinned by a test. Where a claim is about something that
*cannot* be done, the test asserts the refusal.

## The short version

There is **no route that adds a map to an already-deposited reaction**, at
any review status, through the API. Not because it is forbidden -- for an
unapproved record it is not -- but because no endpoint exists that writes a
map onto a record that already exists. Re-depositing produces a second,
unlinked record instead. The supersession machinery, which is the correction
path for a record that is *wrong*, cannot express "this deposit replaces that
one" for two separately deposited reactions.

## Case 1: absent map, record never approved

**This is where all 34 deployed transition states are.**

At the database level nothing is frozen. The accepted-science guards
installed by
[`b6c1f4a8e703`](../../alembic/versions/b6c1f4a8e703_freeze_declared_atom_maps.py)
fire only once a `transition_state_entry`'s review has reached `approved`, so
a map under an unapproved entry can be inserted, edited, and deleted freely.
That is asserted by
`tests/db/test_atom_map_immutability.py::test_map_of_unapproved_transition_state_entry_stays_editable`.

**But there is no API route that does it.** `reaction_atom_map` is written in
exactly one place -- `app/services/reaction_atom_map.py`, a pure INSERT with
no lookup and no upsert -- reached only from the upload workflows. There is
no `PATCH`/`PUT`/`DELETE` for an atom map, a `reaction_entry`, or a
`transition_state_entry` anywhere in `app/api/routes/`.

So the only thing standing between these 34 records and a map is the absence
of a write surface, not a rule about correctness. That is worth stating
plainly, because it is the cheapest of all the cases to change and the one
most likely to be assumed impossible by analogy with the others.

## Case 2: absent map, record approved

The database refuses. `tckdb_guard_accepted_child` fires on `INSERT` as well
as `UPDATE` and `DELETE`, so a map cannot be *added* to an accepted
transition-state entry:
`tests/db/test_atom_map_immutability.py::test_new_map_cannot_be_attached_to_an_approved_transition_state_entry`.

This is deliberate and argued in `b6c1f4a8e703`'s module docstring:
attaching a mechanistic claim to a record reviewers already accepted changes
what was accepted. The reviewers approved a saddle point *without* a stated
mechanism; a map added afterwards would be carried by their approval without
ever having been in front of them.

The prescribed correction is to deposit a new transition-state entry carrying
the map, approve it, and record a `transition_state_entry` supersession. See
the next section for the catch.

## Case 3: a map that is wrong

This case has a working, tested route -- and it is the only one that does.

`POST /api/v1/curation/scientific-record-supersessions` accepts
`transition_state_entry` (one of ten admitted types; the CHECK constraint is
`ck_scientific_record_supersession_supported_type` in
[`c6f2a9d4e7b1`](../../alembic/versions/c6f2a9d4e7b1_enforce_accepted_science_immutability.py)).
The wrong map is preserved verbatim, a new entry carries the corrected map,
and an edge joins them. Proven end to end through the HTTP route by
`tests/db/test_atom_map_immutability.py::test_a_wrong_map_is_corrected_by_superseding_its_transition_state_entry`.

**The catch, and it is the important part of this document.** That test
builds its replacement entry *directly in the session*, under the **same**
`transition_state` as the original. `supersession_subject` for a
`transition_state_entry` is `(row.transition_state_id,)`
(`app/services/accepted_science.py`), so two entries can only be joined if
they hang off one `transition_state` row.

Every deposit mints a brand-new `TransitionState` -- see
`app/workflows/computed_reaction.py`, which constructs `TransitionState(...)`
unconditionally -- and no upload schema accepts an existing
`transition_state_id` (the only `existing_*` fields are
`existing_calculation_id`, `existing_conformer_id`,
`existing_conformer_observation_id`, `existing_species_entry_id`).

Therefore: **the supersession route works, but through the API there is no
way to produce the second entry it needs.** The refusal is asserted by
`tests/api/scientific/test_api_late_atom_map_deposit.py::test_supersession_cannot_join_two_separately_deposited_entries`,
which deposits twice through `POST /api/v1/uploads/computed-reaction` and
gets `400 ... must describe the same subject`.

That refusal is the correct failure -- the route declines rather than
recording an edge between records that are not the same subject. But it means
Case 2's prescription currently has no API-only route, and a reader of
`b6c1f4a8e703` would not learn that from the migration alone.

## Case 4: re-depositing with the map

The obvious move, and what it actually does.

A second `POST /api/v1/uploads/computed-reaction` carrying the map produces:

- the **same** `chem_reaction` -- identity is deduped on `stoichiometry_hash`
  (`app/services/reaction_resolution.py`), so the reaction concept is shared;
- a **new** `reaction_entry`. `ReactionEntry` has no unique constraint and no
  lookup before insert; the workflow constructs one unconditionally;
- a **new** `transition_state` and `transition_state_entry`;
- the map attached to the new records only. The original entry still reads as
  unmapped.

Asserted by
`tests/api/scientific/test_api_late_atom_map_deposit.py::test_redepositing_with_a_map_creates_a_second_unlinked_reaction_entry`
and `::test_each_deposit_mints_its_own_transition_state`.

The corpus now holds two entries for one reaction, one mapped and one not,
with nothing but the shared `chem_reaction_id` relating them, and no way to
say which supersedes which -- `reaction_entry` is not an admitted supersession
type, and the `transition_state_entry` route is blocked by the subject check
above.

**This is the outcome to know about in advance.** Discovered by accident it
looks like a duplicate-data bug; understood in advance it is a deliberate
consequence of `reaction_entry` being an append-only deposit record rather
than a deduped identity row.

## Case 5: retrying with the same idempotency key

`409 idempotency_conflict`, and nothing is written. Asserted by
`tests/api/scientific/test_api_late_atom_map_deposit.py::test_replaying_the_original_key_with_the_corrected_payload_writes_nothing`.

**This is the key working, not an obstacle.** An idempotency key promises
that one key means one request. The corrected payload is a different request,
so honouring the key would mean either silently ignoring the correction or
silently applying a body the caller never associated with that key. Refusing
is the only answer that keeps the promise, and the refusal happens in the
dependency before the route body runs, so no partial deposit lands
(`app/services/idempotency.py`, `app/api/idempotency.py`).

**What to do instead:** use a new idempotency key. The corrected deposit is a
new request and deserves a new key. Note that this lands you in Case 4 -- a
new key does not make the second deposit link to the first, it just lets it
happen.

## The 34 deployed records: options, not a decision

Whether to retro-fit maps onto the deployed corpus is a curation call and is
deliberately **not** made here. ADR 0011 puts retroactive mapping explicitly
out of scope and accepts a corpus split between mapped and unmapped records
rather than solving it. What follows is what each option would cost.

The relevant fact is that **none of the 34 has ever been approved**, so
Case 1 applies to all of them and the accepted-science freeze is not the
obstacle. That is a property of today's deployment, not a guarantee; approve
any of them and it moves to Case 2 permanently, since `first_approved_at` is
never cleared.

**A. Leave them.** The corpus splits: 34 unmapped reactions alongside newly
mapped ones. Consumers that need maps filter them out; `export_ml_reactions`
continues to emit them without maps. Costs nothing, and is what ADR 0011
anticipated. The split is permanent and grows no larger once the adapter
emits maps.

**B. Re-deposit them with maps.** Available today with no code change. Yields
Case 4 for each: 34 second entries, unlinked, with the originals still
unmapped and still present. Doubles the transition-state count and leaves a
reader unable to tell which of two entries is current. ADR 0011 contemplates
this ("the reactions that matter can be re-uploaded with maps") but predates
the observation that nothing marks the old one as superseded.

Half of that objection has since been answered and half has not. Where a
supersession edge *is* recorded, a read now says so — see
[`accepted_science_immutability.md`](accepted_science_immutability.md)
§"Announcing a replacement on a read" — but `transition_state_entry` is not yet
one of the record types wired to that resolver, and a bare re-deposit records
no edge at all, so it still yields two entries with nothing connecting them.
The edge is what carries the notice; option B on its own creates no edge.

**C. Add a write surface for maps on unapproved records.** A route that
attaches a map to an existing `transition_state_entry`, permitted only while
that entry is unapproved -- which is exactly the window the database already
leaves open. This is the smallest change that makes the deployed corpus
improvable in place, and it introduces no exception to the accepted-science
regime, because the guard already permits these writes. It needs its own
argument about who may declare a map for a record someone else deposited,
which is a real question and not a formality.

**D. Make the second entry linkable.** Either admit `reaction_entry` as a
supersession type, or accept an existing `transition_state_id` on upload so a
second entry can be created under the same transition state. Both are changes
to the accepted-science regime's shape rather than to atom maps, and both
deserve their own decision record.

Whichever is chosen, the depositor-facing rule from ADR 0011 does not change:
a map is declared by whoever ran the calculation. None of these options
permits TCKDB to infer one.

## What is pinned by tests

| Claim | Test |
|---|---|
| Deposit without a map is accepted and warned | `test_api_late_atom_map_deposit.py::test_depositing_without_a_map_warns_rather_than_blocks` |
| Re-deposit creates a second unlinked entry | `::test_redepositing_with_a_map_creates_a_second_unlinked_reaction_entry` |
| Each deposit mints its own transition state | `::test_each_deposit_mints_its_own_transition_state` |
| Same key + corrected payload writes nothing | `::test_replaying_the_original_key_with_the_corrected_payload_writes_nothing` |
| Supersession cannot join two deposits | `::test_supersession_cannot_join_two_separately_deposited_entries` |
| Map under an unapproved entry is editable | `test_atom_map_immutability.py::test_map_of_unapproved_transition_state_entry_stays_editable` |
| Map cannot be added to an approved entry | `::test_new_map_cannot_be_attached_to_an_approved_transition_state_entry` |
| A wrong map is corrected by supersession | `::test_a_wrong_map_is_corrected_by_superseding_its_transition_state_entry` |
