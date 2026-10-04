# Selecting network kinetics for a stated pressure-dependent question

One page for whoever reads pressure-dependent kinetics and needs one rate coefficient (or one coherent set of them)
for one stated gas-phase question, and for whoever deposits them and wants to know when a deposit can take part. It
describes the selection endpoint, what each answer means, and what the endpoint refuses to claim.

In plain terms: a network can hold several solves of its master equation, fitted in different forms, over different
temperatures and pressures, in different baths. Browsing lists them. Selection is a separate, opt-in question: "of the
solves that can answer this exact question, which one should I use, and why?" The answer says how sure TCKDB is, and
says so when it is not. Today the honest answer is usually "these solves answer your question and nothing ranks
them", because no ranking rule has been approved yet.

## What it is, and what it is not

- **Read-only.** Nothing is stored, no curator endorsement is created or changed, and no record is edited.
- **Opt-in.** The network browse, detail and evaluation endpoints are unchanged.
- **One quantity.** A supplied gas-phase rate coefficient of a network, for a stated state partition, boundaries,
  regime, bath, temperature window and pressure window. It never evaluates a rate, never extrapolates, never splices
  channels across solves or temperature windows, and never averages.
- **Not a recommendation.** A curator or a dataset release recommends records; this endpoint applies published,
  versioned rules to what is stored.

## The request

`POST /api/v1/scientific/networks/{network_ref}/kinetics/select`

The path takes a public `net_...` ref. An integer id is refused, and `channel_key` is a body field, never a path
segment. The question is the body, and none of it is defaulted:

| Field | Meaning |
| --- | --- |
| `coefficient_basis` | `kernel` or `composition_effective`. |
| `temperature_min_k`, `temperature_max_k`, `pressure_min_bar`, `pressure_max_bar` | The window asked for. |
| `bath` | A specified collider (one `spe_...` species entry, no fraction) or an explicit mixture whose mole fractions sum to one. Never renormalised. |
| `partition` | How the observable treats the network's states, by composition hash: `retained`, `eliminated`, `lumps`. |
| `scope` | `single_channel` (default), `projected_bundle` or `full_network`. |
| `channel_key`, `observable` | A single-channel question names both. |
| `outputs` | A bundle or full-network question lists each required output: a channel and an observable. |
| `boundaries`, `regime`, `source_composition_hash`, `sink_composition_hash`, `degeneracy_applied` | Further parts of the observable, stated when they matter. |
| `objective` | `physical_accuracy` (default), `model_fidelity` (needs `reference_model_ref`, an `nsolve_...` ref) or `representation_fidelity` (needs `reference_outputs`). |
| `policy` | `method_preferred` (default), `default`, `most_reviewed` or `latest`. |
| `mode` | `all` (default) or `first`. |
| `min_review_status` | An optional floor on top of the read profile's. |

There is no field for a database id, a candidate cap or a bound. A body that names one is refused.

Plus `POST .../kinetics/select/manifest`, which answers the same request with the decision manifest as a download.

## What a solve must say to take part

A solve is assessed against the question using only what it states: its declared target, protocol and validation,
and the stored columns. A missing fact makes it `unresolved`, a form this selector does not evaluate makes it
`unsupported`, and a contradiction makes it `incompatible`; nothing is applicable by default. The competition unit is
one determination (single channel) or one declared product set of one solve (bundle and full network): a bundle is
never assembled from fits that happen to be available.

## The answer

`outcome` is the selection basis, and `basis` says why in one sentence. Report both verbatim.

- `policy_preferred`: a registered, active rule leaves one node with nothing preferred over it.
- `sole_eligible_candidate`: one node answers the question. That is not a comparative accuracy claim.
- `incomparable_alternatives`: several nodes answer it and no rule ranks them. Nothing was scientifically selected.
- `policy_conflict`: opposing rules or a cycle. Nothing is selected.
- `no_applicable_candidate`: nothing answers the question; `determinations` and `bundles` say why, solve by solve.

`selection` names a node, its solve and, for each member, every eligible fitted representation. With `mode=first` an
`incomparable_alternatives` answer may still name a node, labelled `administrative_first`: a review and recency
choice, not a method claim. Alternate fits of one determination are never independent confirmation, and with
`representation_fidelity` they get their own nested fronts that rank no solve.

