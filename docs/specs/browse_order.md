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
| Analytics reads | `review_rank`, `created_at`, `id` | The same order as the other record lists. |

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
- **Not stable under new deposits, by offset.** An offset page is a slice of the live ordering. A new deposit above your
  position repeats a record on the next page, and a record leaving the visible set above it skips one. Every list also
  takes a cursor, which is stable against both (see "Paging" below).

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

### Provenance filters: ONE calculation matches all of them

A record rests on source calculations, and a provenance filter restricts by what one of them recorded. Every route that
offers these filters reads them the same way:

- **One calculation, every field.** The record is kept when at least one source calculation it links to matches **every**
  supplied provenance field at once (AND across the fields, OR across the record's calculations). A record whose
  calculation A ran at level X in software P and whose calculation B ran at level Y in software Q is not matched by level
  X with software Q: no single calculation did both.
- **Only linked calculations.** A record matches through the calculations it actually links to, never through a sibling's,
  its species entry's or the level's other uses (no borrowed provenance). A record that links no calculation matches no
  provenance filter, an `unclassified` family filter included.
- **Below the read profile's floor.** Under `curated` a calculation below the floor contributes nothing to a match, so a
  filter on `method=b3lyp` cannot keep a record because of a calculation the reader cannot open. (ADR 0023 step 4 will change
  how linked evidence is matched, in the shared helpers; a rejected or deprecated calculation will still never match.)
- **No primary calculation.** A record's displayed `primary_calculation` is a display choice and decides no filter.

The provenance fields are `level_of_theory_ref` / `level_of_theory_id` (where a route offers it), `method`, `basis`,
`software`, `software_version`, `workflow_tool`, `workflow_tool_version`, `method_family`, `dft_rung_min` and `dft_rung_max`.
The thermo and kinetics reads (per entry and search) offer all of them; the transition-state search and browse and the species
browse offer `method`, `basis`, the software and workflow-tool fields and the family and rung fields; the calculation search
and the species-calculation search offer the family and rung fields beside `method` and `basis`; the level-of-theory
search and browse offer them too. Until 2026-10-25 the thermo and kinetics
reads and selects matched level of theory and software through *any* calculation each, independently; that reading is gone,
and a select decision recorded under it (manifest format 3) is refused on replay.

`scientific_origin` (`computed`, `experimental`, `estimated`) is a different kind of filter: it is matched on the record's own
column, not through a calculation. Thermo, kinetics and statmech search, the per-entry thermo and kinetics reads, and the
thermo, kinetics and statmech selects offer it.

### Method family and DFT rung: a classification, not an accuracy measure

A level of theory's `method` is classified into a **family** (`dft`, `wavefunction`, `composite`, `multireference`,
`semiempirical`, `force_field`, `unclassified`) and, for DFT, a **rung** from 1 to 5 (LDA 1, GGA 2, meta-GGA 3, hybrid 4,
double hybrid 5). Level-of-theory reads carry `method_family`, `dft_rung` (null unless `dft`) and `method_classification`
(`{name: "method_family_classification", version}`, the registered, versioned table that produced them).

- **It says what kind of method a name is. It does not say how good the method is.** The ladder covers density functionals
  only, so a coupled-cluster or composite method has no rung and sits on no scale. Which method is more accurate depends on
  the property and the system; a rung-5 functional is not thereby better than a rung-4 one for a given quantity.
- **It is a filter fact and nothing else.** It is never a sort key, a tie-breaker, a select default, a "best" label or part of
  any order echo. A guard test scans for it.
- **It is derived, not stored.** The table is code (`app/chemistry/method_families.py`) applied to the method's identity key
  (case, whitespace and the curated aliases), so no column holds a class and changing the table needs no migration; it is a
  new version of `method_family_classification`.
- **Spin prefixes.** A leading `u`, `r` or `ro` is read through only when what remains is a classified method and the whole
  name is not a table row of its own (`UM062X`, `uccsd`; `rm1` and `rpbe` are rows).
