# Browse order: what the first record means

TCKDB separates three operations on the records of one kind. They are different questions, and a response says which
one it answered.

| Operation | Question | What decides it |
|---|---|---|
| **Browse** | In what order do I see the records? | A documented, deterministic **display order** (this page). |
| **Filter** | Which records qualify? | **Facts**: a status, a phase, a temperature window, a species. |
| **Select** | Which record fits my question? | A **policy the request states**, with the choice or the ambiguity explained. |

TCKDB carries **no built-in ranking of one method above another**. There is no rule that says one level of theory,
one composite recipe or one barrier method beats another, so no read can return "the preferred result". The first
record in a list is the first record in display order. It is not recommended, preferred or best, and no client should
present it that way.

## The display order

Most browse and list reads order their records by the same three keys, most significant first. The sections after the
table list every read that does not, so that this page can be read as exhaustive:

| Key | Direction | Meaning |
|---|---|---|
| `review_rank` | ascending | The record's review status, ranked as in the table below. |
| `created_at` | descending | **Deposition time**: when the row was created in TCKDB, newest first. This is not when the calculation ran or when a paper was written. |
| `id` | descending | A unique tie-breaker so the order is total. It is an internal row identity and carries no scientific meaning. |

Some reads add a key that is a *fact about the record* ahead of or among these (for example whether a species has any
visible entry, whether a reaction has kinetics, a calculation's quality flag, or the parent record's own review rank).
Each such key is named and explained in the response. Those evidence keys are scheduled for removal from the display
order; the order above is the one that survives.

### Reads whose order differs

The response's `request.sort` is the authority for any one read; it names every key. The differences:

| Read | Order | Note |
|---|---|---|
| Structure search (exact, substructure) | `review_rank`, `species_entry_id` (descending) | No `created_at`: the entry's own id is the tie-breaker. |
| Structure search (similarity) | `similarity_score` (descending), `review_rank`, `species_entry_id` | Closest match first. |
| Species browse with a substructure or similarity query | `heavy_atom_count` (ascending), then the usual species keys | Smallest match first. An exact query, or no query, has no size key. |
| Thermo search, kinetics search | The parent entry's review rank and id, then `review_rank`, `created_at`, `id` within each entry | Records of one entry stay together. |
| Species calculations search | `ranking=default` is `review_rank`, `created_at`, `id`; `latest` and `earliest` use time alone; `lowest_energy` leads with `energy_hartree` | The ranking is a stated request option, echoed. |
| Calculations search | `review_rank`, `quality_rank`, `created_at`, `id` | `quality_rank` is a fact about the calculation (curated, raw, rejected, unknown). |
| Artifact lists | `review_rank`, artifact deposition time, artifact id | |
| Literature records | `record_type`, `created_at`, `id` | |
| Level of theory, frequency scale factor and energy correction scheme lists | Their own keys: method and basis; scale kind and value; scheme kind and name; then `id` ascending | Reference lists, not records with a review state. |
| Artifact integrity, calculation paths | `last_observed_at` then `sha256`; `point_index` | Ordered by what they list. |

Two reads that choose a single record (`collapse=first` with a `selection_policy`) can use a different order from the
candidate list. They echo it separately as `request.effective_order`: `latest` drops `review_rank` and orders by
`created_at` then `id`; `default` and `most_reviewed` use the three keys above. `request.sort` keeps describing the
candidate list. The per-species thermo, statmech and transport reads all carry `effective_order`.

### What each review status means, and how it ranks

| Status | Rank | Meaning |
|---|---|---|
| `approved` | 0 | A curator reviewed the record and accepted it. |
| `under_review` | 1 | A curator has opened the record. This is **not a verdict**, so it earns no place ahead of an unreviewed record. |
| `not_reviewed` | 1 | Nobody has reviewed the record, including a record with no review row. |
| `deprecated` | 2 | Superseded or withdrawn after review. Hidden by default. |
| `rejected` | 3 | Reviewed and refused. Hidden by default. |

`under_review` and `not_reviewed` rank **equal**: within that tier the order is deposition time, newest first, then the
tie-breaker. The rank table has one definition (`REVIEW_RANK` in `backend/app/schemas/reads/scientific_common.py`); the
SQL `CASE` that orders a database query is generated from it, never copied.

A *review floor* (`min_review_status`, or a read profile's floor) is a different question from display order, so it
does not use this rank. A floor of `under_review` still admits `approved` and `under_review` records and excludes
`not_reviewed` ones, as before.

## What the order is, and is not

- **Deterministic.** For the same data, the same filters and the same read profile, the order is the same.
- **Descriptive.** It orders by review status and deposition time. It says nothing about which value is more accurate.
- **Echoed.** A browse or list response carries `request.sort` as a structured `display_order`: each key, its direction
  and its meaning, and a sentence saying the order is not a scientific preference. A single-record read that collapses
  the list (`collapse=first`) says which order it used; the record it returns is the first in that order.
- **Not stable under new deposits.** A page is a slice of the live ordering. A new deposit can sort above your position
  and shift an offset page by one (a record can be skipped or seen twice between pages). Keyset cursors with a snapshot
  watermark (the analytics endpoints) are stable against new insertions; stable paging for the other lists is a later
  change.

## Selection is a separate operation

The `.../select` endpoints answer "which records can answer my stated question, and what is the situation among
them?". Every one of them reports these three outcomes over the *eligible* records:

- `sole_eligible_candidate`: exactly one record is eligible. It is the only one; that is not a claim that it is best.
- `incomparable_alternatives`: several are eligible and TCKDB does not rank them. They are listed in display order,
  and `result_mode=first` may name the first of them, labelled administrative.
- `no_applicable_candidate`: none is eligible, with the reasons.

The structure and statmech selections have more outcomes, because they also answer questions that are not "which
record": `no_candidates`, `evidence_conflict` and `unresolved_comparability` (structure only), `energy_unavailable`,
`recorded_minimum`, `representative_minimum`, `qualified_evidence` and `validated_corpus_minimum` (structure), and
`qualified_models`, `unresolved_applicability`, `missing_required_data`, `unsupported_task` and `unsupported_model`
(statmech). They state what was established and never rank one method above another; their definitions are in
`structure_selection_decision.md` and `statmech_selection_contract.md`.

The `policy` field of a selection request names an *administrative* display order only (`default`, `most_reviewed`,
`latest`). It is never a method claim. The structure selection's administrative key (version 2) is: `review_rank`
ascending, `quality_rank` ascending, `created_at` descending, then the public ordinal descending. `quality_rank` (a
fact about a determination's source calculation: curated, raw, rejected, unknown) is used only to order exact ties among
alternatives or to label an administrative first; it never moves a record across a scientific distinction.

## Visibility defaults

- `rejected` and `deprecated` records are hidden by default; a request opts in with `include_rejected` or
  `include_deprecated`. The `curated` read profile never shows them.
- The `exploratory` read profile shows `approved`, `under_review` and `not_reviewed` records.
- The `curated` read profile has a review floor of `approved`: nothing below it is returned, counted or named.

Visibility and ordering are independent: hiding a record never reorders the rest, and ordering never reveals a hidden
one.
