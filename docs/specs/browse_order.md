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

Every browse and list read orders its records by the same three keys, most significant first:

| Key | Direction | Meaning |
|---|---|---|
| `review_rank` | ascending | The record's review status, ranked as in the table below. |
| `created_at` | descending | **Deposition time**: when the row was created in TCKDB, newest first. This is not when the calculation ran or when a paper was written. |
| `id` | descending | A unique tie-breaker so the order is total. It is an internal row identity and carries no scientific meaning. |

Some reads add a key that is a *fact about the record* ahead of these (for example whether a species has any visible
entry, whether a reaction has kinetics, a calculation's quality flag, or the parent record's own review rank). Each such
key is named and explained in the response. Those evidence keys are scheduled for removal from the display order; the
order above is the one that survives.

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
them?". Each reports one of three outcomes over the *eligible* records:

- `sole_eligible_candidate`: exactly one record is eligible. It is the only one; that is not a claim that it is best.
- `incomparable_alternatives`: several are eligible and TCKDB does not rank them. They are listed in display order,
  and `result_mode=first` may name the first of them, labelled administrative.
- `no_applicable_candidate`: none is eligible, with the reasons.

The `policy` field of a selection request names an *administrative* display order only (`default`, `most_reviewed`,
`latest`). It is never a method claim.

## Visibility defaults

- `rejected` and `deprecated` records are hidden by default; a request opts in with `include_rejected` or
  `include_deprecated`. The `curated` read profile never shows them.
- The `exploratory` read profile shows `approved`, `under_review` and `not_reviewed` records.
- The `curated` read profile has a review floor of `approved`: nothing below it is returned, counted or named.

Visibility and ordering are independent: hiding a record never reorders the rest, and ordering never reveals a hidden
one.