- **A dispersion suffix or a basis never changes the class.** `-D2`, `-D3`, `-D3BJ`, `-D3(BJ)`, `-D4` and the like are stripped
  before the lookup (a bare `-D` is not: `wB97X-D`, `B97-D`, `B3LYP-D` are rows). A row that itself ends in a suffix wins over its stem: `B97` (Becke 1997) is a
  hybrid, rung 4, but `B97-D`, `B97-D3`, `B97-D3BJ` are Grimme's GGA, rung 2. A name the table does not hold is
  `unclassified`; nothing is guessed from a substring.
- **Filters.** `method_family` takes one family or a list (any of them matches). `dft_rung_min` / `dft_rung_max` are inclusive
  and imply `method_family=dft`: with a bound, only a DFT method can match, so a bound beside a family list without `dft`
  matches nothing, and a lower bound above the upper matches nothing.

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

> **Rule change in progress (owner decision 2026-10-25, [ADR 0023](../adr/0023-curated-profile-floor-applies-to-records-linked-evidence-is-badged.md)).**
> Under the new rule, the `curated` floor decides which *records* a list, search or select returns; a returned record's linked
> evidence (source calculations, levels derived from them, conformer observations in a group, evidence counts, nested refs) is
> shown with its review badge, and only `rejected` and `deprecated` evidence is withheld.
>
> **Already true (step 2):** a nested ref in a response, in a selection document or in an export is withheld only when the record
> it names is `rejected` or `deprecated`; a ref to a not-reviewed or under-review record stays, with its badge where the model has
> one. The legacy entity reads follow the same split for their pointer columns and embedded records (a conformer group's
> observations), and an id filter is rewritten to name nothing only when it names a withheld record. A returned record that is
> itself below the floor still fails loud.
>
> **Badges.** Under `curated` every scientific response carries `review_status_by_ref`: the review status of each gated ref it
> names (null under the default profile). The export streams carry the same map on each line; a selection manifest does not (it
> is hashed and replayed) and records each candidate's review status instead. The legacy entity reads carry raw ids and have no
> badge.
>
> **Already true (step 3):** the counts, flags and derived levels of a returned record follow the same split. A calculation that is
> `rejected` or `deprecated` is not counted, sets no flag and supplies no level; an unreviewed or under-review one is counted and
> supplies its level, as the response names it. A level only a withheld calculation supplied is null together with its source,
> and no other calculation is put in its place. This covers transition-state, conformer, network, statmech, thermo and kinetics
> summaries, the per-type level maps, the analytics provenance flags and the CHEMKIN transport linearity witness. A record that is
> itself a record in the list (a transition-state entry, a network solve) still has to clear the floor to be counted.
>
> **Known state, not moved by step 5.** Usage lists on reference rows (what uses a level of theory, a scale factor or a
> correction scheme) are unchanged. The level-of-theory `used_by`,
> `evidence_summary.calculation_usage_count` and the usage-eligibility filter of the level-of-theory search stay on the record
> floor together: they preview the calculation record list, so a level used only by calculations below `approved` is not listed
> and its count says so. A species entry's availability `calculation_count` previews the same list and also keeps the floor. The sweep lists every route where this
> happens (`STILL_ON_THE_EARLIER_RULE`), and the list can only shrink. The export and relative-energy routes stay in that list
> only for the RECORDS they list or compare, and for the ML export's energy rows, a named exception (owner decision Q7: the
> calculations that are its rows keep the approved floor).
>
> **Already true (step 5):** the trust checklists and the selection loaders (structure, statmech, network, relative energies, and
> the source-finding gate of thermo and kinetics select) read a returned record's linked evidence by the evidence rule: an
> unreviewed calculation, declared member or conformer observation is used, a `rejected` or `deprecated` one is absent. The record
> they return or compare (a candidate, a compared conformer group, the owning transition-state entry) still has to clear the floor.
>
> **Already true (step 6):** made-under comparisons are profile-free. A stored stamp is compared with a recomputation from the full set of
> inputs, so a route that asks for `check_inputs` answers the same under `curated` and `exploratory`.
>
> **Still the earlier rule (decision D6: below the floor is hidden, evidence included), and described as it is:** detail-by-ref (step 7). Matching in search and select (step 4)
> follows the evidence rule: see the filter paragraph below. Every passage below that describes what `curated` hides or shows for
> those is therefore the earlier rule.

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
| A thermo record's temperature points and NASA-9 intervals | `temperature_k`, `interval_index` ascending |
| A kinetics record's PLOG pressure entries | `entry_index` ascending |
| A calculation's energy components, composite terms, frequency modes | `component`, `term_position`, `mode_index` ascending |
| A calculation's applied energy corrections, submission links | `id` ascending, `submission_id` ascending |
| A literature record's authors | `author_order` ascending |
| A workflow tool's releases (inside `GET /workflow-tools/{id}`) | `release_date` descending, nulls last, then `id` descending |
| A reaction entry's `/full` provenance: calculations, networks | calculations newest deposited first then `id` descending, networks `id` ascending |
| A statmech or transition-state entry's source calculations | `id` ascending (then `role`) |

