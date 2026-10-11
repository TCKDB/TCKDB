# Selecting a statistical-mechanics model for a stated use

One page for whoever reads the statmech records of one species entry or one transition state entry and needs to know
which supplied model supports a stated use, and why. It describes the one selection endpoint, what each answer means,
and what it refuses to claim.

In plain terms: a statmech record says how a molecule's rotation, vibration, symmetry and electronic levels were
treated. Several records can exist for one entry, made differently, and some say more than others. Selection is a
separate, opt-in question: "of the models that can answer this exact question, which ones do, which cannot be judged
because something is not stated, and does any audited rule prefer one?" It returns **no number**: no entropy, heat
capacity, partition function, rate or population is computed. The honest answer is often "these models qualify and
nothing ranks them", and the endpoint says so instead of picking one.

## What it is, and what it is not

- **Read-only.** Nothing is stored and nothing is edited. Browse orders are unchanged.
- **Opt-in.** The statmech search and detail reads keep their order. A selection is a separate `POST`.
- **Not a recommendation.** A curator or a dataset release recommends records; selection applies stated rules to what is
  stored and shows its working.
- **Not a physical-search certificate.** Coverage is the population you are allowed to see.
- **No hidden rules.** This release ships **no active preference rule**. Qualification is the whole of what an
  unmodified server returns; a preference is possible only once an audited rule is activated.
- **Unknown is never unity.** A fact a model does not state (a scale factor's application, an energy zero, a counted
  symmetry factor) is reported as missing; nothing is filled in.

## The endpoint

`POST /api/v1/scientific/statmech/select` answers the request below; `POST /api/v1/scientific/statmech/select/manifest`
answers the same request with the replayable decision manifest. One typed target covers a species entry (`spe_`) or a
transition state *entry* (`tse_`); a basin, determination, ensemble declaration or candidate is named by its public ref.
No integer id, bound, page, sort or rule is accepted; a misspelled token is a 422.

```json
{
  "intent": "qualify",
  "task": "within_basin_thermal",
  "observable": "entropy",
  "target": {
    "kind": "species_entry",
    "species_entry_ref": "spe_aaaaaaaaaaaaaaaaaaaaaaaaaa",
    "scope": {"kind": "basin", "conformer_group_ref": "cg_aaaaaaaaaaaaaaaaaaaaaaaaaa"},
    "isotope_convention": "entry_documented_masses",
    "electronic_scope": "entry_state",
    "stereochemical_scope": "entry_identity"
  },
  "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}}
}
```

`intent` is `qualify` (assessments, nothing selected) or `protocol_preferred` (also compares whole applicable models, and
needs a `comparison_objective`). The tasks are `rotational_component`, `vibrational_component`, `zpe_component`,
`within_basin_thermal`, `canonical_model` and `conventional_ts_transverse_model`; a recognised deferred task
(for example `numerical_evaluation`) is a valid request answered `unsupported_task`.

## Reading the answer

- **`outcome`** and **`basis`** are the answer; quote them verbatim. A valid negative outcome is HTTP 200: nothing
  qualifies, a conflict, an unresolved comparability.
- **`selection`** holds at most one of `scientific_ref` (supported dominance over every eligible alternative),
  `sole_eligible_ref` (a lone eligible model; noncomparative) and `administrative_ref` (only with `result_mode: first`,
  among incomparable alternatives; an administrative order, not a scientific one). Each carries its basis and its
  population limit. `qualify` and every unresolved outcome carry none.
- **`candidates`** lists every assessed candidate by public ref: `smd_` for a declared model, `sm_` for a legacy record
  read as it was stored. Each has `applicability`, `reasons`, the `recipe` it states, the `evidence` roles it rests on, and
  its **`advisory`** list.
- **`advisories` and `notices`** disclose what does not exclude a candidate but should not pass silently:
  `imported_parameters_not_revalidated:<role>` (an imported literature source stands for that role without a local job;
  the stationary-point character it was computed at is the source's own statement and was not re-judged) and
  `source_literature_unidentified:<role>` (a cited source has no DOI or ISBN, so it is kept but meets no role).
- **`fronts`, `edges`, `conflicts`, `undecided`** are the comparison, over public refs. With no active rule there are no
  edges and one front: the alternatives are unranked, **not equally accurate**.
- **`coverage`** says what the answer is a statement about: the authorized population, with `physical_search` always
  `not_established`.

| Outcome | Meaning |
| --- | --- |
| `qualified_models` | The listed models support the claim (qualify). |
| `sole_eligible_candidate` | One eligible model; not a comparative claim. |
| `policy_preferred` | An audited rule prefers one whole model over every alternative. |
| `incomparable_alternatives` | More than one applicable model and no active rule that ranks them. |
| `unresolved_comparability` | A rule might rank them but a prerequisite is unknown; nothing is selected. |
| `policy_conflict` | Rules oppose each other or form a cycle; nothing is selected. |
| `no_candidates`, `no_applicable_candidate`, `unresolved_applicability`, `missing_required_data`, `unsupported_model`, `evidence_conflict`, `unsupported_task` | Why nothing qualifies. |

With qualified models next to undecided ones, a request for the entire requested domain is not certified: the unresolved
outcome is returned and the qualified subset stays visible.

## Refusals

A population over a bound (500 candidates, 25,000 nested rows, a dependency traversal past depth 8, a 10 MiB manifest) is a
422 `statmech_selection_bound_exceeded`; nothing is chosen from a prefix. A population this release does not evaluate (a
pinned release) is `statmech_selection_unsupported`. An unknown, hidden or foreign target, basin, determination or
candidate is a 404 (`unknown_statmech_candidate_ref` for a candidate); the answer is the same whichever it is.

## The manifest

The manifest carries the request, the read profile, every candidate's facts and source calculation, each assessment and
the decision, with public refs only. Three offline operations recompute it: reproduce the recorded ordering, reassess the
captured inputs, or replay the whole decision; a self-inconsistent manifest or an unsupported version is refused. Digests
are checksums, not signatures; replay shows the reasoning follows from the captured inputs and proves neither that they
are true nor that the population was complete. The download is a new snapshot of the current data, not a saved decision.

## From the client and the agent tool

`TCKDBClient.select_statmech_models(request)` and `get_statmech_selection_manifest(request)` send the request as given,
refuse an integer id, a ref of the wrong kind and any bound, page, sort or rule before a request is made, and return the
server's answer untouched. The MCP tool `tckdb_select_statmech_models` does the same. Neither pages, caps, or chooses a
candidate.
