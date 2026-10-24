# Browse order: what the first record means

TCKDB separates three operations on the records of one kind. They are different questions, and a response says which
one it answered.

| Operation | Question | What decides it |
|---|---|---|
| **Browse** | In what order do I see the records? | A documented, deterministic **display order** (this page). |
| **Filter** | Which records qualify? | **Facts**: a status, a phase, a species, a level of theory. |
| **Select** | Which record fits my question? | A **policy the request states**, with the choice or the ambiguity explained. |

TCKDB carries **no built-in ranking of one method above another**. There is no rule that says one level of theory,
one composite recipe or one barrier method beats another, so no read can return "the preferred result". The first
record in a list is the first record in display order. It is not recommended, preferred or best, and no client should
present it that way.

A display tie-breaker never becomes a selection rule. A browse read returns **every** matching record; "give me one"
happens only through the select endpoints, which label what they did (see "Reads never pick" below).

## The display order

Most browse and list reads order their records by the same three keys, most significant first. The sections after the
table list every read that does not, so that this page can be read as exhaustive:

| Key | Direction | Meaning |
|---|---|---|
| `review_rank` | ascending | The record's own review status, ranked as in the table below. |
| `created_at` | descending | **Deposition time**: when the row was created in TCKDB, newest first. This is not when the calculation ran or when a paper was written. |
| `id` | descending | A unique tie-breaker so the order is total. It is an internal row identity and carries no scientific meaning. |

**Evidence completeness is not a key.** Whether a reaction has kinetics or a transition state, whether a species has
an entry, a calculation's quality flag and a record's evidence score are *facts about a record*. They are filters
(`has_kinetics`, `has_transition_state`, `quality`, ...) and displayed fields. None of them orders a list.

Each record carries its own `created_at` where the order uses it (thermo and kinetics records include it, so a client can
check the order it was served).

### Reads whose order differs

The response's `request.sort` is the authority for any one read; it names every key. The differences:

| Read | Order | Note |
|---|---|---|
| Species search and browse | `review_rank`, `created_at`, `id` | A species has no review status of its own. Its `review_rank` is the **best rank among its entries the profile shows**; a species with no visible entry ranks after every reviewed one. That is the species' review standing, derived from review rows, not an evidence count. |
| Structure search (exact, substructure) | `review_rank`, `species_entry_id` (descending) | No `created_at`: the entry's own id is the tie-breaker. |
| Structure search (similarity) | `similarity_score` (descending), `review_rank`, `species_entry_id` | Closest match first. The request chose it (`mode=similarity`), and `request.sort.requested_by` says so. |
| Species browse with a substructure or similarity query | `heavy_atom_count` (ascending), then the usual species keys | Smallest match first. The request chose it (`requested_by: "mode=substructure"` or `"mode=similarity"`). An exact query, or no query, has no size key. |
| Thermo search, kinetics search | `review_rank`, `created_at`, `id` of the **record itself** | One flat list. The parent entry's review status and id order nothing, and records of one entry are not kept together. |
| Species calculations search | `ranking=default` is `review_rank`, `created_at`, `id`; `latest` and `earliest` use time alone; `lowest_energy` leads with `energy_hartree` | A non-default ranking is a stated request option, echoed as `requested_by` (`"ranking=lowest_energy"`). |
| Calculations search | `review_rank`, `created_at`, `id` | Quality is a filter (`quality`, `include_rejected_quality`), not a key. |
| Network kinetics search | `review_rank`, `created_at`, `id` | A network kinetics row has no review row of its own; its `review_rank` is the **parent solve's**. |
| Artifact lists | `review_rank` (the owning calculation's), `artifact_created_at`, `artifact_id` | An artifact is reviewed through its calculation. |
| Literature records | `record_type`, `created_at`, `id` | Records citing one paper, grouped by kind. |
| Level of theory, frequency scale factor and energy correction scheme lists | Their own keys: method and basis; scale kind and value; scheme kind and name; then `id` ascending | Reference lists, not records with a review state. |
| Artifact integrity, calculation paths | `last_observed_at` then `sha256`; `point_index` | Ordered by what they list. |
| Analytics reads | `review_rank`, `created_at`, `id` | Behind a keyset cursor with a snapshot watermark. |

An echo test (`backend/tests/api/test_browse_order_echo.py`) seeds rows whose review status, deposition time and id
disagree, calls every list endpoint above, and requires the served order to equal the order the response echoed.

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
  and its meaning, and a sentence saying the order is not a scientific preference. When the request itself asked for the
  order (`ranking=...`, `mode=...`), `requested_by` names the parameter and value; it is `null` for the default order.
- **Not stable under new deposits.** A page is a slice of the live ordering. A new deposit can sort above your position
  and shift an offset page by one (a record can be skipped or seen twice between pages). Keyset cursors with a snapshot
  watermark (the analytics endpoints) are stable against new insertions; stable paging for the other lists is a later
  change.

## Reads never pick

`collapse` and `selection_policy` are gone from every browse read (species, species browse, reactions, thermo, statmech,
transport, kinetics, species calculations, and the thermo and kinetics searches). A request that names either gets
**422 `browse_parameter_removed`**, whose `context` names the select endpoint that answers "give me one":

| Record kind | The select endpoint |
|---|---|
| Thermo | `POST /scientific/species-entries/{ref}/thermo/select` |
| Kinetics | `POST /scientific/reaction-entries/{ref}/kinetics/select` |
| Statmech | `POST /scientific/statmech/select` |
| Calculations and conformers | `POST /scientific/species-entries/{ref}/calculations/select` (structure selection) |
| Transport, species, reactions | none yet; filter the list |

`pagination` carries `offset`, `limit`, `returned` and `total`; there is no second, post-collapse total because nothing collapses.

### Filters match any linked calculation

`level_of_theory_id`, `level_of_theory_ref` and `software` on the thermo and kinetics reads keep a record when **any**
source calculation linked to it ran at that level or in that software. There is no "primary" calculation for a filter and
no role priority (a record's displayed `primary_calculation` is a display choice, and it decides no filter). A thermo
record's calculations are its own source links; a kinetics record's are its own source links the profile shows.

### Coverage windows are not filters

`coverage_temperature_min_k` / `coverage_temperature_max_k` (thermo and kinetics, per-entry and search) filter nothing:
they set the window each record's `temperature_coverage` is computed against, and records outside it are still returned.
They were called `temperature_min` / `temperature_max`; the old names get **422 `parameter_renamed`**. `pressure_bar` on
the kinetics reads is a real filter (a record that does not apply at that pressure is dropped) and keeps its name.

## Exports never pick

| Format | What it holds | What it does with several candidates |
|---|---|---|
| Native NDJSON (`GET /scientific/export/ndjson`) | many records per species and reaction | exports **all** of them, in display order; its manifest says `records: "all_matching"` |
| ML species NDJSON | many | all: `thermos`, `frequency_results` and `hessians` are lists, one entry per record or calculation, each naming its calculation and level of theory |
| ML reactions NDJSON | many | all: `transition_states` lists every TS entry the profile shows (in display order: review rank, newest deposited, id descending), each with the energy and geometry of EVERY calculation, grouped by level of theory; `barriers` has one barrier per TS entry and per level at which the TS and every reactant have an energy (`E_TS - sum(E_reactants)` inside that one level, from the lowest single point at the level, named by `ts_calculation_ref`; each reactant energy names its `calculation_ref` and `energy_type`; the barrier's `definition` states the convention); `barriers_unavailable` names each TS entry with none and says why (`no_energies`, `no_reactants`, `no_level_with_all_reactant_energies`); `delta_h298_kj_mol` is `null`, counted under `delta_h298_declined_ambiguous_thermo`, when a species has several thermo records, because it would otherwise sum a pick |
| CHEMKIN zip (`POST /scientific/export/chemkin`) | **exactly one** thermo, transport and kinetics record per species or reaction | never picks: takes the records the request names in `thermo_refs`, `transport_refs`, `kinetics_refs` (typically from the select endpoints), or refuses |

When a species or reaction has more than one candidate and the request has not named one, CHEMKIN refuses with
**422 `export_selection_ambiguous`** naming every ambiguous subject and its candidate refs, and says how to resolve it:
call the select endpoint (thermo and kinetics have one; transport has none) or name the record in `thermo_refs` /
`transport_refs` / `kinetics_refs`. Named refs that match no candidate are reported in the same response
(`context.unmatched`; alone they are **422 `export_ref_unmatched`**), so one round trip shows every problem.
`include_transport: false` leaves transport out entirely, so transport candidates cannot make the export refuse.
The ML export never compares absolute energies across methods: a barrier is computed inside one level, and the levels
are listed by label, never ordered by energy.
`collapse` and `selection_policy` are refused on every export. The native export's format is `tckdb.export` version 2 and
the ML export's is `tckdb.ml` version 2.

## Other lists, and where their order is documented

### Detail sub-lists

Lists inside one record's detail read are ordered by what they list, not by review status; they are parts of one record,
not competing records. Ascending id unless a position or role is named:

| List | Order |
|---|---|
| A network's solves, species, reactions, states, channels, kinetics | `id` ascending (species: `role`, then `species_entry_id`) |
| A network's state energy sources | state, then species entry |
| A conformer group's observations | `id` ascending |
| A transition state's entries, validation evidence and energies | `id` ascending |
| A calculation's source calculations (by role) | `id` ascending, then `role` |
| Calculation geometry, constraints, scan coordinates | `input_order` / `output_order`, `constraint_index`, `coordinate_index` |
| Scan, IRC and path-search points | `point_index` ascending |
| Statmech torsions and electronic levels | `torsion_index`, `level_index` |
| A record's review history | `reviewed_at` ascending, nulls last |

### The legacy entity lists

The primitive entity lists (`GET /api/v1/{thermo,kinetics,statmech,transport,species,reactions,conformers,...}` and the
other entity routes under `backend/app/api/routes/`) are administrative listings: **`id` ascending, with no review
filter**, so they include records of every review status. They are not browse reads, take no `profile`, and carry no
`OrderEcho`. Their behaviour is unchanged here; they are handled together with `/lookup` in a later change.

## Selection is a separate operation

The `.../select` endpoints answer "which records can answer my stated question, and what is the situation among
them?". Every one of them reports these three outcomes over the *eligible* records:

- `sole_eligible_candidate`: exactly one record is eligible. It is the only one; that is not a claim that it is best.
- `incomparable_alternatives`: several are eligible and TCKDB does not rank them. They are listed in display order,
  and `result_mode=first` may name the first of them, labelled administrative.
- `no_applicable_candidate`: none is eligible, with the reasons.

The structure and statmech selections have more outcomes, because they also answer questions that are not "which
record": `no_candidates` and `evidence_conflict` (both), `unresolved_comparability` (structure only), `energy_unavailable`,
`recorded_minimum`, `representative_minimum`, `qualified_evidence` and `validated_corpus_minimum` (structure), and
`qualified_models`, `unresolved_applicability`, `missing_required_data`, `unsupported_task` and `unsupported_model`
(statmech). They state what was established and never rank one method above another; their definitions are in
`structure_selection_decision.md` and `statmech_selection_contract.md`.

The `policy` field of a selection request names an *administrative* display order only (`default`, `most_reviewed`,
`latest`). It is never a method claim. The structure selection's administrative key (version 2) is: `review_rank`
ascending, `quality_rank` ascending, `created_at` descending, then the public ordinal descending. `quality_rank` (a
fact about a determination's source calculation: curated, raw, rejected, unknown) is used only to order exact ties among
alternatives or to label an administrative first; it never moves a record across a scientific distinction. It lives in
the select endpoint, which states the policy it applied; it is not a key of any browse read.

## Visibility defaults

- `rejected` and `deprecated` records are hidden by default; a request opts in with `include_rejected` or
  `include_deprecated`. The `curated` read profile never shows them.
- The `exploratory` read profile shows `approved`, `under_review` and `not_reviewed` records.
- The `curated` read profile has a review floor of `approved`: nothing below it is returned, counted or named.

Visibility and ordering are independent: hiding a record never reorders the rest, and ordering never reveals a hidden
one.