### The legacy entity lists

The primitive entity lists (`GET /api/v1/{thermo,kinetics,statmech,transport,calculations,species,reactions,transition-states,conformer-groups,conformer-observations,networks,...}`
and the other entity routes under `backend/app/api/routes/`) are the **raw entity layer**: administrative listings in
**`id` ascending**, not browse reads. They sit beside the scientific reads, and they obey the same visibility rules:

- **Order.** `id` ascending, stable. Each list says so in `order` (a structured `OrderEcho` whose one key, `id`, is described as an
  administrative listing order, with a statement that it is not the browse display order and not a scientific preference). `order` is
  always `id` ascending whatever the review status or deposition time say; use the scientific reads to browse.
- **Default visibility.** Rejected and deprecated records are hidden unless `include_rejected` / `include_deprecated` is set. Applied
  energy corrections (`/applied-energy-corrections`) are reviewable records and follow the same rules. Species and reactions (identity
  rows), geometries, levels of theory, software, literature, workflow tools and the energy-correction *reference parameters* (schemes and
  frequency scale factors) have no review state of their own and show every row. Species entries and reaction entries have one too, but they are
  **scoping parents: never gated on detail, sub-routes, filters or pointers; entry listings follow the scientific species-search
  visibility.** `/species-entries/{id}` and `/reaction-entries/{id}` answer 200 for an unreviewed or rejected entry under every profile, the
  routes under an entry and the `species_entry_id` / `reaction_entry_id` filters and pointers work for it, and an approved record on an
  unreviewed entry shows under `curated`. Only the entry *lists* in `/lookup/*` filter (default exclusions and the floor), as the
  scientific species search does, with the disclosure below; the records of the lookup (calculations, kinetics) are searched on every entry,
  listed or not. A record whose entry the request does not list says so: `entry_listed: false` on the result (and on the
  `selected_by` group), and a match detail code `species_entry_unlisted` / `reaction_entry_unlisted` whose detail names the entry ids
  (entries are ungated, so naming them reveals nothing). The scientific thermo, kinetics and species-calculation searches discover their
  entries without the entry visibility for the same reason: a record is never dropped because its entry is unreviewed or rejected,
  under the floor or the default exclusions. Only the species search's *entry listing* follows the entry visibility.