**Every rule shipped in this release is inactive**, so `method_preferred` ranks nothing today and the response lists
the rules it considered and why each is inactive. See [`network_selection_rules.md`](network_selection_rules.md).

## Bounds, and what a refusal means

The server fixes its bounds: 200 solves, 2000 kinetics parents, 500 channel nodes, 500 bundle nodes, 1000 states,
2000 channels, 200 required outputs, 10000 evidence entries, 100000 numeric cells and an 8 MiB snapshot. Over any of
them the answer is a 422, `network_selection_population_too_large` or `network_selection_snapshot_too_large`, with the
bound and the visible count, and nothing is assessed: a winner picked from part of a population would say nothing
about the rest. Only solves visible to the caller's read profile are counted.

## Visibility and the manifest

The read is one read-only REPEATABLE READ snapshot, opened before the network ref is resolved. Under the `curated`
profile a solve below the review floor is neither listed nor counted (`excluded_by_review_withheld` is true), whether
or not any exist. The manifest holds the normalised request, every captured solve with its normalised facts, every
assessment, the rules consulted and each comparison, as public refs only, with a SHA-256 digest. It can be replayed
with no database at two levels: the assessments are recomputed from the captured facts, then the decision from those
assessments, and both are compared with what the manifest records.

The digest is a checksum, **not a signature**: anyone can recompute it after editing a document. Replay re-derives the
reasoning from the captured inputs and checks that the document agrees with itself (including that its review basis, read
profile and captured solves are consistent), but it does not authenticate where those inputs came from. Only the export
endpoint compares them with what the server holds.

## Exporting a selection

`POST /api/v1/scientific/networks/{network_ref}/kinetics/export-selected` serialises one selection, after checking it.
You send the manifest you saved from `.../select/manifest` exactly as downloaded, the node you chose (`node_ref`) and
`representation_refs`: exactly one eligible fitted representation for every member of that node. `format` is `native`
(default) or `chemkin`. You send no rule, no verdict the server should believe and no database id.

The server does not trust the manifest's digest. It requires the manifest complete and for this network, replays it from
its own captured inputs, then re-runs the selection against its own content and your authorised population in one
snapshot and requires everything scientific to match. Each refusal is a 422, nothing is exported, and the code says why:

| Code | Meaning |
| --- | --- |
| `network_export_manifest_invalid` | incomplete, another network's, too large, or it does not replay (edited, or a forgery that was resealed) |
| `network_export_manifest_stale` | the content, review states, rules, population or read profile no longer match: run a fresh selection |
| `network_export_choice_not_allowed` | not the selected node; an unranked alternative without `allow_administrative_choice` (false by default); or a selection that chose nothing |
| `network_export_representation_choice_invalid` | not exactly one eligible fit per member, or a fit that is not this node's |
| `network_export_unsupported_form` | a form or species with no CHEMKIN serialisation, or two chosen channels with one equation |

`allow_administrative_choice` permits exporting one of several unranked leading alternatives, and the response then says
`administrative: true`: a review and recency choice, not a method claim. It never bypasses a conflict, an empty
selection, an incomplete membership or an unsupported form.

Native output keeps every form with its solve, determination and representation refs, the channel's directed endpoints
(with species), units and the stored numbers. CHEMKIN output is **forward-only** (`=>`): no reverse coefficient is
derived from reversibility, and no thermodynamics are written, so there is no reverse or equilibrium assumption; both are
listed in `assumptions`. `DUPLICATE` is never written: a collision between two chosen channels is refused (CHEMKIN) or
reported (`equation_collisions`, native), never added. Replaying a historical decision as it was is a separate, deferred
operation.

## From the client and the MCP

`TCKDBClient.select_network_kinetics`, `get_network_kinetics_selection_manifest` and `export_selected_network_kinetics`,
and the MCP tools `tckdb_select_network_kinetics` and `tckdb_export_selected_network_kinetics`, send exactly the fields you give them, drop `None` values and quote the ref into the
path. None of them caps or pages the population.