- **Read profile.** Every legacy read takes `?profile=` (and refuses `release`), resolved by the dependency the scientific reads use.
  `curated` raises the floor to `approved` (and overrides the opt-ins): a record below it is absent from the list and from its `total`,
  and its detail route (and every route under it, e.g. `/calculations/{id}/...`) answers exactly as an id that names nothing. A visible
  record does not name a record the profile hides, of any gated kind: a pointer column (`statmech_id`, `conformer_observation_id`,
  `target_conformer_group_id`, `transition_state_entry_id`, ...) is blanked, a link or edge element that carries one is dropped, and a
  record embedded in another response (a transition state's `entries`, a group's `observations`) is shown only if the request shows it.
  Which columns count is a registry (`POINTER_KINDS` in `app/api/routes/_legacy_reads.py`), not a naming rule; a registry test fails for
  an id column in a returned model that is in neither it nor the list of ungated columns with their reasons. A child is hidden with its
  parent (an observation in a hidden conformer group; an entry of a hidden transition state), and an id-typed query filter that names a
  hidden record answers as an id that names nothing. In `hidden_by_default`, a child is counted under every excluding status that applies to it, its
  own and its parent's (once per status). Each count is therefore the number of matching records that exclusion hides, not the number a
  single switch restores: a child that is itself deprecated in a rejected group is in both counts and needs both switches. A path id is judged as the id FastAPI bound, so `+5`, `5.0` and ` 5` are the same
  id as `5`. The lists under a species entry or reaction entry (thermo, statmech, transport, conformers, kinetics, transition states) and a
  network's solves apply the same rules; the entry summary counts only what the curated list would show. Write routes take no profile.
- **Disclosure.** A list under the exploratory profile reports `hidden_by_default` (see below). A bare-array sub-list has no envelope, so it
  reports the counts in the `X-TCKDB-Hidden-By-Default` header (`deprecated=0; rejected=1`). The header carries counts only, one per
  default exclusion; what is counted is per route: the records the array lists (thermo, statmech, transport, kinetics, transition states,
  observations), conformer groups on `/conformer-groups`, network solves on `/networks/{n}/solves` and on a channel's kinetics (a
  kinetics row is reviewed through its solve). Under `curated` there is neither. Counts inside a record (a species entry's conformer
  summary, a network's `solve_count`, a group's `observation_count`) follow the same visibility as the list they summarise.

The legacy payloads still carry raw integer foreign keys to species and reactions and to species and reaction entries when they are
the scope of a record (they are scoping parents, as on the scientific reads). `GET /species-entries/{id}/conformer-observations/lowest-sp`
is removed: it was a silent pick (a `limit(1)` tie-break by `created_at` and `id`) and replaced by
`POST /scientific/species-entries/{ref}/conformers/relative-energies`.

### `/lookup`

`GET /api/v1/lookup/*` (species, calculations, thermo, species-calculation, reaction, kinetics, reaction-kinetics, geometry, statmech,
transport, network) is public and obeys the scientific reads' rules:

- **Visibility and profile.** Same defaults, same `include_rejected` / `include_deprecated`, same `?profile=`. Counts inside an identity result
  (`calculation_count`, `thermo_count`, `kinetics_count`, `calculation_output_count`) count the records the request shows.
- **Order.** Records are listed in the display order (`review_rank`, `created_at` descending, `id` descending), echoed in `order`. Identity rows
  (species, species entries, reactions, reaction entries) come first and are ordered the same way. Species and reaction rows are identity
  and always listed; the **entry listing is filtered** by the request's visibility (as the scientific species search does; the entries stay
  reachable by id and the records on them are never withheld), and what the default hid is
  reported in `entries_hidden_by_default` (`"counted": "species_entries"` or `"reaction_entries"`) on every lookup that lists entries,
  whether or not it also lists records. `hidden_by_default` always means the listed *records*; one name per meaning, so a client does not
  have to know which endpoint it called to read the field. `/lookup/species-calculation` searches **every** entry of the species (it used to take the first by id).
- **`selection=lowest_energy|latest|earliest`** is an explicit request to narrow a list, and the response says what it did in `selected_by`
  (the mode, its criterion, and per species entry how many candidates there were, how many were chosen, and whether they tied). It is not a
  scientific preference. `lowest_energy` compares recorded electronic energies and is refused with **422 `lowest_energy_across_levels`**, naming
  the levels found, when the candidates that have an energy span more than one level of theory (filter to one level, or use a select endpoint);
  with no recorded energy among them it is 422 `lowest_energy_unavailable`. An exact tie returns every tied record with `tied: true`; none is
  picked by id. A mode is applied within each species entry, since entries are different structures. A record the profile hides never takes part,
  and `selected_by.groups[].not_compared` counts the `lowest_energy` candidates that had no recorded energy and so were not compared. Levels
  are compared as the rows they stand for (a level merged into another is that other level).
- `hidden_by_default` is reported for the record lists (calculations, thermo, kinetics, statmech, transport, network).

## Hidden by default: what the default visibility hid

A list that applies the default visibility says how many records it hid, or "no such record" cannot be told from "hidden by default".
The block sits in `request.hidden_by_default` on the scientific lists, and at the top level of the legacy and lookup envelopes:

```json
{"counts": {"rejected": 1, "deprecated": 1},
 "include_with": {"rejected": "include_rejected=true", "deprecated": "include_deprecated=true"},
 "counted": "records"}
```

- It has one key per exclusion the request left at its default, counted over the records that matched the other filters. An exclusion the
  request lifted is absent; with both lifted (or nothing hidden) the block is `null`. A status a `min_review_status` removes anyway is a
  filter's doing and is not counted. A species list hides *entries* (`"counted": "species_entries"`), since a species has no review state.
- **Exploratory only.** Under `curated` the block is always `null` and no count, total or summary differs in a way that reveals a record below
  the floor (decision D6: hidden == unknown == absent); the response says only that the floor applies (`profile_recommendation:
  approved_floor_only`).
- **Cost.** One aggregate statement per list (none when nothing is excluded by default, none under curated). Lists that already hold the
  candidates' review statuses in memory count them with no extra statement; structure search takes it from the aggregate it already ran.

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

### What a select call does, in order

1. **User filters.** The caller narrows the candidates by facts, with the vocabulary the matching browse or search endpoint
   already offers, and with the same matching rules (not the same code: select reimplements the joins in
   `selection_filters.py`). A filter the search does not offer is a 422, never ignored. A filter
   excludes on a fact and ranks nothing.
2. **Requested eligibility.** Each exclusion a selection applies is a *named default* the request can lift. Every one is
   echoed in the answer (`eligibility[]`: `name`, `applied`, and `source` of `default`, `request` or `read_profile`, plus how
   many candidates it kept out and how many compete only because it was lifted). A candidate that competes only because of an
   override says which default in `passed_only_by_override`; the finding or failure it passed stays disclosed on the candidate.
3. **The policy.** There are no built-in method rankings. With no rule, the outcomes stay `sole_eligible_candidate`,
   `incomparable_alternatives` and `no_applicable_candidate`, and `result_mode=first` stays labelled administrative.
4. **The result.** One record, or an explanation that several remain.

The answer also carries `filters` (the normalised filters and how many candidates they removed; a narrower `permitted_quality`,
`member_refs` or a `model_revisions` population is listed there too, and `note` says so when the filters removed everything) and
`funnel` (`considered`, `removed_by_filters`, `removed_by_floor` for the caller's own `min_review_status`, `removed_by_eligibility`,
`remaining`; `unit` says what is counted, solves for a network). Each candidate is counted once, at the first step that removed it,
so those four add up to `considered`. Filters apply before eligibility, so a candidate a filter removed is not counted as an
eligibility exclusion. The per-default `excluded` counts are different: a candidate that fails several defaults is counted
under each of them, so they can sum to more than `removed_by_eligibility`.

| Default (stable name) | What it excludes | Lifted by | Offered by |
|---|---|---|---|
| `rejected_review_status` | a record whose review status is `rejected` | `eligibility.include_rejected` | every select |
| `deprecated_review_status` | a record whose review status is `deprecated` | `eligibility.include_deprecated` | every select |
| `evidence_hard_fail` | a computed record whose evidence rubric hard-fails | `eligibility.include_hard_failed_evidence` | thermo, kinetics |
| `confirmed_blocking_findings` | a record a live, confirmed structure finding invalidates through a source | `eligibility.include_confirmed_findings` | thermo, kinetics, statmech, structure |
| `rejected_calculation_quality` | a calculation whose quality is `rejected`, or a determination resting on one | list `rejected` in `permitted_quality` | every structure grain |
| `entry_review_status` | a determination or candidate whose **transition-state** entry is rejected, deprecated or below the floor | not liftable | structure TS evidence, statmech TS target |

Filter vocabulary by route: thermo and kinetics `model_kind`, `scientific_origin`, `level_of_theory_ref`, `method`, `basis`,
`software`, `software_version`, `workflow_tool`, `workflow_tool_version`, `method_family`, `dft_rung_min`, `dft_rung_max` (ONE
shown source calculation matches every provenance field supplied); network and statmech `model_kind` plus `method`, `basis`,
`software`, `software_version`, `workflow_tool`, `workflow_tool_version` (one source calculation matches all supplied fields),
and statmech `scientific_origin` too; the structure selections the six provenance fields (the calculation itself, or the
calculations a determination pins as sources). The family and rung fields are offered where the matching search offers them.
**A filter on a linked record matches through linked evidence unless the evidence rule withholds it (ADR 0023, step 4).** This holds
on every search, list and select route, not only on select. Under `curated`, the approved record floor decides which records a
search returns; the calculations and conformer observations they link to are evidence, and a rejected or deprecated one
contributes nothing to a match: not its level of theory, method, basis, software, workflow tool or `scientific_origin`; not a
`has_*` evidence flag (`has_source_calculations`, `has_freq_calculation`, `has_rotor_scans`, `has_freq`, `has_observations`, ...);
not a `dependency_role` edge to it. A not-reviewed or under-review one matches, as it is shown, with its badge. A record whose only
level-B link is rejected is not found by asking for level B, and `total` and `hidden_by_default` do not change because such a link
exists. Observations inside a conformer group are evidence; transition-state entries, basins in structure select and network
solves are records and stay on the record floor; species and reaction entries never gate. The provenance match is one helper
(`apply_calculation_provenance_filter`) fed a source set already narrowed by `linked_evidence_predicate`; select does the same
through `selection_filters`. A record-level `*_ref` filter that names a parent (`species_entry_ref`) is a scope, not a subject, and is unchanged; a ref that names linked
evidence (`parent_calculation_ref`, `child_calculation_ref`, `conformer_observation_ref`) is evidence, and matches nothing when it names a
rejected or deprecated record. Under exploratory
nothing is withheld as evidence: a rejected calculation still takes part in a match, because `include_rejected` and
`include_deprecated` govern the records a route returns, not the evidence behind them, and a default exclusion is disclosed by
`hidden_by_default`. That asymmetry is by design (the ADR records it).

**The read profile's floor is not a default.** Under `curated` the floor is `approved` and hidden equals unknown equals
absent. No select request can lift it: `include_rejected` and `include_deprecated` lift nothing under it (the echo says
`source: read_profile`), and nothing below the floor is counted or named. An override can only lift an exclusion within what
the read profile already shows. What a lifted default does change under `curated` is limited to approved records, such as an
approved record whose evidence rubric hard-fails.

**Species and reaction entries are scoping parents.** Their review status never excludes a record or hides what is under
them, under any profile. A transition-state entry, a conformer group and the other gated kinds still hide with their parent (a basin whose conformer
observation sits in a group below the profile hides with the group; in `exploratory` a rejected or deprecated group is
judged by the review defaults like the observation)
under `curated`. A decision records its filters and eligibility settings, so replay reproduces it; a manifest in an earlier
format is refused, not read as the defaults.

## Visibility defaults

- `rejected` and `deprecated` records are hidden by default; a request opts in with `include_rejected` or
  `include_deprecated`. The `curated` read profile never shows them.
- The `exploratory` read profile shows `approved`, `under_review` and `not_reviewed` records.
- The `curated` read profile has a review floor of `approved`: nothing below it is returned, counted or named.

Visibility and ordering are independent: hiding a record never reorders the rest, and ordering never reveals a hidden
one.
Every list that applies these defaults reports what they hid (`hidden_by_default`, above), under the exploratory profile only.

## Paging: offset is random access, the cursor is the stable traversal

Every browse and list read takes two ways to move through its order, and they answer different questions.

- **`offset` is random access** ("page N of M"). It is deterministic only for a static corpus. An upload that sorts above the
  reader's position repeats a record on the next page; a record that leaves the visible set (a deprecation, say) makes the
  reader skip one. Neither is visible in the response.
- **`next_cursor` is the stable traversal.** Every list response carries `next_cursor` (null on the last page) and `watermark`.
  Pass `next_cursor` back as `cursor` for the following page. The cursor holds the sort key of the last record delivered, so
  the next page asks for records strictly after it in the order above; a record uploaded above the reader changes nothing.
  It also holds a snapshot bound (the highest record id the request was allowed to see when the traversal began), so uploads
  that arrive mid-traversal are outside it. `pagination.mode` says which contract served a page (`offset` or `cursor`).

Rules of the cursor:

- It is bound to its endpoint, its filters and the read profile. Replayed against another endpoint, or with different
  filters or profile, it is a 422 `cursor_query_mismatch`. Only `limit` and `include` (and, on a point series,
  `include_geometries`) may change between pages.
- A malformed, altered, forged, old-version or other-key cursor is a 422 `invalid_cursor`. The version is compared for
  exact equality. Every value in it is checked against the sort before it is used, so a forged cursor is never a 500.
- `cursor` and a non-zero `offset` cannot be combined: 422 `cursor_offset_conflict`.
- It is opaque: encrypted and authenticated under the server's `PAGING_CURSOR_KEY`, so it holds no readable id, sort
  value or snapshot bound (the internal-id policy keeps those out of every response). Rotating the key invalidates
  cursors in flight: clients get 422 `invalid_cursor` and restart. Nothing stored depends on the key.
- It never names a record the read profile hides. It holds the key of a record already delivered, and its snapshot bound
  is the highest id among records the request may see (under `curated`, the highest approved id, not the table's).

What the cursor guarantees: **insertions are bounded.** A record uploaded after the traversal began is not delivered, and
cannot shift the reader or change `total`.

What it does not guarantee: **review state is not frozen.** `review_rank` leads the sort key, so a review change can move
a record across the reader's position, and then:

- approving a record the reader has not reached moves it ahead of the reader: it is **skipped**;
- moving a delivered record from `approved` to `under_review` moves it behind the reader: it is delivered **again**;
- a record deprecated or rejected before it is reached drops out, which is the intended outcome;
- `species/search` keeps a species whose entries are all hidden and ranks it by its visible entries, so deprecating an
  entry moves the species to the end rather than removing it: a species already delivered is delivered again.

Nested and derived facts are bounded by the parent's id only. `species/browse` bounds species by `species.id`, so a new
approved entry under an old, not-yet-reached species changes that species' rank and the traversal can skip it. A list
that is eligible through child rows (a level of theory, a frequency scale factor or a correction scheme is listed once a
calculation uses it) is bounded by its own id, not by the usage.

So "every record exactly once" holds only while review state and eligibility do not change during the traversal. For an
immutable, citable set, read a published dataset release's frozen `selected_records` artifact; live paging is
reproducible against insertions, not immutable.

Not paged by cursor, with the reason: the two NDJSON exports (a stream with no envelope), and the authenticated operations
lists (admin users and curator tasks, submissions for review and mine, a submission's audit events and record links).
