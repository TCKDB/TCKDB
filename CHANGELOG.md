# Changelog

All notable changes to TCKDB are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Two things are versioned, and they are not the same

**The software** (`tckdb-backend`, `tckdb-client`, `tckdb-schemas`,
`tckdb-mcp`, `tckdb-chemkin`) is versioned per package and described in this
file.

**A curated scientific dataset** is versioned independently as a *dataset
release* — a tag such as `2026.07.0`, an immutable SHA-256-checksummed
manifest, an attributed selection ledger, and its own citation string. Dataset
releases are not listed here; they are discoverable at
`GET /api/v1/scientific/releases` and each carries its own
`changelog_entry`. Cite the dataset release when you use TCKDB's numbers; cite
the software (see [`CITATION.cff`](CITATION.cff)) when you use the code.

Conflating the two is the mistake this split exists to prevent: upgrading the
backend must never change what a published dataset says, and re-curating a
dataset must never require a code release.

## The made-under line (2026-10-24)

An assessment now says which rubric judged it, at which version, and whether that is still the version in force.

- **Wire (additive).** `assessments.deterministic_trust` and `assessments.reproducibility` carry `made_under`: `rubric`, `assessed_version`, `current_rubric`,
  `current_version`, `state` (`current`, `stale`, `unassessed`), `stale_reason` (`rubric_version`, or `inputs_changed` when the version is unchanged but the
  inputs the assessment was computed from differ now) and `inputs_checked`. Version 0 means "stamped before the 2026-10 versioning reset" and is never
  current. `unassessed` is not `stale`. A response holding more than one record compares only the rubric name and version (`inputs_checked: false`), because
  re-evaluating every stored assessment's inputs is tens of statements per record. The admin curator-task reads carry `made_under` for the latest stored
  machine review of the record. `tckdb-client` 0.165.0 types it.
- **Website.** One shared line (`MadeUnderLine`) with a Stale badge and a plain explanation ("It has not been re-assessed under the current version"; nothing
  schedules a re-assessment). On the transition-state entry page (under the Trust fact), on each thermo record (the reproducibility assessment, requested with
  `include=assessments`) and in the Machine findings queue.

## Lookup and the legacy entity lists obey the visibility rules (2026-10-24)

`/lookup/*` and the raw entity lists (`/thermo`, `/kinetics`, `/calculations`, ...) used to ignore every visibility rule the scientific reads
obey: no review filter, no read profile, id order. They now follow `docs/specs/browse_order.md`.

- **Visibility and profile.** Rejected and deprecated records are hidden unless `include_rejected` / `include_deprecated` is set. Every
  legacy and lookup read takes `?profile=`; `curated` withholds records below the `approved` floor from lists, totals, counts and detail
  routes (a hidden record answers as an unknown id), and drops mentions of hidden calculations from the records it does show. A curated
  reader could previously reach below-floor records through the legacy lists: that is closed.
- **Order.** Lookup lists use the display order (`order` echoes it). Legacy lists stay `id` ascending and now say so (`order`, described as
  an administrative listing order).
- **Selection modes.** `/lookup` `selection=lowest_energy|latest|earliest` labels what it did (`selected_by`); `lowest_energy` across more
  than one level of theory is refused (422 `lowest_energy_across_levels`) instead of warning; an exact tie returns every tied record;
  `/lookup/species-calculation` searches every entry instead of the first by id.
- **Disclosure.** Lists report what the default visibility hid (`hidden_by_default`: counts and how to include them), under the exploratory
  profile only; under `curated` nothing about records below the floor is counted or named.
- **Closed after review.** A path id is judged as the id FastAPI bound (`+5`, `5.0`, ` 5` were not gated). A visible record no longer
  names a hidden record of any gated kind (registry of pointer columns, not a name suffix); embedded records, children of a hidden parent
  and id-typed filters follow the same rule. Applied energy corrections and species/reaction entries obey the default visibility and the
  floor. `GET /species-entries/{id}/conformer-observations/lowest-sp` is removed (a silent pick that named hidden records).
  `/workflow-tools` and `/workflow-tool-releases` list by `id`, as their echo says.
- **Parent rule.** A child of a hidden parent (an observation in a hidden conformer group, an entry of a hidden transition state) is hidden
  by the detail gate, by pointers at it, by id filters naming it and by embedded copies, under the request's visibility (one registry,
  `PARENT_OF`). Lookups report hidden entries in `entries_hidden_by_default` everywhere; `hidden_by_default` is always about records.
- **Entries never withhold records.** The scientific thermo, kinetics and species-calculation searches no longer drop a record because its
  species or reaction entry is unreviewed or rejected (floor and default exclusions alike). `/lookup/species-calculation` and
  `/lookup/reaction-kinetics` mark records on entries they do not list (`entry_listed: false`, `species_entry_unlisted` /
  `reaction_entry_unlisted`). The hidden-by-default count includes children hidden through a rejected parent.
- Packages: `tckdb-schemas` 0.133.0 and `tckdb-client` 0.164.0 (a new refusal code; the removed route). No schema or migration change.

## Browse is a browse: no evidence keys, no silent pick (2026-10-23)

Browse, filter and select are three operations, and a browse read now does only the first (`docs/specs/browse_order.md`).

- **One order.** Every browse list orders by the record's own review rank, then deposition time (`created_at` descending), then id. Evidence is
  no longer a key: `has_kinetics`, `has_transition_state` (reactions), `has_entries` (species) and the calculation `quality_rank` are gone from the
  orders and the key catalogue; they stay filters. Thermo and kinetics search order by the record, not its parent entry. A non-default order the
  request asked for (`ranking=`, `mode=`) is labelled in `request.sort.requested_by`.
- **No read picks.** `collapse` and `selection_policy` are removed from every browse read, and from the exports; sending either is 422
  `browse_parameter_removed` naming the select endpoint. `effective_order` and `selection_policy_applied` are gone with them.
- **Exports never pick.** The native NDJSON and ML exports list every qualifying record (`tckdb.export` and `tckdb.ml` are format version 2; the ML
  species record carries `thermos`, a list). CHEMKIN takes `thermo_refs` / `transport_refs` / `kinetics_refs` and refuses with 422
  `export_selection_ambiguous` (every ambiguous species or reaction, with its candidate refs) or `export_ref_unmatched`.
- **Filters match any linked calculation.** `level_of_theory_*` and `software` on thermo and kinetics no longer read one role-prioritised "primary"
  calculation. This also fixes the kinetics `software` filter, which raised `AttributeError` on any reaction with a source calculation.
- **Honest names.** `temperature_min` / `temperature_max` on the thermo and kinetics reads filtered nothing; they are now
  `coverage_temperature_min_k` / `coverage_temperature_max_k` (the old names are 422 `parameter_renamed`).
- **Hidden calculations match no filter.** The thermo level-of-theory and software filters ignore a source calculation the read profile hides
  (curated), as the kinetics ones already did; a record whose only level-B link is an unreviewed calculation is not found by asking for level B.
- **The ML export picks nothing either.** The ML reactions record lists every transition-state entry with its energy and geometry per level
  (`transition_states`), one barrier per entry and per level at which the TS and every reactant have an energy (`barriers`, each inside one level;
  absolute energies at different levels are never compared), and names each entry with none (`barriers_unavailable`). The ML species record
  carries `frequency_results` and `hessians` as lists keyed by calculation and level. (`tckdb.ml` version 2.)
- **CHEMKIN** leaves transport out entirely when `include_transport` is false, and reports unmatched refs and ambiguous subjects in one 422.
- **`post_collapse_total` is removed** from every pagination block, and `collapse` / `selection_policy` are refused on every scientific operation
  (a router-level guard), not only where they once worked.
- **Every list endpoint's echo is tested** against the order it served, on rows whose review status, time and id disagree.
- Packages: `tckdb-schemas` 0.132.0 (four refusal codes), `tckdb-client` 0.162.0, `tckdb-mcp` 0.12.0. No schema or migration change.

## Method-preference rules removed; one browse order, named (2026-10-22)

TCKDB no longer ranks one method above another, anywhere. The manifest-backed rules (E1 G4 over G3, the XYG3 versus B3LYP barrier,
the network and structure candidates) and everything that applied them are deleted, and the first record in a list is documented as display
order, never as a preference.

- **Selection.** With no rules, a selection over eligible records is `sole_eligible_candidate` (one), `incomparable_alternatives` (several, in one
  front, in display order) or `no_applicable_candidate`. The outcomes `policy_preferred`, `policy_conflict` and (statmech) `unresolved_comparability`
  are gone, as are `policy.rules[]`, `relations`, `rule_matches`, `pair_checks`, `rules_applied` and statmech's `edges`, `conflicts`, `undecided` and
  `protocol`. The request policy `method_preferred` is retired; `default`, `most_reviewed` and `latest` remain and are administrative.
  The request options that only applied rules are removed: network `objective`, `reference_model_ref`, `reference_outputs`; structure `apply_rules`,
  `objective`, `reference_model`; statmech `comparison_objective`, `reference_model_ref`.
- **Manifests.** The thermo, kinetics, network, structure and statmech decision manifests are format version 2; replay refuses the old shape as an
  unknown format. The H298 golden was regenerated: every decision that carried an edge or a conflict is now `incomparable_alternatives` in one front.
- **Browse order.** `under_review` now ranks equal to `not_reviewed` (a curator opening a record is not a verdict). Review floors still tell the two
  apart. The SQL rank in structure search is generated from `REVIEW_RANK`. Analytics orders by review rank, deposition time (`created_at`), id
  (keyset cursor version 2). Every browse and list response echoes `request.sort` as a structured display order (keys, directions, meanings, and a
  statement that it is not a scientific preference). See `docs/specs/browse_order.md`.
- **Renamed and re-pinned.** The structure and statmech intent `protocol_preferred` is now `protocol_coverage` (it lists the
  protocols that cover the request; the old name is refused). The relative-energy manifests are format version 2 and
  record the structure administrative-key version. The transition-state badge rubric `computed_transition_state` is
  version 2 and pins that key version, so its stored machine reviews read stale. Species browse with a substructure or
  similarity query echoes `heavy_atom_count` first; thermo search echoes its real entry-level order; thermo and transport
  with `collapse=first` carry `request.effective_order`.
- **Schema and migration impact:** none.
- Packages: `tckdb-client` 0.161.0, `tckdb-mcp` 0.11.0.

## Stored and wire strings split into a name and an integer version; every TCKDB-owned version reset to 1 (versioning PR-4, 2026-10-20)

**The last of four versioning PRs, and the one clients see: a deliberate wire break, once.** Migration: `c7d4a9e2b6f1_split_versioned_names`
(both directions; it rewrites stored data, bypasses the accepted-science freeze inside its own transaction and **unpublishes every release**,
see below). New environment variable: none. `tckdb-schemas` 0.131.0, `tckdb-client` 0.160.0 (floor `tckdb-schemas>=0.131.0`), `tckdb-qcschema` 0.9.1; `tckdb-mcp` unchanged. OpenAPI snapshot, producer
contract and API vocabulary regenerated. Four rule-manifest SHA pins moved and need the owner's explicit approval (listed in the pull request).

In plain terms: anywhere TCKDB used to write one string with a version stuck on it (`computed_thermo_v2`, `tckdb.dataset_release.v2`,
`gaussian_v1`, `"computed_thermo@2"`) it now writes the name and the version as two fields (`computed_thermo` and `1`), and the numbers all start
again at 1 because the earlier numbering referred to versions nothing can read any more. Versions that belong to someone else (Gaussian 16,
NIST's ThermoML release, a producer's own parser) are left exactly as given.

- **Stored columns (migration).** `record_machine_review`: `prompt_name` + int `prompt_version`, `rubric_name` + int `rubric_version`, int
  `context_schema_version`, `model` `external_cp_comparison` (the `rubric_versions_json` dict is gone: a review holds exactly one rubric). The
  migration **aborts** if any review's `rubric_versions_json` is not exactly one key. `record_reproducibility_assessment.rubric_version` is an
  integer. `submission_audit_event.details_json["schema_version"] = "machine_review_v2"` becomes `["format"] = {"name": "machine_review", "version": 1}`.
  `release_manifest`: `manifest_format_*`, `recovery_archive_format_*`, `review_policy_*` pairs replace the three fused columns.
  `conformer_assignment_scheme.version` is an integer. Every integer version the revision adds has a CHECK constraint (`>= 1`; `>= 0` where the pre-reset 0 can be stored).
  `uq_external_source_record_identity` includes `parser_name` and `mapping_name`.
  `execution_environment_manifest`: `schema_name` + int `schema_version`, `canonical_json` rewritten and `content_digest` recomputed with a frozen copy
  of the canonicaliser. `calculation` / `calculation_parameter` / `calc_hessian`: `*_parser_name` beside the text version (`gaussian_v1` becomes
  `gaussian_parameters` / `1`, `orca_v1` and `orca_v2` stay two different parsers as `orca_parameters` `0` and `1`, a producer's own stamp is kept
  verbatim under the name `producer`). `external_source_record`: `mapping_name`, ThermoML stamps `thermoml_cp` / `thermoml_cp_mapping` at `1`
  (only rows that are ThermoML; any other row aborts the upgrade); `molecular_property_observation.external_source_parser_name`;
  `conformer_assignment_scheme` `torsion_basin` `v1` becomes `1` and its content-derived `public_ref` is recomputed.
- **Releases are unpublished (owner decision 3).** A release manifest's format tag sits inside its frozen checksum, so it cannot be relabelled.
  The migration deletes every `release_manifest` and `release_artifact` row and sets each **published** `dataset_release` back to `draft`
  (`published_at` cleared); a **withdrawn** release stays withdrawn with its `published_at`, `withdrawn_at` and `withdrawn_reason` intact. The DOI
  and every `release_selection` row are kept, so **a minted DOI points at an unpublished release until it is re-published**.
  Downgrade cannot restore the manifests and refuses to run while one exists.
- **The accepted-science freeze is bypassed, for this one revision only (owner decision 3).** The relabel touches provenance stamps on approved
  calculations. The migration disables every enabled user trigger on the tables it writes (recording its exact mode), rewrites, puts each one back in
  that mode (`ENABLE`, `ENABLE ALWAYS` or `ENABLE REPLICA`) and then raises (rolling everything back) if one it disabled is not back; a trigger that was
  already disabled is left as it was. No scientific value is read or written; no declared-repair machinery is used.
- **Everything stored as machine review or reproducibility assessment now reads stale, by design, and says why: old versions are stored as 0.** A
  rubric's latest old version on main becomes 1 (the same rubric; a frozen table in the migration, read from main) and every older version becomes 0;
  every prompt becomes 0 (the LLM prompt's wording changed while its version was reset to 1; a runner's old stamp had no version); a
  reproducibility rubric becomes 0. Version 0 is never current (`VersionRef` and every registry refuse it; the read side holds it in
  `StoredVersionRef`), so every pre-reset review reports `prompt_version_mismatch` and/or `rubric_versions_mismatch`. The context recipe version stays 1:
  no version is exempt from the reset. The downgrade restores a 0 exactly where one specific older version existed and refuses where several collapsed.
- **Split cleanly or abort.** Before writing, the migration inspects every stored shape and refuses (nothing committed) on a fused name it does not
  know, a look-alike parser stamp (`gaussian_v2`, `GAUSSIAN_V1`), a non-ThermoML source record, or a shape its downgrade could not restore exactly;
  afterwards it checks that no new `*_name` column still ends in a version segment. The downgrade restores the exact old strings or refuses.
- **Reset to 1.** All fourteen trust rubrics, the reproducibility rubric, the machine-review prompt, context schema and provider format, every
  selection engine's manifest format, policy, assessment-semantics and component versions, relative-energy, every rule (E1, XYG3, the network and
  structure candidates, the inactive council rules) and the four rule manifests, the document formats (dataset release, archive, export, ML export,
  deposit, review policy, execution environment, contribution bundle, replay sidecar) and TCKDB's own parsers. The four rule YAML files carry
  `name:` and `version: 1`, no `manifest_history`; their SHA-256 pins moved (every rule stays inactive; E1's approved status is unchanged).
- **Wire and API.** Trust `evidence.rubric` is the bare name with an integer `evidence.rubric_version`; `rubric_version` is an integer in the
  assessment summaries; the admin run-fake response carries `context_schema_version: int`, `prompt: {name, version}`, `rubric: {name, version}`;
  document headers are `format: {name, version}` objects (release, archive, export, ML export, deposit); release summaries carry `manifest_format`,
  `review_policy` and `recovery_archive` refs; selection manifests carry integer versions, `evidence_rubric` as `{name, version}`, `software` and
  `software_version` as two fields, and rule references as `{rule_id, rule_version}`; the producer-facing `parameters_parser_version` is
  `parameters_parser: {name, version}` and a Hessian's `parser_version` is `parser: {name, version}`; `bundle_version` is the integer 1.
- **More registries (dispatch by integer version, unknown refused).** Each engine's rules (`rules_registry`, with `rule_recorded_in` turning
  `UnknownVersion` into that engine's replay refusal), the dataset-release renderer, the machine-review audit-adapter payload formats, the
  document formats, TCKDB's own parsers, and each engine's assessment semantics.
- **Debt list: empty.** All 57 remaining rows are fixed, `MOVED_DEBT` and `RENAMES` and their script support are deleted, `COMPLETED_PRS` is
  {PR-2, PR-3, PR-4} and both ceilings are 0; only reasoned `ALLOWED` entries remain (botocore, the Gaussian IOp keyword, NIST's ThermoML
  release, a base64 payload, an arXiv id, an HPC module label, a database URL).
- **Strict wire versions.** A version written as a `Literal[1]` (`bundle_version`, the execution-environment `schema_version`) now refuses `true` and
  `1.0` (`ExactInt`). Every replay comparison of a recorded version or `{name, version}` block uses `same_value`, which does not let `True` pass for 1
  (thermo and kinetics `policy`, network `policy` and declaration versions, the statmech, structure and relative-energy `versions`).
- **Selection manifests carry `format: {name, version}`** in place of the bare `manifest_format_version` (and, for relative energy, the separate
  `manifest_format` name). The `assessment_semantics` block carries `name` and `version`, and its `source_findings` is a `{name, version}` pair
  (`structure_source_findings`) instead of a bare integer. The reader refuses a block with another engine's name, extra keys or a non-integer version.
- **Kept, and still bite:** stale marking, refusal of unknown versions, checksums, append-only results.

## Code names no longer carry a version; versions go through one registry (versioning PR-3, 2026-10-20)

**Renames plus registry dispatch; no stored value, no wire value and no version number changes.** Migration: none. New environment
variable: none. `tckdb-schemas` 0.129.0 (the contribution-bundle contract file and surface name); `tckdb-client` and `tckdb-mcp`
unchanged (neither used a renamed name). OpenAPI snapshot: the component `ContributionBundleV0` is now `ContributionBundle`, and
three route descriptions no longer cite `computed_transport_v1` / `computed_transition_state_v2`; nothing else moves.

In plain terms: a name in the code that had a version stuck on its end (`COMPUTED_THERMO_V2`, `ContributionBundleV0`) is now the stable
name (`COMPUTED_THERMO`, `ContributionBundle`), and the version lives in the object's own `version` field. Code that has to pick a
behaviour by version now asks a registry instead of a hand-written dictionary or constant.

- **Renamed (old to new):** the seven trust rubric constants (`COMPUTED_CALCULATION`, `COMPUTED_KINETICS`, `COMPUTED_THERMO`,
  `COMPUTED_STATMECH`, `COMPUTED_TS_STATMECH`, `COMPUTED_TRANSPORT`, `COMPUTED_TRANSITION_STATE`), the advisory rubrics
  (`EXTERNAL_CP_COMPARISON` and the six `*_CONSISTENCY`), `BOUNDS_V1` to `DEFAULT_BOUNDS`, `ContributionBundleV0` to `ContributionBundle`,
  `MachineReviewProvider{Finding,Result}V2` to `MachineReviewProvider{Finding,Result}`, `MACHINE_REVIEW_V2_SCHEMA_VERSION` to
  `MACHINE_REVIEW_SCHEMA_VERSION` (its value `"machine_review_v2"` is a stored string and moves in PR-4),
  `parse_machine_review_v2_payload`, `machine_review_v2_result_to_details_json` and `record_machine_review_v2_audit_event` lose `_v2`.
- **One rubric registry.** `RUBRIC_REGISTRY`, `OWNER_SCOPED_RUBRICS` and the machine-review `_ACTIVE_RUBRICS` tuple are replaced by
  `RUBRICS` (a `VersionRegistry` of every rubric, `startup_checked`), `RUBRIC_NAME_FOR_RECORD_TYPE` (record type to rubric name) and
  `OWNER_SCOPED_RUBRIC_NAMES`. `statmech_rubric_for` now returns the rubric *name*; callers resolve it with `RUBRICS.get(name)`. The
  evaluators and the admin trigger look their rubric up there, and the machine-review recipe is derived from `RUBRICS` so a new rubric
  cannot be left out of it.
- **Manifest formats go through registries.** Each selection engine (thermo, kinetics, statmech, structure, network) and the relative
  conformer energy manifests register their format in a `startup_checked` `MANIFEST_FORMATS` registry. The writer stamps the registry's
  current version; the reader looks up the version the manifest recorded. A missing, non-integer or unregistered version is refused with the same
  message as before (a manifest that records no version is never read as the current one). `SUPPORTED_MANIFEST_FORMATS` is gone.
  **Deliberately stricter:** the readers now match the recorded version as an exact integer, so a float (`2.0`) or a bool (`True`) is
  refused, where the old frozenset/`!=` readers accepted them because `2.0 == 2` and `True == 1`. Text, `null` and a missing key were
  already refused. No manifest this release writes is affected (they record a plain integer); only a hand-edited or foreign manifest.
- **Not converted, and why:** rule versions, policy versions and assessment-semantics versions are still text (`"1.0.0"`, `"2"`), and a
  registry version is an integer from 1, so they move to registries with the reset in PR-4. The dataset-release renderer and the
  machine-review audit adapter dispatch on a fused wire tag (`tckdb.dataset_release.v2`, `machine_review_v2`), which is also PR-4.
- Debt list: every PR-3 row is removed (111 rows); 57 rows remain, all PR-4. Two PR-4 rows whose f-string text named a renamed
  constant are declared in the new `MOVED_DEBT` tuple, which `check_versioned_debt_not_grown.py` accepts only for a row whose old text
  existed at the merge base, is gone from the head, has no higher count, and whose new text is the old text with only the whole-word
  identifiers of the declared `RENAMES` map replaced (each new name in that map must itself pass the version patterns, read from the merge
  base). An old row can be claimed by one move only. A move is printed once, while the base still has the old row; once it has landed it is inert.

## Legacy and replay-only versions deleted (versioning PR-2, 2026-10-20)

Pre-release, so only the current version of anything is read. Removed, with their fixtures and tests: the kinetics XYG3 0.2.0 replay rule and its manifest, the structure-rule 0.1.0 dual pin, pre-v2 assessment semantics, every previous selection-manifest format (thermo, kinetics, statmech and structure 1; network 2), the relative-energy v1/v2 projection, the dataset-release v1 renderer, the machine-review v1 (precheck) contract with `llm_precheck` providers and runner, the statmech owner transition report, and the dead parser stamps. Kept and tested: refusal of an unknown version, stale-assessment marking, checksums and append-only results.

- A manifest of a retired format, an assessment block that is missing or foreign, a rule version this release does not carry, or a machine-review payload without the current `schema_version` marker is now refused.
- `GET /submissions/{id}/ai-review-summary` reads the event's `status` (it read a `label` key no current event wrote, so `label` was always null).
- Thermo replay now refuses a rule resting on a different audited manifest digest, as kinetics, statmech and network already did.
- **Deploy note:** a release frozen under the retired `tckdb.dataset_release.v1` tag makes `verify_release` (and any re-render) raise `unknown_manifest_schema`. Releases frozen under the old tag stop verifying until PR-4's migration rewrites the stored tags; there is no compatibility path.
- Removed environment variable: `LLM_PRECHECK_PROVIDER` (setting `llm_precheck_provider`, type `LLMPrecheckProviderName`). Nothing read it after the precheck provider factory was deleted; `AI_REVIEW_ASSISTANT_MODE` selects the provider.
- `verify_release` now has a test that fails if the recorded `content_sha256` is not compared with the stored document.
- New refusal code `unknown_manifest_schema` (409), catalogued in `code_catalogue.py`. `tckdb-schemas` is 0.128.0 and `tckdb-client` is 0.158.0 (generated contract and `RejectionCode`).
- Schema impact: none. No migration. No new environment variable.

## One way to version things: `VersionRef`, `VersionRegistry`, and a guard (2026-10-19)

**New types and a guard test; no behaviour change.** Migration: none. New environment variable: none. OpenAPI snapshot: unchanged.
`tckdb-schemas` 0.126.0 (one new model); `tckdb-client` unchanged.

- A versioned thing TCKDB owns is a stable `name` plus a separate integer `version`, never a name with the version glued on.
  `VersionRef(name, version)` (tckdb-schemas) carries the pair and refuses a name that embeds a version. `VersionRegistry`
  (`app/versioning.py`) maps `(name, version)` to the implementation, with exactly one current version per name; a version-dependent
  function takes `version: int | None = None` and dispatches through it, and `UnknownVersion` is raised for a version that is not
  registered instead of falling back to the current one. `EvidenceRubric` gains `ref`; its unused `qualified_name` is removed.
- `backend/tests/test_no_versioned_names.py` fails on any new identifier, string constant, f-string or file name that embeds a version
  (`parse_machine_review_v2_payload`, `ContributionBundleV0`, `...RuleV0_2_0`, `tckdb.archive.v1`, `rules_0_1_0.yaml`). Everything that
  already violates the rule is listed, with the PR that removes it, in `backend/tests/versioned_names_debt.py`; that list can only shrink.
  The guard also scans f-string, `.format`, `%` and `+` builds, lowercase `foov2`, the YAML rule manifests, `integrations/mcp/src` and
  non-test `frontend/src`; chemistry (`cc-pV5Z`, `VV10`) is pinned as clean, and rule ids such as `K-XYG3-B3LYP-BARRIER` are valid names.
  `repo-gate.yml` runs `backend/scripts/check_versioned_debt_not_grown.py`, which compares the debt list with the merge base's copy so a
  count-for-count swap is refused. A startup check (`validate_version_registries`) fails the boot for a registry name with no current version.

## Curators can record structure-evidence findings (2026-10-19, #750)

**Additive route.** Migration: none (the table, its append-only trigger and its constraints already exist). New environment variable: none.
`tckdb-schemas` and `tckdb-mcp` unchanged. `tckdb-client` is 0.157.0: the two routes are registered in the parity ledger as curation workflow
(no typed method, as for the other curator routes) and `RejectionCode` gains twelve `structure_finding_*` members.

- `POST /api/v1/curation/structure-evidence-findings` (curator or admin) appends an authorized adjudication about a geometry, calculation or
  structure determination named by public ref. `GET` on the same path lists a subject's findings and whether each is still in force.
- Append-only: a correction is a later adjudication naming the earlier ruling on the same subject and stating the replacement verdict
  (`does_not_invalidate`, `unresolved` or `invalidates`). Corrections can themselves be corrected; nothing is edited or deleted.
- **Selection behaviour changes only for a corrected role invalidation or contradictory characterization.** Selections already read the newest ruling in
  force of each chain, and a finding no correction names reads exactly as before. What changes: a correction that continues a `role_invalidation` or a
  `contradictory_characterization` now keeps that claim's role and scope instead of reading as a whole-subject failure that bars every role.
  No stored manifest can contain such a chain, because no finding could be written through the API before this route existed, so the
  eligibility semantics are not re-versioned. The curator `GET` shows both the recorded `kind`/`role` and the `applied_kind`/`applied_role`
  that selections use.
- An in-force `invalidates` finding excludes the evidence from H298, kinetics and structure selection through the route, and a correction
  restores or reopens it. Before this, only tests that inserted rows directly could do either.
- Twelve new coded refusals, all prefixed `structure_finding_`; see the generated error-code docs.
- Spec: "Recording a structure-evidence finding" in `backend/docs/specs/automated_trust_layer.md`.

## Transition-state trust carries the scoped structure assessment (2026-10-19, #723)

**Additive field and a rubric bump.** Migration: none. New environment variable: none. `tckdb-schemas` and `tckdb-client` unchanged
(the client types `trust` as `JSONDict`). `computed_transition_state` is version 5; machine reviews stamped version 4 or 3 read as stale
and are re-planned, none is rewritten.

- `trust` on a transition-state entry (detail, search, the sibling `entries`, and `/reaction-entries/{id}/full`) gains
  `structure_assessment`: the structure assessment's own answer to one fixed, evidence-only question (`qualify_evidence`,
  `first_order_saddle`, `known_values`, no connectivity requirement), echoed as `task` so the same request to
  `POST /scientific/transition-state-entries/{ref}/evidence/select` returns the same answer. Other record types are unchanged.
- It runs on a single transition-state-entry detail read only. Every list surface carries the status `not_assessed_on_lists` and
  issues no statement for it. An entry with no declared saddle target reads `no_declared_target` and costs one statement.
- New zero-weight warning `declared_saddle_target_qualifies`: it fires when a declared target does not qualify or is contested. It
  means the declared target needs investigation, not that the transition state is disproved. Label, completeness, score and
  `is_certified` never read the assessment.
- The frequency-presence checks read the frequency result a declared target pins in role `curvature` (an entry that declares nothing
  keeps reading the newest attached result). Lists read the pins too, in one grouped statement per page or response, so the label and
  the passed/possible counts agree on the entry page, browse, search, the sibling `entries` and `/full`.
- `saddle_point` (the "N imaginary modes" block) follows the pin: it describes the pinned result and carries the new `basis` value
  `declared_curvature_witness`; with no declaration it is unchanged (`latest_direct_frequency`). When several declared targets each pin a frequency result, the checks pass if any pinned result passes the designation rule and the block shows that result; when none passes, both use the newest pin. Declarations of a rejected or deprecated entry pin nothing, on lists and on the entry page alike; under `profile=curated` a hidden frequency result is absent for the block and the checks, which then read the newest visible result.
- Under `profile=curated` the assessment runs under that profile: a target resting on a hidden calculation is unresolved and warns.
- The TS-entry page shows the assessment in plain words.

## The curated ref guard covers every route; the per-surface hiding is retired (2026-10-18)

**No wire change, apart from one statement fewer on the statmech reads that include `frequencies`.** Migration: none. New
environment variable: none. `tckdb-schemas` and `tckdb-client` unchanged; OpenAPI golden unchanged.

- The rollout allowlist (`_ENABLED_ROUTE_PREFIXES`) is deleted. The boundary hook applies to every scientific route that is not
  in `EXEMPT_ROUTE_PREFIXES` (releases, `/meta` vocabulary, artifact download bytes), so a route added later is guarded from the
  day it exists. The sweep is enforce-only: its baseline file, report mode and regenerate switch are gone.
- Retired the hand-written hiding the guard now covers: the statmech source-calculation/frequency/torsion filters (#765), the
  curvature-witness badge filter (#764; it also issued a review-badge statement under the exploratory profile), the statmech
  conformer-context SQL filter, the thermo provenance hiding (#767; slots are still picked from every link first) and the
  transition-state statmech summary hiding (#767; the batching stays). The count/flag floors (D5) and the selection
  population withholding stay: they are not ref rules.
- Tests of #764, #765 and #767 pass unmodified. Curated and exploratory responses of the sweep crawl are byte-identical to
  the previous head.

## Curated exports no longer name hidden records (2026-10-18)

**Narrowing under `profile=curated` only; the default (exploratory) output is byte-identical.** Migration: none. New environment variable:
none. `tckdb-schemas` and `tckdb-client` unchanged.

- The four export streams (`/export/ndjson`, `/export/ml/species.ndjson`, `/export/ml/reactions.ndjson`,
  `/export/chemkin`) bypassed the response boundary and could name records whose own route answers 404. They now reach the
  ref guard through a document seam: record dicts are buffered 200 at a time and withheld in untyped mode (one visibility
  statement per batch). A withheld ref is `null` in an export, a hidden ref in a list of strings is dropped, free text that
  names one is blanked. Exports have no placeholders.
- Where a hidden record supplied a *value* it is left out before anything is derived: a hidden calculation or statmech record
  is no longer a linearity witness (NDJSON `is_linear*`, CHEMKIN transport comments), a hidden calculation's energy,
  frequencies, Hessian, geometry and conformer links are not in the ML species records, and a hidden calculation or
  transition-state entry no longer sets or names the ML reaction barrier. A visible transition-state entry of a hidden
  transition state keeps its entry and has a `null` owner. Gap and skip counts follow the floor.
- The profile is captured before a generator is returned and passed down explicitly (the ML streams used to read it on first
  iteration, and the thermo/transport/kinetics selection read the context variable). The sweep's `KNOWN_UNGUARDED` and
  baseline entries for the four exports are gone.
- Release artifacts and `/releases/*` are untouched (they carry their own digest; the export streams carry none).

## Curated selection documents no longer name hidden records (2026-10-18)

**Under `profile=curated`, hidden evidence now reads as unavailable and the curated decision may differ from the exploratory one. Exploratory output is unchanged except the manifest format-version numbers.** Migration: none. New environment variable: none. `tckdb-schemas` and `tckdb-client` unchanged; OpenAPI golden unchanged.

Rule (hidden == unknown): selection evidence that points at a record the reader cannot open is treated exactly like a reference to nothing. A nested ref the engine only needs to compare (a determination's target, a candidate's own target group, a supersession pointer) becomes a placeholder `withheld:<n>` (sorted-ref numbering, one token per real ref per document, taken on the selection's own snapshot) before anything is assessed, decided or hashed; a curated manifest replays byte for byte.

- **Thermo (format 2):** curated, a hidden supporting calculation (protocol citation or source link) no longer counts: it is dropped from the protocol, from the recipe keys, from the source-finding check and from the trust evidence. A record that targets a hidden conformer group carries a token.
- **Kinetics (format 2):** curated, hidden supporting and energy calculations and a hidden network solve (with the fit link it gates) no longer count. A determination's target (transition-state entry, network, channel) carries a token.
- **Network (format 3):** unchanged behaviour: a validation entry's reference to a solve the reader cannot open is `reference_solve_ref: null` with `reference_withheld: true`, as for a reference to nothing, and the evidence reads as unavailable. Replay of a format-2 manifest still reads the flag.
- **Structure (format 2):** curated, validation evidence resting on a hidden calculation is no evidence row. A basin determination of a hidden conformer group carries a token.
- **Statmech (format 2):** curated, a hidden conformer group no longer anchors a legacy record and a member declared in one states no group. A declaration's own scope group and a supersession pointer carry tokens.
- **Relative energies (version 3):** a hidden group stays out of the compared world as before; the semantic version moved so the document may carry a token. Version 2 manifests, comparison manifests included, still replay.
- Free text that names a hidden record (in any spelling: percent-encoded, with invisible characters) is blanked whole to `""`, never to null: a torsion's `invalidated_reason` is read for its presence, so the invalidation stays and only the words naming the hidden record go. A depositor URL is blanked whole.
- Thermo and kinetics source findings no longer read structure determinations the profile hides: a live finding on a hidden determination that shares a visible source calculation does not block the visible record.
- The sweep crawls network select, statmech select and export-selected over a world with a hidden ref in every nested position; its baseline lost every selection entry.

## Curated reads of the network, literature, analytics and provenance routes no longer name hidden records (2026-10-18)

**Narrowing under `profile=curated` only; the default (exploratory) output is unchanged.** Migration: none. New environment variable:
none. `tckdb-schemas` unchanged, `tckdb-client` 0.156.0.

- The central ref guard is enabled for `/networks`, `/network-solves`, `/network-kinetics` (including both evaluate routes, which now call
  it), `/literature`, `/analytics`, `/level-of-theories`, `/frequency-scale-factors`, `/energy-correction-schemes` and
  `/composite-schemes`. The sweep ratchet lost every entry of these families.
- **OpenAPI field widening (D1):** `ScientificNetworkSolveRecord.network` and `ScientificNetworkKineticsRecord.network` are nullable (still
  required keys). A visible solve or fit of a hidden network stays visible with the owner withheld; a hidden owner never hides its child.
- **Batch evaluate** (`POST /networks/{ref}/kinetics/evaluate`) evaluates the fits of the solves the profile shows. It used to evaluate every
  fit through the handle gate, answer 404 naming a hidden fit, and count hidden fits against its grid cap.
- Counts, flags and filters follow the floor (D5): a network's solve, fit and source-calculation counts, kind flags, temperature/pressure
  envelope and per-channel `has_kinetics`; a solve's evidence, barriers and state-energy sources; a fit's source-calculation count; the
  network search filters; a level of theory's usage, `used_by`, software breakdown and listing (a level used only by hidden calculations is
  not listed); a scheme's and a scale factor's `used_by` and usage counts; a literature record's linked-record counts; and the analytics
  provenance flags and filters.
- **Exploratory is not byte-identical in one place:** a network's `solves` list now has a deterministic order (ascending row id); it
  had none. No other list of this change gained an `ORDER BY` that alters exploratory output (the batch evaluate's solve query is
  ordered too, but its fits were already served in row-id order).
- **Shapes upload cannot produce (D1).** A visible record stays visible and its links to hidden records are dropped, even then: an
  `elementary` channel whose only microreaction cites a hidden transition-state entry is served as `mechanism: "elementary",
  microreactions: []`. A validation entry's reference to a hidden solve is `null`; `reference_withheld` stays `false` on every read
  (it could only ever be `true` for a hidden solve). The analytics cursor's `watermark.max_id` is the largest id the profile shows.
- `tckdb-client` 0.156.0: `NetworkSolveRecord.network` and `NetworkKineticsRecord.network` are `JSONDict | None`.

## Curated reads of the transition-state and reaction routes no longer name hidden records (2026-10-18)

**Narrowing under `profile=curated` only; the default (exploratory) output is unchanged.** Migration: none. New environment variable:
none. `tckdb-schemas` unchanged, `tckdb-client` 0.155.0.

- The central ref guard is enabled for `/transition-states`, `/transition-state-entries`, `/reaction-entries` (the entry's kinetics and
  `/full`), `/reactions` and `/kinetics`. Calculation, transition-state-entry, network, statmech, conformer-group and kinetics blocks that
  name a record below the review floor are dropped from lists or set to `null`; a ref the guard cannot place fails the request (500)
  rather than leaking. The sweep ratchet lost its 40 entries for these routes.
- **OpenAPI field widening (D1):** `ScientificTransitionStateEntryRecord.transition_state` and `TransitionStateInFull.transition_state_ref`
  are nullable (still required keys). A visible entry whose transition state is hidden stays visible with the owner withheld; a hidden
  owner never hides its entry.
- A kinetics record's `determination` is `null` when its target entry or network channel is hidden, `tunneling_application` is `null` when
  it cites a hidden entry or calculation, and `provenance.irc` is `null` when its calculation is hidden.
- Counts and flags follow the floor (D5): a transition state's `entries_summary` and evidence coverage, an entry's `evidence_summary`,
  `available_sections` and `validation` tokens, and a reaction's `availability` (kinetics count, transition state, path search, atom map)
  count only what the profile shows. `/reactions` default order sorts on those flags, so a curated page can order the same records
  differently.
- The same floor now reaches what used to be existence oracles: the `levels_of_theory` maps and kinetics `levels` (a level that comes only
  from a hidden calculation is not shown), the evidence checklist, `trust` fragments and public assessments (evaluated over the
  calculations the profile shows), the transition-state search filters (`has_*`, `method`, `basis`, `software`) and the species browse
  method/basis filter, and `include_review=full` on `/full` (no review row of a hidden record, in a stable order).
- `tckdb-client` 0.155.0: `TransitionStateEntryRecord.transition_state` is `JSONDict | None`. The transition-state entry page tolerates a
  null owner.

## Structure rule manifest 0.2.0, with 0.1.0 kept for replay (2026-10-18)

**Audit record only; no rule is activated.** Migration: none. New environment variable: none. Package versions: unchanged. OpenAPI
golden: unchanged.

- Manifest 0.2.0 records owner decision O5 (2026-10-13): relative conformer energies are a named-reference operation, descriptive
  MADs are useful without a numeric reference uncertainty, and a superiority claim or a near-tie ranking needs stronger evidence.
  Both `S-ACONFL-*` rules gain the blocker `descriptive_mad_is_not_preference_evidence` listing E1 to E7, and their blocker
  `relative_energy_is_not_a_supported_quantity` is renamed `no_preference_stage_consumes_relative_conformer_energy` (a relative-energy
  read exists now; no preference stage consumes it). Their owner-decision evidence items are marked answered.
  `S-FIXED-MODEL-CONVERGENCE` gains `owner_declined_universal_form`. Every rule is version 0.2.0 and still `activation_approved: false`.
- Source correction: the open-access reprint of Santra and Martin (2022) is recorded (URL and SHA-256 as supplied; nothing was fetched).
  Table 4 prints the per-conformer best-estimate values, which 0.1.0 had said were located but not extracted. A footnote-marker
  slip on the ACONF20 column is recorded as an unresolved noted discrepancy.
- Dual pin, as for the kinetics XYG3 rule: manifest 0.1.0 is preserved byte for byte and `replay_rules()` loads its rules for replay
  only. A structure manifest recorded under 0.1.0 still replays and `replay_provenance` labels it `historical`; new decisions carry
  0.2.0. A version in neither registry is still refused, and `replay_provenance` (structure and kinetics) refuses it rather than labelling it historical. The live validator stays strict on rule id; only replay holds one rule at two versions, and a manifest naming two versions of a rule is refused.

## A descriptive comparison of two stored protocols (2026-10-18)

**New read routes.** Migration: none. New environment variable: none. Package versions: `tckdb-schemas` 0.125.0 (one new refusal
code in the shipped reference), `tckdb-client` 0.153.0 (the same code in `RejectionCode`; no method). OpenAPI golden regenerated
(two new paths, no existing path changed).

- `POST /api/v1/scientific/species-entries/{ref}/conformers/relative-energies/compare` and `/compare/manifest`: how far two stored
  protocols (`candidate`, `reference`: a level and an optional recipe) deviate from each other, candidate minus reference, in kJ/mol,
  against the one reference conformer the caller names. `pairing` (`same_geometry` or `same_basin`) is required. Mean signed,
  mean absolute and root-mean-square deviation, and the largest with its conformer; the reference conformer is not counted. A pair
  missing on either side or failing the pairing is listed with a coded reason and left out, never counted as zero; with no pair
  every figure is `null`; a complete answer with an excluded pair is `incomplete_pairing` with no figures.
- It is a description, not a verdict: `claims.superiority` and `claims.ranking` are `not_licensed`, and the seven prerequisites of a
  future superiority claim (E1 to E7) are always listed as missing. The comparison builds no selection-kernel edge and reads no rule.
- New code: `relative_conformer_energy_compare_identical_protocols` (the same level, with the same effective recipe narrowing, named twice; decided after the entry, levels and groups resolve).
- The manifest holds both runs' complete relative-energy manifests; `replay_comparison` recomputes both and the pairing.
- Docs: a section in `backend/docs/specs/relative_conformer_energy.md`, and `docs/guides/relative_conformer_energies.md`.
- The relative-energy members gain `value_geometry_ref` (additive: the one geometry the stored value was evaluated at). The relative-energy semantic
  version becomes `"2"`. Manifests downloaded from `/relative-energies/manifest` before this release (version `"1"`, no such field) **still replay**:
  the recorded answer is compared with the recomputed one projected onto the fields version 1 carried. The projection is keyed to the
  recorded version; a version-2 manifest missing the field is refused.

## Curated reads of the species-side routes no longer name hidden records (2026-10-17)

**Narrowing under `profile=curated` only; the default (exploratory) output is unchanged.** Migration: none. New environment variable:
none. `tckdb-schemas` unchanged, `tckdb-client` 0.152.0.

- The central ref guard is enabled for `/statmech`, `/thermo`, `/species-entries`, `/species`, `/transport`, `/conformer-groups`,
  `/conformer-observations`, `/conformers`, `/calculations`, `/species-calculations`, `/artifacts` and `/geometries`. A block that
  is about a hidden record (a source calculation, a dependency, a conformer context, an owner TS entry, a correction notice) is
  dropped whole; an optional ref is set to null; a ref the guard cannot place fails the request with a 500 rather than leaking it.
- **Wire change (OpenAPI):** `ScientificConformerObservationRecord.conformer_group` is nullable. Under `profile=curated`, a visible
  observation whose conformer group is not approved comes back with `conformer_group: null`; the observation itself stays visible.
  The default profile never returns null there.
- Free text under `profile=curated`: a note, label, file name or depositor URL (for example a review note saying "duplicate of
  calc_..." on a visible record, or `LiteratureIdentifiers.url`) that names a hidden record's ref is blanked as a whole field
  (`null`, or `""` where the field is required), never edited token by token. Text whose percent-encoding never settles is
  blanked too. The default profile is unchanged. Not covered here: pagination cursors (`next_cursor`), left to their own
  family's change.
- A URL field that names a hidden record's ref anywhere (every path segment, `;params`, query keys and values, the fragment;
  percent-decoded until stable, absolute or relative; for example an applied correction's `target_endpoint`) is
  withheld together with the `*_ref` and `*_id` beside it, and a hidden record's id is removed from the aligned
  `supporting_calculation_ids` list when its `supporting_calculations` row is dropped.
- Under `profile=curated`, `calculation.owner.kind` can be `transition_state_entry` with both owner blocks null (the owning entry is
  hidden). The default profile always carries exactly one owner.
- Owner contracts that said "exactly one" now state the curated exception: `GeometryIdentity` (`identity` is `null` when every
  owner is hidden, the same as a geometry with no owner; `ambiguous_owners` can list fewer owners than were found, never none) and statmech (`species` and `transition_state` both null
  when the subject TS entry is hidden). `tckdb-client` 0.152.0 (this text is comment-only).
- The conformer-observation page (`conformer_group` null) hides the basin breadcrumb and the "Conformer basin" row.
- Species-entry `availability` flags, `calculation_count` and the `*_summary.ids` lists count only what the curated floor shows
  under `profile=curated`, the same as the list endpoints. Unchanged under the default profile.
- **Not yet covered:** transition-state, reaction, kinetics, network, literature, analytics and level-of-theory routes, selection
  documents and exports (the sweep baseline still lists them).

## Relative conformer energies against a named reference conformer (2026-10-17)

**New read route; one route deprecated.** Migration: none. New environment variable: none. Package versions: `tckdb-schemas` 0.124.0
(three new refusal codes in the shipped reference), `tckdb-client` 0.151.0 (the same codes in `RejectionCode`). OpenAPI golden
regenerated (new paths; the structure-manifest description corrected).

- `POST /api/v1/scientific/species-entries/{ref}/conformers/relative-energies` and `/manifest`: each conformer group's energy
  minus a reference conformer group the caller names, in kJ/mol, at one named level, for the electronic energy or the zero-kelvin
  energy. The reference, energy kind, level and certificate are required and have no default; a negative difference stays negative;
  a conformer with no usable value is listed absent with a coded reason and `null` numbers, never zero; members are ordered by ref
  with no rank. Read in a REPEATABLE READ snapshot; the manifest replays.
- New codes: `relative_conformer_energy_unsupported` (enthalpy, Gibbs, no certificate), `relative_conformer_energy_population_too_large`
  (more than 500 visible groups) and `relative_conformer_energy_scope_owner_mismatch` (a group of another entry). Enthalpy and
  Gibbs energy are deferred until a deposit needs them.
- Under `curated`, a hidden group, observation, determination or calculation is not in the answer or the manifest.
- `GET /api/v1/species-entries/{id}/conformer-observations/lowest-sp` is **deprecated** (`deprecated: true` in OpenAPI), pointing to
  the new operation. It still works and is not removed.
- Docs: `backend/docs/specs/relative_conformer_energy.md`, `docs/guides/relative_conformer_energies.md`. The structure manifest's
  schema description no longer claims `tckdb_client` replays it (the client has no such function).

## A central guard for refs the curated profile hides, wired in but not yet enabled (2026-10-13)

**No behaviour change on the wire.** Migration: none. New environment variable: none. No package version changes; OpenAPI golden
unchanged.

- New `app/services/scientific_read/ref_guard.py`: finds every ref of a review-gated record type in a finished scientific response,
  resolves which the active profile hides in one statement, and removes them (delete a pointer block, null an optional ref, fail
  closed with a 500 for a required ref it cannot place). It is called from `apply_internal_ids_visibility`, but no route family is
  enabled yet, so it does nothing.
- One shared predicate, `status_hidden_under_profile`, now answers "hidden under the profile" for the handle gate, the guard and
  the statmech curvature-witness filter. Thermo, kinetics and molecular-property observations join the review-gated set.
- A sweep test calls every `/scientific/*` route under `curated` against a world with a hidden instance of every gated type. Its
  baseline file lists today's known leaks; the per-family follow-ups remove them.

## The XYG3 barrier rule's comparator record is corrected (2026-10-13)

**Data change to an inactive rule; nothing selects differently.** Migration: none. New environment variable: none. No package
version changes; no schema change.

- Manifest `xyg3_b3lyp_barrier_manifest.yaml` is 0.4.0 (new pin) and the rule `K-XYG3-B3LYP-BARRIER` is 0.3.0, because a rule resting on a different audited manifest
  is a new rule version. Rule 0.2.0 over the preserved manifest 0.3.0 (`xyg3_b3lyp_barrier_manifest_0_3_0.yaml`, unchanged
  bytes) stays loadable **for replay only**, so a kinetics decision manifest recorded before this release still replays;
  live selection uses 0.3.0. The rule stays **inactive** (`activation_approved: false`).
- The B3LYP row of Zhang 2009 Table 2 is the 2005 Truhlar B3LYP/MG3S row, not 6-311+G(3df,2p). The same-basis comparison
  (Zhang, Luo, Xu, J. Chem. Phys. 133, 104105 (2010)) gives a 76-barrier MAD of 1.02 for XYG3 against 4.54 kcal/mol for B3LYP at
  6-311+G(3df,2p) and QCISD/MG3 geometries, XYG3 closer on 69 of 76 barriers (B3LYP closer on 7). The Table III HAT12 typo
  (1.66 printed, 1.38 in the SI) is recorded. Four sources are SHA-256 pinned; no PDF is copied into the repository.
- The comparator blocker is resolved. Geometry provenance is not: TCKDB cannot record which geometry a stored barrier used.
  The owner chose to keep the rule inactive for now; whether to fix that with a schema change or accept it as a stated
  limitation is undecided. The 7 barriers where B3LYP is closer have no treatment yet, and owner sign-off has not been given.

## Curated reads no longer name more records the curated profile hides (2026-10-12)

**Narrowing under `profile=curated` only; completes the list left open by the previous entry.** Migration: none. New environment
variable: none. No package version changes; no schema change (OpenAPI golden unchanged).

- `GET /scientific/transition-state-entries/{ref}?include=statmech`: `statmech[].source_calculations` omits calculations below `approved`.
- `GET /scientific/species-entries/{ref}/thermo`: the provenance's statmech fallback (`freq_calculation_ref` / `sp_calculation_ref`, the
  primary calculation and the conformer traced through it) no longer names a hidden source calculation, whether it is the thermo's own
  source or comes from the linked statmech. Applies to the species thermo list and to thermo search (which reuses the same builder).
  The same read also leaves out the thermodynamic target's `conformer_group_ref`, `provenance.statmech_ref` and the traced
  `conformer_observation_ref` / `conformer_group_ref` when the record they name is below `approved`.
- Statmech `include=conformers` omits conformer groups below `approved`.
- Exploratory (the default) output is unchanged. Record-level facts (levels, evidence, availability flags such as `has_conformers`) are
  unchanged, as before.
- Provenance slots are picked from every link first and a hidden pick is then left unnamed: a hidden primary / freq / sp is never
  replaced by another calculation, and `provenance.level_of_theory` stays the true primary's, so no record-level fact varies by profile.
- The TS-entry and TS-concept `include=statmech` also omit statmech records below `approved`, in one batch per read.
- **Not yet covered.** Curated reads are not yet complete: other surfaces may still name records the curated profile hides (a follow-up
  lists them), and supersession notices can name a successor that has since become hidden (needs an owner decision). Do not assume a
  curated read hides every unapproved ref.

## A superseded record can no longer be approved again (2026-10-13)

**Narrowing: a review transition previously accepted is now refused.** Migration: none. New environment variable: none.
`tckdb-schemas` 0.123.0, `tckdb-client` 0.149.0.

- `POST /submissions/{id}/approve` is not refused: a submission member that a correction has already replaced is left deprecated
  (not re-approved) and the other members are approved. The skipped members are listed by public ref
  (`record_type`, `record_ref`, `superseded_by_ref`) under `skipped_superseded_records` in the `curator_approved` audit event's
  `details_json`. Refusing the whole submission would have left it pending forever, since the edge cannot be undone.
- `PATCH /record-reviews/{type}/{id}` answers 409
  `superseded_record_not_approvable` when the target is `approved` and the record has an outgoing supersession edge, that is,
  it has been replaced by a correction. Supersession is the correction path: the predecessor stays deprecated and its successor
  stands. Re-approving the predecessor made it curated-visible again with a notice naming a successor that might be hidden.
  `context` is `{record_type, record_ref, superseded_by_ref}`, public refs only (the two refs are omitted for a type that has none).
- Only the `approved` target is refused. `under_review`, `rejected` and `deprecated` on a superseded record behave as before, so a
  curator can still reopen or reject it; the record just cannot return to `approved`. An `approved -> approved` re-stamp of a legacy
  approved-and-superseded row is refused too. Approving the superseding record is unaffected, and the supersession call itself
  (which writes `deprecated`) is unchanged.
- Existing data is not touched. A superseded record left approved before this guard stays as it is until a curator deprecates it.

## Transition-state-owned statmech is graded by its own rubric, not hard-failed (2026-10-13)

**Trust correction; changes what readers see. Requires owner sign-off and a rollout step (below).** Migration: none (rubrics are
code; no `record_machine_review` row is touched). New environment variable: none. No package version changes: no response-schema
shape changes (OpenAPI golden moves only in one route description), so the package versions are not bumped.

- **Defect.** A statmech record owned by a transition-state entry was graded by the species rubric, whose first required check is
  "attached to a species entry". Every such record read `hard_failed` / `species_entry_missing` on `include=assessments` and
  `include=trust` (detail, search, species read), however complete it was.
- **Fix.** A new owner-scoped rubric, `computed_ts_statmech` v1, grades transition-state-owned statmech. It is `computed_statmech`
  v2 with one check swapped (`transition_state_entry_present` replaces `species_entry_present`); every other check is the same
  object. It adds no TS-specific check, certifies nothing, keeps an automated geometry fail advisory, and the statmech selection
  package still never imports the trust layer. A TS-owned record's `assessments.deterministic_trust.rubric` /
  `trust.evidence.rubric` become `computed_ts_statmech` / `computed_ts_statmech_v1` and its check map gains
  `transition_state_entry_present` and loses `species_entry_present`.
- **Species-owned records do not change**: rubric `computed_statmech` v2, check map, badge, completeness and the currency of their
  stored machine reviews are byte-identical (the shared version is deliberately not bumped). Pinned by a frozen-expectation test
  and by an equivalence test against the species rubric run directly.
- **Machine review.** A review is now stamped with the rubric that actually evaluated the record. The reviewer-family recipe for a
  TS-owned statmech is `{"computed_ts_statmech_v1": "1"}`. No stored review is rewritten or deleted. After deploy, the latest review
  of each TS-owned statmech reads **stale** (`context_hash_mismatch`, `rubric_versions_mismatch`); species-owned reviews stay
  current. There is no automatic or bulk re-review.
- **Operator report (new, read-only).** `backend/scripts/ops/statmech_trust_owner_report.py` prints, per owner kind, how many
  statmech records change badge and how, whether the species-owned records were routed to the other rubric (`species_entry_unchanged`, which checks routing only and
  must be `true`), and which stored reviews become stale. Also read the species rows of `machine_review_currency`: every one must
  be `current`, which is the check anchored to the code on `main`. It refuses to run without `--database-url` or `--use-configured-database` and runs in a
  `REPEATABLE READ`, `READ ONLY` transaction; there is no apply mode.
- **Rollout.** Restore the latest backup into a scratch database, run the report there with this code, read
  `species_entry_unchanged`, that all species review rows are `current`, and the `transition_state_entry` rows, then deploy. Leave TS-owned reviews stale. Details:
  `backend/docs/specs/automated_trust_layer.md` section 9.3.1.
- The reproducibility rubric has the same species-only assumption for TS-owned statmech; that is a separate decision and is not
  changed here (issue #760).
- Generated: `docs/guides/api_vocabulary.md` (the new check name), the OpenAPI golden (one route description no longer names
  `computed_statmech_v1`).

## Curated statmech reads no longer name calculations the curated profile hides (2026-10-12)

**Narrowing under `profile=curated` only.** Migration: none. New environment variable: none. No package version changes; no
schema change (OpenAPI golden unchanged).

- Under `?profile=curated`, `source_calculations` and `frequencies.source_freq_calculation_refs` omit linked calculations
  below `approved`, the ones `GET /scientific/calculations/{ref}` already answers 404 for. Before, they listed those refs with
  their roles, types and review status. Applies to statmech detail, statmech search and the species-entry statmech read.
- Under curated, a torsion's `source_scan_calculation_ref` (and `_id` under `internal_ids`) is null when that scan calculation is hidden.
- Exploratory (the default) output is unchanged.
- Deliberately not filtered: record-level facts (`levels`, `evidence_summary` counts, `available_sections` flags, trust), which
  describe the approved record's own provenance. Release, archive and export copy raw provenance tables and are unchanged.

## Releases can select and ship transition-state-owned statmech (2026-10-12)

**Release artifact content change; no API schema change.** Migration: none. New environment variable: none. No package
version changes (`tckdb-client`, `tckdb-schemas` and `tckdb-mcp` are unchanged; admin selection still takes a
`record_ref`).

- A statmech record owned by a transition-state entry can now be selected into a dataset release. Before, selecting one
  was refused `record_has_no_subject`; the code stays as an unreachable backstop.
- Release artifacts may now carry `subject_type: "transition_state_entry"` lines for statmech, with a subject identity
  that includes the reaction SMILES. The entry's other statmech records ship as candidates only; releasing a TS entry
  still embeds none of its statmech.
- Species-only releases render byte-identically, and published manifests are untouched.

## Statmech frequencies block lists curvature witnesses (2026-10-12)

**Additive response field.** Migration: none. New environment variable: none. No package version changes
(`tckdb-client`, `tckdb-schemas` and `tckdb-mcp` are unchanged; the client types `frequencies` as a plain dict).

- `GET /scientific/statmech/{ref}?include=frequencies` (and the species and search reads that expand it) gains
  `frequencies.curvature_witnesses`: one entry per (link role, calculation) pair whose calculation stores a frequency result
  or a Hessian, whatever the role or job type: `{calculation_ref, role, calculation_type, has_frequency_result, has_hessian}`.
  Before this, an optimisation or composite job carrying curvature was invisible to a reader.
- Unchanged, byte for byte: `source_freq_calculation_refs` / `_ids` (still `freq`-role links only),
  `available_sections.has_frequencies` and `evidence_summary.has_freq_calculation` (link-presence facts, not eligibility),
  and the `has_freq_calculation` search filter.
- A presence fact only. No eligibility or "selected" field, no witness ranked or picked, and a calculation the record does not
  link is never borrowed. Qualification stays with `POST /scientific/statmech/select`.
- Witnesses follow the read profile's calculation visibility: under `profile=curated` a linked calculation below `approved` is not listed.

## Statmech search refuses a key it does not declare; the statmech MCP tools reach every filter (2026-10-12)

**Narrowing: `/scientific/statmech/search` used to ignore an unrecognised key and now refuses it.** Migration: none. New environment
variable: none. `tckdb-mcp` 0.9.0. `tckdb-client` 0.148.0 (gains the `UNKNOWN_QUERY_PARAMETER` rejection code) and `tckdb-schemas` 0.122.0 (the refusal-code reference lists it); neither sent an undeclared key.

- POST: a body key the request model does not declare is a 422 `extra_forbidden`. GET: an unrecognised query key is a 422
  `unknown_query_parameter`. Before, both returned 200 and dropped the key, so a misspelled filter (`has_torsion`) silently
  broadened the result. A caller that was sending an ignored key must stop sending it. The OpenAPI golden records
  `additionalProperties: false` on the request.
- MCP: `tckdb_statmech_search` takes every filter the backend takes, as a discriminator on its own, an explicit `false`
  included, plus `transition_state_entry_ref` and `declared_conformer_group_ref`. Both statmech MCP tools accept
  `electronic_levels` and an explicitly named `trust` (`all` never expands to it). `include=assessments` is refused with its own
  message.

## Statmech search filters by transition-state entry and by declared basin (2026-10-12)

**Fresh behaviour change: a filter that used to be ignored is now applied.** Migration: none. New environment variable: none.
`tckdb-client` 0.147.0. `tckdb-schemas` and `tckdb-mcp` are unchanged.

- `transition_state_entry_ref` on `/scientific/statmech/search` (GET query or POST body) used to be silently dropped, so a caller
  already sending it got unfiltered results. It now returns only records owned by that transition-state entry. Alone it is a
  meaningful filter (it was a 422 `missing_filter`). A species-axis filter beside it ANDs into an empty page. A malformed or
  wrong-prefix ref is a 422; a well-formed unknown ref is an empty page, like every other filter on this endpoint.
- New `declared_conformer_group_ref` matches only records whose own model declaration is scoped to exactly that basin. It never
  matches an undeclared record, a record only by its owning entry, or an ensemble that merely contains the basin as a member.
- `conformer_group_ref` and `conformer_observation_ref` keep their meaning (every record of the owning species entry).
- Client: `search_statmech` takes both new arguments; `StatmechRecord.species` is now optional (it is `null` on a
  transition-state-owned record) and `transition_state`, `supersession` and `levels` are typed.

## A geometry and a conformer may not disagree (2026-10-12)

**Narrowing: payloads previously accepted are now refused.** Migration: none. New environment variable: none.
`tckdb-schemas` 0.121.0, `tckdb-client` 0.146.0. Closes #635 (items 1, 2 and 4 were already done in #756).

Four new 422 codes, on `/uploads/computed-reaction` and `/uploads/networks/pdep` unless noted:

- `calculation_geometry_conformer_mismatch`: a species calculation whose `conformer_key` and `geometry_key` belong to different
  conformers, and a conformer's own (primary) calculation that states a `conformer_key` or `geometry_key` other than its own
  conformer's. Both used to be accepted; the first anchored to `conformer_key`, the second stored the output on another
  conformer's geometry row or ignored the key.
- `transition_state_calculation_conformer_key`: a `conformer_key` on a transition state's calculation (it was silently dropped).
- `transition_state_calculation_species_geometry`: a transition-state calculation whose `geometry_key` names a species conformer's geometry.
- `calculation_input_geometry_mismatch` (also `/uploads/computed-species`, `/uploads/conformers`, `/uploads/transition-states`): a `freq` or `sp`
  whose explicit `input_geometries` omit the geometry it states (`geometry_key`, or its conformer's or saddle point's geometry). An `opt`
  is exempt. Only coordinate rounding and a rigid move or reordering of the same structure are forgiven: send the conformer's own xyz as
  the input geometry, not coordinates a program re-printed or symmetrised.

Consistent payloads, a lone key, and a `geometry_key` naming no conformer's geometry (still `geometry_key_unresolved`) are unchanged.

## Honest echo and supersession notice on the statmech reads (2026-10-12)

**Chunk 6.1 of the statmech programme.** Migration: none. New environment variable: none. No package version changes
(`tckdb-schemas`, `tckdb-client`, `tckdb-mcp` untouched; the client's `ScientificRequestEcho` is a `TypedDict(total=False)` that does not name the new keys, so no client change is needed).

- `GET /scientific/species-entries/{id}/statmech` now carries the `supersession` notice on every record, as detail and
  search already do. It was `null` for a superseded record.
- Both that read and `POST|GET /scientific/statmech/search` add `request.effective_order` (keys and directions that
  actually ordered the result), `request.selection_policy_applied` (`true` for `collapse=first`, `false` for
  `collapse=all`, `null` on search) and `request.review_visibility` (visible and excluded statuses and whether the
  read-profile floor applied), including on empty pages. `request.filter` still echoes what was requested.
- Additive only: `sort` is unchanged and the actual orders are unchanged. `include=trust` on the species read now
  batches its eager-loads like search.

## Statmech selection endpoint, client method and MCP tool (2026-10-11)

**Chunk 5 of the statmech selection programme: the public surface.** Migration: none. New environment variable: none.
`tckdb-schemas` 0.120.0 (contract route rows and one refusal code), `tckdb-client` 0.145.0, `tckdb-mcp` 0.8.0.

- `POST /api/v1/scientific/statmech/select` and `.../select/manifest`: one typed target union (a species entry or a
  transition-state entry), both on the read-only REPEATABLE READ snapshot session. A valid negative outcome is a 200; a bound
  exceeded is the coded 422 `statmech_selection_bound_exceeded`, never a prefix; an unknown or hidden ref is one 404, and a visible scope ref that belongs to another entry is the coded 422 `statmech_selection_scope_owner_mismatch`.
  Public refs only (`smd_`/`sm_` for candidates), no database id in a response or an error. The response surfaces each
  candidate's `advisory` and the decision `notices`. No computed value is returned, and this release ships no active rule.
- Disclosure: a ref the read profile hides (a target, basin, determination, ensemble declaration, reference model or candidate)
  answers exactly as a ref that names nothing (`handle_not_found` / `unknown_statmech_candidate_ref`, same body up to the ref the
  caller supplied); a ref the caller may see but that belongs to another entry is the coded 422
  `statmech_selection_scope_owner_mismatch` (`context.field` names the ref, the other owner is never named). The shared
  curated-floor 404 now echoes the supplied ref like an unknown ref's, on every route that uses it.
- `TCKDBClient.select_statmech_models` / `get_statmech_selection_manifest` and the MCP tool `tckdb_select_statmech_models`
  mirror the other selectors (refuse an id, a wrong-kind ref, a bound, a page, a sort or a rule before any request).
- Selected export and releases: the other selectors' decisions are not part of a release or an export (only network
  kinetics has an `export-selected`, and it serialises numbers; statmech returns none), so statmech decisions follow the
  thermo, kinetics and structure precedent: select plus manifest. Declared models already ship with their statmech record in
  releases (chunk 2). See `docs/guides/selecting_statmech_models.md`.

## Statmech decision and replay (2026-10-11)

**Chunk 4 of the statmech selection programme: decision and replay, no public surface yet.** Migration: none. New
environment variable: none. No wire, contract or client change (so no version bump).

- An internal decision stage over the qualification: `qualify` selects nothing; `protocol_preferred` compares only a decided
  population, through the shared graph kernel (rule supersession before opposition, conflicts, fronts) over whole models,
  and ships **zero** rules, so every comparison ends `incomparable_alternatives` (or `sole_eligible_candidate`) until an
  audited rule is activated. A pair a rule might rank but cannot (unknown prerequisite) is `unresolved_comparability`.
- A replayable manifest (no database ids) with three offline operations: reproduce the recorded ordering, reassess the
  captured inputs, or replay the whole decision; a self-inconsistent manifest or an unsupported version is refused.
- Replacing a statmech record now needs the same subject (a transition-state record was compared by its empty species
  column only) and, where both declare a model, the same declared scope.

## Export: transport linearity from a linked witness (2026-10-11)

**Changes the bulk-export output (NDJSON and CHEMKIN), no wire-version bump.** Migration: none. New environment variable: none.

- The geometry index of a CHEMKIN `tran.dat` line (and NDJSON `is_linear`) no longer comes from the lowest-id statmech record of the
  species. It comes from a record the selected transport links to: the geometry of a calculation it cites (an `opt`'s final output,
  or a `freq`/`sp` input), then a linked `opt`/`freq` statmech. The atom count is decisive for one and two atoms (0 and 1).
- NDJSON species rows gain `is_linear_source` (`{kind, ref}` or null) and `is_linear_conflict` (`{kind, refs}` or null).
  `is_linear` is null when the species has no selected transport.
- `tran.dat` lines gain a trailing `linearity=<kind>:<ref>` comment. Three or more atoms with no witness are written as nonlinear
  (2) with `linearity=assumed nonlinear from atom count`; disagreeing witnesses are written the same way and name the refs. Each
  assumed value, each disagreement and each witness the atom count overrode is an export gap of kind `transport`.
- A species whose SMILES cannot be parsed has its `tran.dat` line dropped, with a gap; it is never written as 0.

## Statmech qualification (2026-10-11)

**Chunk 3 of the statmech selection programme: bounded qualification, no public surface yet.** Migration: none. New
environment variable: none. `tckdb-client` 0.141.0 (three new rejection codes), `tckdb-schemas` 0.114.0.

- An internal service decides, per candidate model (a declared model or a legacy statmech record read as stored), whether it can
  answer a stated question about one species or transition state, with a closed reason registry and an outcome precedence
  (evidence conflict, unsupported model, missing data, unresolved, none applicable). Undecided alternatives stop the whole requested
  domain being certified. The read runs in one read-only repeatable-read snapshot and is bounded (500 candidates, 25,000 nested
  rows). Nothing is exposed over HTTP yet.

## Declared statistical models for statmech records (2026-10-11)

**Chunk 2 of the statmech selection programme: durable declarations.** Migration: `e6b3a9d1f742` (additive; chains from
`d3a8f6c1b952`). New environment variable: none.

- A statmech record can declare the model it supplies: scope (atom, basin, explicit equilibrium ensemble, or a transition-state
  determination), attribution, recipe, ensemble members and sources. New tables `statmech_model_declaration`,
  `statmech_model_member`, `statmech_model_source`, one declaration per record. Optional on every statmech upload path; a record
  deposited without one reads as it always did, nothing is inferred and nothing is backfilled.
- The declaring record's accepted-science freeze covers its declaration, members and sources. Owner rules (basin, determination,
  member, source calculation and geometry) are database facts. Archive and release carry the new tables.
- `tckdb-schemas` 0.113.0, `tckdb-client` 0.140.0 (new rejection codes).

## Transition-state badge: undesignated imaginary modes warning (rubric version 4) (2026-10-19)

Closes #729. Migration: none. New environment variable: none. API shape: unchanged (a new key in the existing
`trust.evidence.checks` map).

- **New warning `no_frequency_result_has_undesignated_imaginary_modes`.** Zero weight, built like
  `no_frequency_result_lacks_an_imaginary_mode`. It fires when any attached frequency result with a recorded count reports two or
  more imaginary modes and does not designate the reaction coordinate; a null count is unknown and neither rescues nor condemns. A
  warning means that attached result needs investigation, not that the transition state is disproved; it never changes the label,
  the completeness counts or `is_certified`. The required check `reaction_coordinate_designated_for_ts` is unchanged.
- **`computed_transition_state` is version 4.** Reviewer-family machine reviews stamped version 3 read as stale
  (`rubric_versions_mismatch`) and are re-planned; no stored review is rewritten.
- **Transition-state page.** Warnings are now listed in words under the Trust fact, each ending "needs investigation"; an
  unrecognised warning key shows as its raw token. The line "not a saddle point at this level" for a recorded zero-mode result is
  now "recorded at this level — needs investigation": a recorded count is evidence to investigate, never a disproof.

## Trust-contract correction, inspection-filter correction and the structure coverage inventory (2026-10-06)

**Chunk 6 of 6 of calculation, conformer and transition-state energy selection: legacy corrections.** This changes live answers
and is kept separate from the selection work so it can be judged on its own. Migration: none. New environment variable: none.

- **Trust contract version 2.** An automated geometry-validation `fail` no longer hard-fails a record; it is a warning on the
  geometry check. The transition state badge issues no frequency verdict (it cannot pin the declared target a saddle claim
  is about); a new warning `no_frequency_result_lacks_an_imaginary_mode` keeps a recorded zero-mode result visible, and the scoped
  judgement is the structure assessment's. `computed_calculation`, `computed_kinetics`, `computed_thermo`, `computed_statmech` and
  `computed_transport` are version 2; `computed_transition_state` is version 3. Reviewer-family machine reviews made under the
  previous versions read as stale (`rubric_versions_mismatch`, usually with `context_hash_mismatch` too) and are re-planned; no stored review is rewritten.
- **Selection consumers.** H298 and kinetics selection consume the evidence hard-fail, so a record whose only flaw was an
  automated geometry fail is now eligible there. Both also read live, supported structure findings about a record's own source
  calculations (scoped by subject and role; only an authorized same-subject adjudication settles one), so a confirmed
  invalidation excludes the record. Changed live answers include two records that both became eligible and now read
  `incomparable_alternatives` where the flagged one used to be excluded. The decision-procedure `policy` versions are unchanged;
  assessment semantics are versioned separately (`assessment_semantics`, version 2, in each manifest; older manifests are
  `pre_v2_assessment` and replay as historical).
- **Inspection filter.** On the species calculation search, `calculation_quality=rejected` now also requires
  `include_rejected_quality=true`, as on the generic calculation search; without it the page is empty.
- **Legacy TS browse projection.** `saddle_point` carries `basis: "latest_direct_frequency"`: it is the latest directly attached
  frequency result, not validated saddle evidence.
- **Read-only inventory.** `scripts/ops/structure_selection_coverage_inventory.py` counts which recipe, geometry, grouping,
  validation and coverage facts structure selection would find unstated. It names its database, runs READ ONLY and backfills nothing.

## Structure rule audit: the ACONFL example registered inactive (2026-10-05)

**Chunk 4 of 6 of calculation, conformer and transition-state energy selection: rule audit.** The structure rule registry now
carries three audited candidates, loaded only against the pinned SHA-256 of `structure_rule_candidates.yaml` and registered
**inactive**: two derived from the council's relative-conformer-energy example (Santra and Martin, J. Phys. Chem. A 126, 9375
(2022), open access) and the one proposed form with no source (controlled convergence on one fixed model). No rule is active, no
decision changes, and the contract is unchanged. Migration: none.

- **What the audit found.** The ACONF16 and ACONF20 reference is MP2-F12/CBS plus the LNO-CCSD(T)/vTight/AV{Q,5}Z correction, the
  protocol one rule prefers, so only the 12 ACONF12 conformers are an independent comparison; the article states no numeric
  reference uncertainty (the revision moved energies by up to +0.60/-0.67 kcal/mol; MAD 0.31 between old and new references);
  every figure is a set MAD, not a per-conformer error; core treatment, included corrections and the geometry level are not
  stated; and a relative conformer energy against a named reference conformer (all-trans for two sets, the hairpin for the third)
  has no intent in structure selection yet. Each blocker is named in the manifest with the paper, table or data that would be
  needed.
- **Provenance.** Sources are pinned by SHA-256 with a `used_for` line and page, table or figure anchors for every number. The
  published PDF could not be downloaded (bot challenge); the published text was read from Europe PMC's open-access full text, page
  anchors are those of arXiv v2, and the Supporting Information PDF was read for Table S1 only. The per-conformer spreadsheets in the
  Supporting Information were located, not extracted.
- **Not rules.** The thermochemistry-decomposition, GOAT-ensemble and TS mode/IRC examples are recorded as considered and
  deliberately not preference rules.
- Approval in the manifest alone applies no edge until the predicates are written (`RULES_WITH_IMPLEMENTED_PREDICATES`, empty);
  a model-fidelity rule needs a pinned reference model to be active.

## Structure decision and replay: cohorts, numerical order, protocol preference (2026-10-05)

**Chunk 3 of 6 of calculation, conformer and transition-state energy selection: decision and replay.** On top of the assessment, a
pure decision stage (`app.services.structure_selection.decide_structures`, entry point `select_entry_structures`) and a replayable
manifest with two-level replay. No route serves it (chunk 5), nothing is persisted, and no literature rule is active. Migration:
none. Contract: 679,085 bytes against the 700,000 ceiling (one new refusal code).

- **A cohort is the unit of numerical comparison.** Units are ordered by value only inside one cohort (same established actual recipe,
  quantity, energy convention, and scope family: an unconverged optimisation's intermediate value is its own cohort). Several cohorts
  each return a conditional minimum and the outcome is `incomparable_alternatives` (also when one established cohort cannot be
  ordered: a single cohort is never named the winner because another is unorderable): total energies of different protocols are not on
  one scale, so there is never an overall winner by absolute energy. Values compare exactly: ties keep every tied reference, there is
  no tolerance and no chain of "near".
- **Outcomes** (13). `no_candidates`, `energy_unavailable`, `unresolved_comparability`, `no_applicable_candidate`, `recorded_minimum`,
  `representative_minimum`, `qualified_evidence`, `validated_corpus_minimum`, `policy_preferred`, `sole_eligible_candidate`, `incomparable_alternatives`,
  `policy_conflict`, `evidence_conflict`. A complete claim (`all_requested_members`) needs every requested member eligible and in one
  cohort; the result always says its coverage is the caller's authorized population, not a search certificate.
- **Repeats and contradictions.** Determinations of one target are alternates, not independent confirmation. If their values differ
  exactly the cohort is not ordered unless the request names the administrative-representative policy (then the first repeat in the
  administrative order stands for its target and the outcome is `representative_minimum` with `representative_minimum_hartree`, never
  the minimum of all stored values). A target with a passing and a refuted determination is
  contested: nothing of it certifies.
- **Administrative key** (versioned, echoed): review rank, permitted-quality rank, creation time descending, ordinal descending;
  `latest` and `earliest` by time and ordinal. It sorts within a scientific front or among exact ties only. The shared kernel gains an
  optional adapter-supplied `admin_key` on `AdminNode`; thermo, kinetics and network adapters pass none and their goldens are
  unchanged.
- **Protocol preference** (`protocol_preferred`) states its objective (`physical_accuracy`, `expected_accuracy` or `model_fidelity`
  with a pinned reference model); candidates are whole protocols that cover every requested target (no mixed profile is invented);
  edges come only from audited rules through the shared graph kernel, a rule applies only under its own objective and reference
  model, and sole eligibility is `sole_eligible_candidate`, never superiority. The rule registry exists with none active; an active
  rule must name its objective and pin an audited manifest digest.
- **Manifest and replay.** The manifest carries the normalised request, read profile, every normalised unit and source calculation,
  each assessment and the decision, by public ref and non-reversible ordinal (no database id). `replay_structure_assessment`
  recomputes every assessment from the normalised inputs and refuses on any difference; `replay_structure_decision` then decides
  over the *recomputed* assessments. Unknown manifest, semantic or rule versions, a failed checksum, a profile or review status the
  request could not see, an unavailable or changed rule: all refused. **The digests are checksums, not signatures**: they detect
  edits and anyone who edits a manifest can recompute them, so replay reproduces reasoning from captured inputs and does not
  authenticate scientific truth or prove a forged population was complete. A manifest over 10 MiB is refused whole
  (`structure_selection_manifest_too_large`).
- `tckdb-schemas` 0.107.0 and `tckdb-client` 0.138.0 add the one refusal code.

## Structure assessment: which stored energies can answer a stated question (2026-10-05)

**Chunk 2 of 6 of calculation, conformer and transition-state energy selection: snapshot and assessment.** A service layer
(`app.services.structure_selection`) that decides, for each calculation, basin determination or saddle determination of one entry,
whether it can supply a requested energy and, where asked, a requested structural claim. It reads one read-only REPEATABLE READ
snapshot and returns assessments. Nothing is ordered, ranked or chosen yet (chunk 3), no route serves it (chunk 5), and nothing is
written. Migration: none.

- **Verdicts.** `applicable`, `incompatible`, `unresolved` or `unsupported`, with every finding, split into blocking, advisory and
  comparative-unknown. A missing energy is unavailable, never zero. A known failure blocks only the claim it invalidates (an
  unconverged optimisation keeps a labelled endpoint value but cannot support an optimised minimum).
- **The recipe is normalised, never assumed.** The level of theory and the depositor's `actual_protocol_declaration` combine into
  established / contradicted / unestablished facts. Equal nulls do not prove equivalence: a unit comparable with others needs every
  required fact established (`recipe.cohort_key`); an undeclared unit is still reported on its own. Software identity does not divide
  a cohort in normaliser version 1 (disclosed, not hidden).
- **Curvature belongs to a geometry.** A frequency result supports a minimum or saddle claim only on the geometry the determination
  evaluates, or on its optimisation's output, and only through the owner's persisted judgement (count, structural flag, designated
  reaction coordinate) re-evaluated by the shared stationary-point owner (ADR 0012). Imaginary modes are judged by magnitude, not counted: a minimum claim is
  supported by no imaginary mode or by every one below the stored tau (however many; the owner's count-based findings are
  advisories), contradicted only by a mode at or above tau, and unresolved when modes are counted but no magnitude is stored; a
  transition state's extra modes follow tau and their declared dispositions; nothing is borrowed
  from another geometry or observation, and a stored Hessian that was never evaluated does not certify (the matrix itself is never
  loaded). Every structure-consuming intent defaults to its grain's conventional characterization (a minimum, or a first-order
  saddle for transition-state use). A lower-level characterisation of a higher-level energy states its own surface.
- **Findings** are read at their own subject and role: a failed rerun on another geometry does not erase an older bundle, a live
  confirmed disproof blocks, only an authorized adjudication about the same subject settles a finding (a producer's assertion, another
  kind of finding, or an adjudication about a different subject never does; the database refuses a non-adjudication that names
  one), and an unknown kind or version is disclosed, never a universal failure.
- **Visibility and bounds.** Review floors apply at the grain that owns each record (a group's approval does not approve its observations).
  A record the read profile hides is dropped without trace, a hidden pinned calculation reads as one generic code, and hidden entries
  and members behave exactly like missing ones; that covers lineage, validation evidence, observation refs, counts and the depth
  refusal. The entry's own floor applies at the calculation grain too. A shared parent under two dependency paths is a diamond, not
  a cycle. Population (500), nested rows (5,000) and dependency depth (8) are counted over the
  whole authorized population and refuse with coded 422s rather than taking a prefix.
- `tckdb-schemas` 0.106.0 and `tckdb-client` 0.137.0 add five refusal codes. A deferred quantity (enthalpy, Gibbs energy, barrier, rate, ...) is a structured `structure_selection_unsupported` refusal. Producer contract: 679,047 bytes against the 700,000 ceiling.

## Actual protocols and structure determinations (2026-10-05)

**Chunk 1 of 6 of calculation, conformer and transition-state energy selection: contracts and metadata.** A calculation can
declare the recipe it actually ran, and a conformer or transition-state upload can declare structure determinations.
Declarations only: nothing is selected, ordered or read differently, and every claim is stored as made, never inferred and
never backfilled (a calculation deposited without one reads "not stated"). Migration `d3a8f6c1b952` (additive, no data step).

- **`actual_protocol_declaration`** on every calculation block (version 1): electronic state and root, spin and
  relativistic treatment, effective core potential, core correlation, auxiliary basis, dispersion, solvation, constraints,
  material numerical approximations and the corrections the number includes. Each single fact is `known`, `unknown` or
  `not_applicable`; a fact left out is not stated, never read as a default or as gas phase. Read back on the calculation
  detail as `provenance.actual_protocol_declaration`.
- **`structure_determinations`** on conformer and transition-state uploads and on the computed-species and
  computed-reaction bundles (each conformer, and the reaction bundle's transition state): a source-attributed claim about a
  geometry, conformer basin or saddle point that pins the upload's own calculations (by `key`, or an earlier `calc_` ref) to
  the roles energy, geometry optimization, curvature, correction, connectivity and alternative characterization.
  Immutable. **The key is an identifier**: the owner (a basin's observation too), the source and the key name one
  determination; stating it again with the same content resolves to the existing one (no Idempotency-Key needed), with
  different content it is refused (`structure_determination_mismatch`, reason `content`). A basin is per observation, so it
  cannot be restated across uploads. The pinned calculations must belong to the determination's own species entry or
  transition state entry and, for a basin, to its observation (composite foreign keys); they are fixed when the determination
  is created. Once the transition state entry or conformer observation is accepted, nothing can be added to, changed on or
  removed from its determinations (the shared accepted-science guards; TRUNCATE is refused). Transition-state calculations
  accept an optional `key` for this.
- **Findings table** (`structure_evidence_finding`, append-only) exists for confirmed identity, state or path
  incompatibility and adjudication; nothing writes it yet.
- Released transition-state entries ship their determinations and cite what they pin (with each calculation's declared
  actual protocol); the new tables are in the archive registry; the new calculation column is out of the review digests while
  NULL, so no stored review goes stale.
- `tckdb-schemas` 0.105.0 adds the models, enums and four refusal codes; `tckdb-client` 0.136.0 adds
  `Calculation(..., actual_protocol_declaration=...)`. See `docs/guides/declaring_structure_evidence.md`. Producer contract:
  676,025 bytes against the 700,000 ceiling.

## Network selected export (2026-10-04)

**Chunk 6 of 6 of pressure-dependent network selection: serialise a verified selection.** `POST
/api/v1/scientific/networks/{network_ref}/kinetics/export-selected` takes the decision manifest a caller saved from
`.../kinetics/select/manifest`, the node it chose and exactly one representation for every member of that node, and
returns the native serialisation (solve, determination and representation provenance, directed channel endpoints and
the stored numbers) or a forward-only CHEMKIN `chem.inp`. Read-only.

- **The manifest is checked against the server, not its digest.** The server requires it complete and for this network,
  replays it at both levels from its own captured inputs, then re-runs the selection from its request under one
  read-only REPEATABLE READ snapshot (opened before the ref is resolved) and requires the scientific content, rules,
  read profile and population to be identical. A change is `network_export_manifest_stale` and needs a fresh selection;
  an old decision is never silently updated.
- **The choice must be one the decision allows.** The selected node when the selection chose one; an unranked
  leading-front node only with `allow_administrative_choice` (false by default); nothing from a conflict or an empty
  selection, whatever the flag. Exactly one eligible representation per member: alternatives are never added alongside
  the choice and `DUPLICATE` is never written.
- **No derived reverse.** CHEMKIN is `=>` only and carries no thermodynamics, so no reverse or equilibrium assumption is
  made; the output records both. Forms with no CHEMKIN serialisation (a tabulated fit, a Chebyshev not stored as log10 k,
  a missing mapping domain, a species without a composition) and two chosen channels sharing one equation are a structured
  `network_export_unsupported_form` naming each; a native export keeps every form and reports equation collisions.
- **Reported rates are disclosed (ADR 0010).** A `reported` solve reaches CHEMKIN only with `include_reported`, annotated
  with its literature; native output carries `solve_kind` and `literature_ref`.
- **Numbers are pinned and the server's bounds are used.** Selection manifests are now format 2: each captured fit carries a
  digest of its stored numbers, so an in-place coefficient edit makes a saved manifest stale (a format-1 manifest is refused as
  invalid). A manifest naming other bounds is refused and the live selection always uses the server's. The response labels the
  caller's digest apart from the server-derived one, the request body is capped before parsing (413
  `network_export_body_too_large`), exports are logged, and `energy_units` is a closed set.
- `tckdb-client` 0.135.0 adds `export_selected_network_kinetics`, `tckdb-mcp` 0.6.0 adds
  `tckdb_export_selected_network_kinetics`, and `tckdb-schemas` 0.104.0 adds the five refusal codes and the contract route
  row. See `docs/guides/selecting_network_kinetics.md`.

## Network selection endpoints, client method and MCP tool (2026-10-04)

**Chunk 5 of 6 of pressure-dependent network selection: the public face.** `POST
/api/v1/scientific/networks/{network_ref}/kinetics/select` and `.../select/manifest` answer one stated gas-phase
rate-coefficient question about a network (state partition, boundaries, regime, bath, temperature and pressure
windows, coefficient basis, channel or bundle of outputs, comparison objective) from its stored pressure-dependent
solves, and say why. Read-only; the network browse and evaluation endpoints are unchanged.

- The path takes a public `net_` ref only (an integer id is refused); `channel_key` is a body field, never a path
  segment. The read runs in a read-only REPEATABLE READ snapshot opened before the ref is resolved, and the service
  insists on it. Bounds are the server's: over one the answer is the 422 `network_selection_population_too_large` or
  `network_selection_snapshot_too_large` (for example more than 200 required outputs), and nothing is assessed.
- `method_preferred` applies the registry, in which no rule is active, so it ranks nothing and says so; the other
  policies apply no rule. The route, not the engine, sets `apply_rules` from the policy.
- The manifest replays at two levels with no database and carries a digest that is re-sealed after any redaction. Under
  the `curated` profile solves below the floor are neither listed nor counted.
- `tckdb-client` 0.134.0 adds `select_network_kinetics` and `get_network_kinetics_selection_manifest`; `tckdb-mcp`
  0.5.0 adds `tckdb_select_network_kinetics`. `tckdb-schemas` 0.103.0 only lists the two new read-only routes in the producer contract. See
  `docs/guides/selecting_network_kinetics.md`.

## Network coverage inventory (2026-10-04)

**The read-only coverage inventory of the network-selection plan, as a script.**
`backend/scripts/ops/network_selection_coverage_inventory.py` prints, from a database the operator names
(`--database-url`, or on purpose `--use-configured-database`; with neither it prints usage and exits 2 without
connecting), inside one read-only REPEATABLE READ snapshot, how many stored solves, fits and determinations state or
leave unstated their target, grouping, domain and protocol, how many solves are bundle or full-network ready, and the
facts an assessment would find missing. Every known category is reported, with zero when nothing was found. It writes
nothing and infers nothing; an unreadable stored claim is counted as unreadable, a determination with no fit has its
own category, and the per-determination fit counts sum to the total. It is tested on synthetic data only and is not
run against any deployed database by this repository. The selection-aware export endpoint is not part of this change
(it needs the public selection endpoints).

## Network rule audit: the council examples, registered inactive (2026-10-04)

**Chunk 4 of 6 of pressure-dependent network selection.** Six candidate rules (the Amedro, Johnson/Green and
Jasper/Miller examples, the last split into a model-fidelity and a physical-accuracy rule, a same-model convergence
rule and a held-out representation rule) are registered **inactive** in
`app/chemistry/network_rules/network_rule_candidates.yaml`, loaded only against its pinned SHA-256, each with its
specific blockers and exactly which papers, tables and data would have to be supplied. The Amedro entry is anchored to
the open-access article and supplement, pinned by SHA-256. No paywalled paper was downloaded, and nothing was
activated: activation needs the owner's dated acceptance in that file and the rule's predicates, which do not exist
and are enabled rule by rule. The held-out rule is registered at the representation level, so it can never rank
solves. The API now builds the registry at startup, so a bad pin fails the deploy. See
`docs/guides/network_selection_rules.md`.

## Network preference engine and two-level replay (2026-10-04)

**Chunk 3 of 6 of pressure-dependent network selection: the engine that turns eligible candidates into an honest
outcome, and a manifest that replays at two levels.** No endpoint, SDK or MCP tool yet, and **no rule is active**: the
shipped registry is empty, so a decision states which candidates are eligible and why none is ranked.

- `app/services/network_selection/{rules,engine,manifest,selection}.py`, on the existing `selection_kernel`
  (unchanged; the H298 golden and the elementary-kinetics tests pass untouched). Rules are judged on every member of a
  candidate, only for a request that states their objective (physical accuracy, model fidelity or representation
  fidelity), and edges compose only under one objective key. Opposite advantages on different channels, unknown
  prerequisites, nodes answering different outputs and nodes with nothing to judge make no edge. Alternate fits get
  nested fronts that rank no solve and are never independent confirmation.
- `replay_network_assessment` recomputes every verdict from the captured inputs; `replay_network_decision` recomputes
  edges, conflicts, fronts and the selection from those recomputed verdicts, never from recorded eligibility flags;
  `replay_network` does both and compares the recorded outcome as well as the decision. A policy, assessment, bounds,
  declaration or rule version, status or audited manifest that the running code does not carry refuses the replay.
  Replay reproduces reasoning; it does not authenticate the inputs, and the manifest says so.

## Network selection snapshot and assessment (2026-10-04)

**Chunk 2 of 6 of pressure-dependent network selection: a bounded, consistent snapshot of one network's authorized population, and a deterministic applicability assessment of its determinations (one channel) or declared product sets (a requested bundle).** There is no ranking, rule, endpoint, SDK or MCP tool yet, and nothing is written.

- `app/services/network_selection/`: versioned engineering bounds (200 solves, 2,000 fits, 500 channel nodes, 500 bundle nodes, 1,000 states, 2,000 channels, 200 required outputs, 10,000 evidence entries, 100,000 numeric cells, 8 MiB snapshot) counted over the complete authorized population before any applicability filtering; over any bound the decision is refused (`network_selection_population_too_large`, `network_selection_snapshot_too_large`), never made on a prefix, and a refusal never counts or names what the read profile hides.
- The snapshot opens before the network ref is resolved. Review qualification is the solve's and is never relaxed.
- Assessment follows the plan's order and returns every reason: identity and direction, observable and basis, partition, boundary and regime, units and order, determination and representation roles, whole-request coverage as the intersection of solve scope, representation support and declared physical validity, bath, and integrity. Unknown is never a default; tabulated intervals, additive components and composition-dependent baths are unsupported; unknown protocol evidence is disclosed and does not exclude.

## Network solve declarations and fit determinations (2026-10-04)

**Chunk 1 of 6 of pressure-dependent network selection: a solve can state what its outputs are outputs of, and a
fit which determination it represents.** There is no selection, endpoint, SDK or MCP tool yet, and no rule.

- Additive revision `e5b9c2a7d4f1`: `network_kinetics_determination` (public ref prefix `nkdet`, immutable from
  creation, an ownership child of its solve), three nullable declaration columns on `network_solve` (`target`,
  `protocol`, `validation`) and three nullable grouping columns on `network_kinetics`. No backfill: every earlier
  row reads `NULL`, which means "not stated".
- `POST /uploads/networks/pdep` accepts the optional `solve.target`, `solve.protocol`, `solve.validation` and, per
  fit, `determination` and `representation`; alternate same-kind fits are allowed only when each declares its own
  keys. New codes `network_declaration_invalid` and `network_declaration_version_unsupported`.
- The solve and fit detail reads serve the declarations as stored (`null` is "not stated"; an unparseable stored
  claim reads `null` with `declarations_unreadable`). A release ships the determinations without their identity
  digest. A solve's reproducibility digest changes only when it has declarations.
- The contribution-bundle v0 format does not carry network solves, so there is no bundle path to extend.
- Impact analysis: `NetworkSolve` CRITICAL (274 dependents, additive nullable columns only; the index was 18
  commits behind), `NetworkKineticsIn` MEDIUM.

## TCKDB is the Theoretical Chemical Kinetics Database (2026-10-04)

- `CITATION.cff`, the API description, the documentation site description and the README now spell TCKDB out as
  the Theoretical Chemical Kinetics Database, the project's original name, which the web interface already used.
  Nothing else changes.

## The XYG3 barrier rule reads a record's declared level (2026-10-04)

- The rule `K-XYG3-B3LYP-BARRIER` (still inactive and unsigned; rule 0.2.0, manifest 0.2.0) takes its method evidence
  from `kinetics.energy_level_of_theory_id`, the record's own declaration. A declaration alone reads `declared`; one
  that a linked or supporting electronic-energy calculation agrees with reads `verified`; one a calculation contradicts
  matches neither side. Calculations without a declaration are not method evidence: they carry no role saying which
  side of the barrier they cover. Nothing is read from a sibling record or inferred from links.
- Excited-state species are no longer put in scope; a manifest passed to the rule is held to the sha256 pin; the
  manifest's `degenerate_identity_reaction` flag is now checked against the species (NHT08 and NHT10 corrected to
  `true`; no number changed).

## A kinetics record can declare its electronic level of theory (2026-10-04)

- `kinetics.energy_level_of_theory_id` (nullable, indexed; revision `a3e7c1d9b542`, no backfill: existing rows stay NULL).
  `/uploads/kinetics` stores the `energy_level_of_theory` it already accepted (it was only used to find source single
  points); a bundle's kinetics (`BundleKineticsIn`, tckdb-schemas 0.98.0) can state it too. A declared level that
  contradicts a linked energy calculation is refused (`kinetics_energy_level_contradiction`, the same tier as
  thermo's). A kinetics bundle import stores the declared level without requiring the exporter's calculations.
- The scientific kinetics read reports it as `levels.declared_energy`, separate from the derived level and never filled
  from a linked calculation. A kinetics bundle export carries it. tckdb-client 0.130.0 (regenerated rejection codes only).

## Kinetics applicability assessment and the shared selection kernel (2026-10-04)

**Change 2 of 4 of kinetics method-aware selection: a service that assesses which stored rates answer a
requested gas-phase rate coefficient, and the graph kernel it shares with the H298 selector.** There is no
endpoint, SDK or MCP tool yet (change 4), and no preference rule (change 3).

- tckdb-backend and tckdb-client 0.129.0 (the regenerated rejection codes only). New
  `app.services.selection_kernel`: conflicts, preference fronts, administrative order and outcomes over refs and
  attributed edges, extracted from the H298 engine. H298 decisions and manifests are byte-identical: a golden
  generated before the extraction pins fifteen scenarios under every administrative policy.
- New `app.services.kinetics_selection`: a loader (one consistent snapshot per reaction entry), an assessor and a
  grouping by determination. Each record is `applicable`, `incompatible`, `unsupported` or `unresolved` with
  reasons; an unstated fact is unknown and never a default, so a record that says less cannot beat one that says
  more. Alternate fits of one determination are one candidate. A visible population over 500 records is refused
  before anything is assessed (`kinetics_selection_population_too_large`, HTTP 422 once routed), never truncated,
  and rows the read profile hides are not counted, listed or named.
- Nothing is written, no migration, no new environment variable, and existing kinetics browsing keeps its order.

## A kinetics record can declare its determination, applicability and protocol (2026-10-04)

Change 1 of 4 of the kinetics applicability and method-aware selection plan: **persist the missing scientific
declarations on kinetics, with upload and read round trips.** Selection, assessment, the rule registry, the
`/kinetics/select` endpoint, the SDK selector and MCP are not in this change.

- tckdb-backend, tckdb-schemas 0.96.0 and tckdb-client 0.128.0. New table `kinetics_determination` (public ref
  `kdet`, immutable from creation, deduplicated on a content hash) and four nullable `kinetics` columns:
  `determination_id`, `representation_role`, `applicability_declaration`, `protocol_declaration`. Migration
  `c7a2e5d9b148`, additive, no backfill; every existing record reads `null` for all four and that means "not
  stated".
- Wire: `determination`, `applicability` and `protocol` on `/uploads/kinetics` and on each fit of
  `/uploads/computed-reaction` (and so in contribution bundles); a bundle fit also takes `direction` (`forward`
  or `net`). Vocabulary and rules are in `schema_spec.md`. An applicability claim that contradicts a stored column
  (direction, pressure context, model kind, third-body flag, order) is refused.
- Reads: `determination`, `applicability`, `protocol` on every kinetics record. Release serialisation embeds the
  determination; the archive carries the table; contribution-bundle export carries what is portable and reports
  the rest as `declaration_pruned`.
- Replacing an accepted kinetics record also checks that both records declare the same target when both declare
  one.
- Fixes #598. `reaction.reversible` is optional on `/uploads/kinetics` and in a network's reactions: omitted means
  not stated, and it is never guessed. The deposit takes the value of the one stored reaction with those
  participants (or of the reaction its transition state or determination anchors it to); when there is nothing to
  inherit, or both twins are stored, it is refused with `reaction_reversible_required` and the producer states it.
  A deposit that makes or hits a graph reaction whose twin (same participants, opposite `reversible`) is stored
  gets a `reaction_reversible_twin` warning on every route that creates reactions (reactions, kinetics, computed
  reactions, bundles, networks, pressure-dependent networks) and in the matching job results. Reaction identity is
  unchanged.
- A determination holds either complete representations or additive components (`kinetics_determination_mismatch`,
  `context.reason` `role`). A declared `reaction_order` is the order of the side its direction names (reactants
  forward, products reverse; a net rate is not checked). Numeric declaration fields are strict (`"2"` is refused).
  An export gives each determination a bundle-local `determination.group` handle (never stored, bundle imports
  only), so two determinations that read alike stay two after a re-import, with every key exactly as stated; the
  export CLI now prints the omissions of a kinetics export.
- Deploy: apply the migration first, then the backend, then clients. Environment variables: none.

## Hessian and scan-point geometries are now checked (2026-10-04)

- tckdb-backend and tckdb-schemas 0.94.0 (changelog and contract note only; no payload field, client
  change, new environment variable or new refusal code): a Hessian's geometry, every scan point's geometry
  and every IRC point's geometry (including the TS-marker point and `both`, which were stored unchecked) are
  now held to the subject their
  calculation is filed under, by the same two checks the input/output geometry links already ran
  (`calculation_geometry_composition_mismatch` and `calculation_geometry_isotope_mismatch`, #680). **A
  wrong-element or wrong-isotope Hessian, scan-point or IRC-point geometry that was previously accepted is now
  refused**, with `field` naming `hessian.geometry`, `scan_result.points[N].geometry` or `irc_result.points[N].geometry`. This matters most
  for the Hessian: `hessian_reanalysis` takes its atomic masses from that geometry, so a deuterated
  Hessian geometry under a protium species used to yield deuterium frequencies on reanalysis with no
  refusal. A Hessian recovered from an uploaded artifact is checked too; there a refusal never fails the
  upload, the Hessian is simply not stored (warning logged). Species stored before #66 with isotope labels
  on `species.smiles` and no `isotope_key` are now read through that SMILES, and so does the Hessian reanalysis protium check, as the consistency checks
  already did. Existing stored data is unaffected; only uploads are judged.

## Method-aware kinetics selection endpoint (2026-10-04)

- tckdb-backend, tckdb-client 0.131.0, tckdb-schemas 0.100.0 and tckdb-mcp 0.4.0: a read-only
  `POST /api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select` assesses every visible kinetics
  record of a reaction entry against one stated gas-phase rate-coefficient question (direction, target, coefficient
  basis, temperature window, pressure, collider) and returns the outcome (`policy_preferred`,
  `incomparable_alternatives`, `sole_eligible_candidate`, `no_applicable_candidate`, or `policy_conflict`) with the
  assessments, determinations, preference relations, ordered fronts, rule versions and disclosures.
  `/kinetics/select/manifest` downloads the replayable decision manifest. The read runs in a read-only REPEATABLE READ
  snapshot opened before the route body (`get_snapshot_db`). More than 500 visible records is a 422
  (`kinetics_selection_population_too_large`). Records below the review floor are listed only when the read profile
  has no floor of its own. The client gains `select_reaction_kinetics` and the MCP server
  `tckdb_select_reaction_entry_kinetics`. The application now also builds the kinetics rule registry at startup, so a
  bad manifest pin fails the deploy. **Every rule in this release is inactive** (the XYG3 versus B3LYP barrier rule is
  audited and shipped inactive with its blockers recorded), so `method_preferred` ranks nothing yet. Browse order is
  unchanged. Fourth of four changes toward method-aware kinetics selection. No schema change, no new environment
  variable, no new refusal code. OpenAPI: the shared `KineticsDeterminationTargetKind` enum is now one component
  (previously `KineticsDeterminationTargetKind-Input`). A read-only coverage inventory
  (`backend/scripts/ops/kinetics_selection_coverage_inventory.py`; it and the H298 inventory now refuse to run
  unless told which database to read, `--database-url` or `--use-configured-database`) counts how many stored
  records qualify. See
  `docs/guides/selecting_kinetics.md`.

## Method-aware H298 selection endpoint (2026-10-03)

- tckdb-backend, tckdb-client 0.126.0, tckdb-schemas 0.90.0 and tckdb-mcp 0.3.0: a read-only
  `POST /api/v1/scientific/species-entries/{species_entry_ref}/thermo/select` assesses every visible thermo
  record of a species entry for the gas-phase formation enthalpy at 298.15 K and returns the outcome
  (`policy_preferred`, `incomparable_alternatives`, `sole_eligible_candidate`, `no_applicable_candidate`,
  or `policy_conflict`) with the assessments, preference relations, ordered fronts,
  rule versions and disclosures. `/thermo/select/manifest` downloads the replayable decision manifest. More than 500 visible records is a 422 (`thermo_selection_population_too_large`). Records below
  the review floor are listed only when the read profile has no floor of its own. The client gains
  `select_species_thermo` and the MCP server `tckdb_select_species_entry_thermo`. The application now builds
  the rule registry at startup, so a bad manifest pin fails the deploy. Browse order is unchanged. Third of
  three changes toward method-aware H298 selection. No schema change, no new environment variable; new
  refusal code `thermo_selection_condition_conflict`. See `docs/guides/selecting_thermo_for_h298.md`.

## H298 candidate assessment and the E1 preference rule (2026-10-03)

- tckdb-backend (service layer only; no route, wire package or client changes): a deterministic H298
  applicability assessor (finite scalar, exact 298.15 K point, NASA-7/NASA-9 evaluated by the existing
  engine; formation-from-elements reference, gas phase and declared target required), a versioned rule
  registry shipped with the app, rule E1 (standard G4 over standard G3, restricted to the 38-species
  audited manifest, curator-approved 1.0.0), and a preference engine that returns fronts, a
  `policy_conflict` for opposing rules or cycles, and a replayable decision manifest of public refs.
  Nothing is persisted and browse order is unchanged. Second of three changes toward method-aware
  H298 selection; the endpoint, SDK and MCP follow. `backend/scripts/ops/thermo_h298_coverage_inventory.py`
  reports, read-only, how many stored records can answer H298 and why the rest cannot. No schema
  change, no new environment variable.

## Thermo target and protocol declarations (2026-10-03)

- tckdb-schemas 0.85.0, tckdb-client 0.121.0 and tckdb-backend: a thermo record can state what its
  values describe (`thermodynamic_target`: `equilibrium_ensemble`, or `single_conformer` naming one
  conformer group of its own species entry) and how they were produced (`protocol`: a versioned
  declaration of the recipe, the formation-reference construction, the thermal approximation, any
  departures from the standard recipe and the supporting calculations). Both are optional attributed
  claims, stored as made; neither is ever inferred from a statmech link or a method name, and every
  existing record reads `null` for both. They travel through `/uploads/thermo`, the computed-species
  and computed-reaction bundles and contribution bundles, and thermo reads show them. First of three
  changes toward method-aware H298 selection; selection itself is not part of this one. Schema
  impact: revision `b3d8f1a6c924` adds three nullable columns and two CHECKs to `thermo` (additive,
  no backfill, `downgrade()` implemented); accepted thermo stays frozen, so an approved declaration is
  corrected only by supersession. Deploy the migration before the backend and clients. No new
  environment variable. See `backend/schema_spec.md` and
  `docs/guides/depositing_a_thermo_record.md`.

## Transition-state contract additions (2026-09-30)

- tckdb-schemas 0.64.0 and tckdb-backend: a transition state carries more of
  what a producer knows (#621). Validation evidence gains the kinds
  `energy_ordering` (the saddle point above both wells, with the compared
  energies, each from its own calculation) and `imaginary_mode` (count,
  frequency, displacement verdict); the computed-reaction bundle accepts a
  `statmech` block on its transition state; the standalone transition-state
  upload accepts scans, applied energy corrections and an atom map; and an IRC
  result may leave its direction and branch flags unstated, which read back as
  null and never as `false`. A TS entry read gains `include=statmech`, and its
  `validation` descriptor reports each evidence kind separately. Schema
  impact: revision `a7d3f1c95e28` adds `transition_state_validation_energy`,
  three nullable columns and relaxed constraints on
  `transition_state_validation_evidence`, and makes the IRC direction and flag
  columns nullable; it adds nothing to fill and refuses to downgrade over rows
  the old shape cannot hold. Only a passing `irc` record silences
  `transition_state_missing_irc_evidence`. No new environment variable. No client change.

## Kinetics: an Arrhenius reference temperature, and parity on the bundle (2026-09-30)

- tckdb-schemas 0.63.0, tckdb-backend, tckdb-client 0.102.0 (#620): a kinetics
  record carries `t0_k`, the reference temperature of `k = A (T/T0)^n exp(-Ea/RT)`
  (default 1 K, the plain `A T^n` form; `0 < t0_k <= 10000`; falloff, PLOG, Chebyshev and multi-Arrhenius records must stay at 1 K), on `POST /uploads/kinetics`, on the
  reaction bundle, and on every read of the row
  (`parameters.T0_k`, `t0_k` in the export, ML, lookup and analytics views).
  The CHEMKIN export writes `A / T0^n` so the mechanism file is the same rate,
  the thermo-kinetics consistency check does the same for Cantera, and the
  web Arrhenius chart and k(T) table evaluate with T0. The reaction bundle's
  kinetics block also takes `interpretation_assignments`, `tunneling_application`
  and `network_kinetics_ref`, validated and stored exactly as the standalone
  route does; every reference is to a record deposited earlier.
- Migration `f1c8a4d7b263` adds `kinetics.t0_k` (NOT NULL, default 1): existing
  rows read as 1 K, which is what they meant. It is metadata-only and touches no
  row, so approved records are unaffected. Stored consistency and reproducibility
  reviews stay current: a row at T0 = 1 K hashes as it did before the column.

## Writes are committed before the response is sent (2026-09-30)

- tckdb-backend: every write route now commits its session before the
  response goes out (`Depends(get_write_db, scope="function")`). Before, the
  commit ran after the `201` had been sent, so a read straight after an
  upload could return `404`, a commit-time failure was reported as a success,
  and `/auth/login` followed by `/auth/api-keys` could return `401`. A
  commit-time failure is now a coded error response and is still audited as a
  failed upload. The `fastapi` floor is raised to `>=0.121.0`, the first
  release with dependency scopes. No schema change; no client change: a
  client that polled to see its own write can stop.

## QCSchema torsion drives in and out (2026-09-29)

- tckdb-qcschema 0.6.0: `import` reads a QCSchema `TorsionDriveResult`
  (families v1 and v2, one or more dihedrals) together with the optimization
  it started from (`--parent-opt`) and posts both as one computed-species
  bundle: the optimization becomes the conformer's `opt` and the drive a
  `scan` calculation attached to it. Grid-point energies are stored exactly;
  each grid angle is checked against its own geometry before it is stored.
  `export` turns a stored relaxed dihedral scan back into a v2
  `TorsionDriveResult`, validated by qcelemental, and refuses bond, angle,
  improper and rigid scans and scans stored as a relative sweep rather than
  mislabel them. IRCs stay unsupported: qcelemental 0.51.2 has no model for
  them.
- Several drives from one optimization (one per rotor) are imported together
  (`import rotor1.json rotor2.json --parent-opt opt.json`): one `opt`, one
  `scan` per drive. A second rotor imported on its own later is refused with
  `scan_parent_already_imported` instead of storing the optimization twice.
- A torsion drive's mapping report names every field of the document once,
  as the other record kinds' do: each grid point's geometry and energy
  `transformed`, the per-point optimizations `retained_only`.
- `import --upload` and `export` with no `--base-url` and no
  `$TCKDB_BASE_URL` print one `ERROR [missing_base_url]` line, exit 1,
  instead of a traceback.

## Supersede names submissions by public ref (2026-09-29)

- Backend: `POST /api/v1/submissions/{submission_ref}/supersede` takes the
  old submission's `sub_...` ref in the path and `{"new_submission_ref":
  "sub_..."}` in the body; a row id is refused with 422 in either place
  (issue #571). Submission reads now carry `public_ref`, so the ref is
  obtainable. No caller sent the integer, so there is no deprecation window.
- The no-database-id guard walks every producer-facing write route, chosen
  by the producer contract's classification rather than a path-prefix list,
  and now checks path and query parameters as well as request bodies.
- tckdb-schemas 0.53.0: the producer contract describes the new shape.
- tckdb-client 0.95.1: parity table follows the renamed route.

## A QCSchema import names every field it does not store (2026-09-29)

- tckdb-qcschema 0.5.0: every field of an imported document is now in exactly
  one list of the mapping report (`transformed`, `retained_only`,
  `unsupported`, `rejected`), named by its path in the document's own family
  (`input_data.specification.model.method` for v2). Before, a Hessian
  document's `properties.return_energy`, `extras.qcvars` and nine other
  `properties.*` fields were dropped without being named (issue #573). A key
  the adapter does not know is listed `unsupported`; if a mapping branch
  leaves its own result unclassified, the import refuses with the new code
  `mapping_report_incomplete` and posts nothing.
- A Hessian document's energy is still not stored, and is now listed
  `retained_only`: a `freq` record has no energy field, and an `sp` beside it
  would be joined to it by a `single_point_on` edge, whose parent must be an
  `opt`.
- The exporter declares `fix_com` and `fix_orientation` on a Hessian export,
  because the matrix is expressed in the exported coordinates' axes.

## A web-site base URL is named, not a TypeError (2026-09-29)

- tckdb-client 0.95.0: a JSON endpoint answering 2xx with a body that is not
  JSON raises `TCKDBUnexpectedResponseError`, naming the URL requested and,
  for an HTML page, the `/api/v1` base URL to use instead (issue #568).
- tckdb-qcschema 0.4.1: `export` and `import --upload` print that, or any
  other client failure, as one `ERROR [...]` line with exit status 1 instead
  of a traceback. `import --upload` against a site root now stops at the
  duplicate pre-check instead of reading the page as "no duplicate" and
  posting the deposit to the web site.

## Enthalpy reference declaration (2026-09-23)

- tckdb-schemas 0.47.0 and tckdb-client 0.89.0: explicit enthalpy references,
  fit-only deposits, builder refusals, and grouped thermo reference reads.
- Database: nullable reference enum and an unvalidated scalar CHECK; no
  legacy data step. Workflows enforce whole-record content/declaration pairing.
- Producers: declared CHEMKIN formation convention, explicit ARC/SDF configuration,
  and CCCBDB sensible increments routed to molecular-property observations.
- tckdb-chemkin 0.4.0 declares the supported format's formation convention.
  ARC fit-only input leaves an absent H298 scalar null.

## Maturity and version policy

TCKDB is **pre-1.0**. Until 1.0:

- **Minor** version bumps may contain breaking changes to HTTP contracts,
  wire schemas, and database schema. Read the entry before upgrading.
- **Patch** bumps are additive or corrective only.
- The **database schema** advances by Alembic revision, never by editing an
  applied revision. The revision a deployment is at is reported by
  `GET /api/v1/readyz`, and is bound into every dataset-release manifest.
- **Append-only tables stay append-only.** `release_selection`,
  `release_manifest`, `release_artifact`, `record_review_event`,
  `record_reproducibility_assessment` and the accepted-science tables are
  guarded by database triggers, not only by application code. No release will
  relax that.
- **A published dataset release is never rewritten.** Superseding a curated
  selection appends a row; retracting a release sets its status to
  `withdrawn` and keeps the row and manifest readable, so an outstanding
  citation never dangles.

Per-package maturity classifiers:

| Package | Status | Notes |
|---|---|---|
| `tckdb-backend` | Alpha | HTTP read contracts stabilising; schema still advancing |
| `tckdb-client` | Beta | Typed methods track the published OpenAPI |
| `tckdb-schemas` | Alpha | Upload wire contracts; version bumped on every change — **enforced in CI since 2026-08-17; five earlier versions are not unique, listed under Unreleased** |
| `tckdb-mcp` | Alpha | Agent integration surface |
| `tckdb-chemkin` | Alpha | Mechanism-export adapter |

The client is deliberately ahead of the backend: it is a thin, well-tested
wrapper over a contract that is itself still moving.

## Unreleased

### Owner-scoped reproducibility rubric for transition-state statmech (#760)

- A transition-state-owned statmech was graded `insufficient` whatever its evidence, because the
  `scientific_context` check asked every statmech for a species entry. It is now graded under
  `tckdb_reproducibility_ts_statmech` v1: the shared rubric with that one question asking for a
  transition-state entry. It reaches at most `described`; no transition-state-specific check is added.
  Species-owned statmech keeps `tckdb_reproducibility` v1 byte for byte, and its stored assessments stay `current`.
- The read-time current/stale comparison now uses the rubric the fresh evaluation used. No migration, no
  schema or OpenAPI change. Stored transition-state-owned assessments read `stale` until a curator re-assesses;
  nothing is re-assessed automatically.
- New read-only operator report `backend/scripts/ops/reproducibility_ts_statmech_report.py` (`--list-stale` lists
  the stale records); runbook in `backend/docs/specs/reproducibility_assessments.md`.

### Observation reads, a deposit-time rights gate, and curator identity attach (Phase C-E5)

- New `GET /api/v1/scientific/species-entries/{id}/observations`
  (`?property_kind=`, `min_review_status`, `include_rejected`,
  `include_deprecated`, `include=internal_ids`, `offset`/`limit`): the first
  public read of `molecular_property_observation` -- until now no read route
  named that table at all. Returns typed value/unit, uncertainty (value,
  kind, coverage factor, level of confidence, assessor), temperature,
  pressure, state basis, method note, scientific origin, literature ref,
  external-source provenance (name/release/DOI/record key/content sha256/
  schema label/parser+mapping versions, unioned across the legacy flattened
  columns and the new `external_source_record` custody FK), and a review
  badge. No `collapse`/`selection_policy`/`trust` -- this record type is a
  raw observation, not a candidate product with review-rank selection.
- New Alembic revision `d2f4a7c1b8e6` (down from `0b4a3afabfd3`): gives
  `molecular_property_observation` a `public_ref` (`mpo_` prefix, opaque,
  backfilled server-side), so the new route never has to leak a row id.
- Rights gate, in the place the evidence actually ships unattested: a
  release's frozen artifacts (`selected_records.ndjson` /
  `candidate_records.ndjson`) never carry an observation row --
  `molecular_property_observation` is absent from `SELECTABLE_RECORD_TYPES`
  and `CANDIDATE_SOURCES`, so `publish_release`'s existing rights gate
  structurally cannot see one. It travels out instead inside
  `tckdb.deposit.v1`'s bundled `tckdb.archive.v1` evidence archive, which
  `write_deposit` wrote unconditionally. `write_deposit` now refuses
  (`observation_rights_basis_missing` / `observation_rights_basis_incompatible`,
  catalogued as operator-CLI-only) when an observation attached to one of
  the release's covered species entries has no rights basis compatible with
  the release's `data_license`. Scope is deliberately narrow: only
  observations about a species the release's own
  `release_record_universe` names as a subject; an observation about an
  unrelated species is bundled as archive bytes but asserts nothing about
  that release's license.
- New `POST /api/v1/admin/observations/{ref}/identity` (curator or admin):
  fills in an identity-unresolved observation's `species_entry_id`. Refuses
  a second attach (`observation_identity_already_set` -- correction is
  supersession, ADR 0003, not a repoint), a target that is not a
  ground-state, minimum-energy entry of its species
  (`observation_identity_target_not_ground_state_minimum` -- an observation
  carries no stereo/excited-state resolution of its own; the target does
  *not* have to be the unique such entry, since a curator choosing between
  two isomer-candidate entries is the disambiguation this tool exists for),
  and an observation with no linked submission
  (`observation_identity_attach_requires_submission`). Records the actor by
  username, an optional note, and the previous identity (always `null` --
  correction is supersession, never a repoint) as a `SubmissionAuditEvent`
  (`observation_identity_attached`) on the submission the observation was
  deposited under; never creates a species or species entry, and never
  mutates `raw_payload_json` (provenance, byte-identical across a later
  curation act).
- Every observation the ThermoML and CCCBDB bulk importers write is now
  linked to the `Submission` that deposited it (the CCCBDB importer opens
  a `source_kind=bulk_import` submission per run, standing on a
  `depositor_agreement` rights basis -- no CCCBDB terms/citation text
  exists in this repo to attest `source_terms` against, unlike ThermoML).
  Without this link, `observation_identity_attach` has nowhere to record
  the curation fact and refuses. Rows written by the CCCBDB importer
  before this change have no such link and refuse an attach until an
  operator backfills a submission for them.
- Each observation record returned by the species-entry read above now
  carries `identity_basis` (`external_identifier_match` /
  `curator_attached`), naming *how* the row got its identity -- derived
  from whether a `SubmissionAuditEvent` names it, not a stored column.
- New: an observation whose `identity_hint.inchikey` names a different
  connectivity (the InChIKey's first, hyphen-delimited block) than the
  attach target's species refuses with `observation_identity_hint_conflict`
  -- a stereochemistry-only difference is still accepted, since resolving
  that ambiguity is exactly what a curator attach is for.
- `tckdb-client` 0.86.0 -> 0.87.1: 0.87.0 adds the typed
  `get_species_observations()` method for the new species-entry
  observations read (the route entered the parity ledger as `raw_only`
  earlier in this feature, without a bump); the admin identity-attach route
  stays a curator-workflow `not_applicable`.
  0.87.1 regenerates `rejection_codes.py` for the new
  `observation_identity_hint_conflict` code.

### Observation uncertainty, state basis and source custody (Phase C-E1)

- New Alembic revision `0b4a3afabfd3` (down from `9b1c7e2d4a68`): gives
  `molecular_property_observation` a `heat_capacity_cp` property kind
  fixed to unit `J/mol/K`, `pressure_bar`, `state_basis`
  (`ideal_gas`/`real_gas`), and a typed uncertainty --
  `uncertainty_kind` (`standard`/`expanded`/`combined_standard`/
  `combined_expanded`, no "unspecified" member per DR-0007),
  `uncertainty_coverage_factor`, `uncertainty_level_of_confidence_pct`,
  `uncertainty_assessor` (`source_author`/`source_evaluator`) -- instead
  of a bare, meaningless `scalar_uncertainty` scalar.
- New tables `external_source` and `external_source_record`
  (`backend/app/db/models/external_source.py`): an immutable custody
  chain (source database/release, one snapshot's retrieval metadata,
  content digest, parser/mapping versions) that new importers attach to
  via `molecular_property_observation.external_source_record_id`.
  Existing CCCBDB rows are untouched and keep their flattened
  `external_source_*` columns; see
  `backend/docs/specs/cccbdb_importer.md` §7 Gap 4.
  `SubmissionRecordType` gains `molecular_property_observation`
  (reviewable, but not release-selectable and not supersession-eligible
  in this revision).
- No env vars. No route or importer changes -- schema only; the ThermoML
  importer, persistence service and review check land in later Phase C
  work packages (E2-E5).

### Digest-bound publication deposit (`tckdb.deposit.v1`)

- New `backend/scripts/ops/build_publication_deposit.py build|verify` and
  service `app/services/deposit/`: binds one published dataset release, the
  `tckdb.archive.v1` evidence archive written in the same session, the exact
  git commit and lockfiles, the manuscript-number generators and their
  expected outputs, `REPRODUCE.md` and `ACCOUNTS.md` into one `MANIFEST.json`
  of SHA-256 digests. Refuses (distinct exit codes) on a dirty tree, an
  untagged commit, an `unknown` package version, a database not at the
  Alembic script head, an unverifiable release, and any account or actor
  outside the `--author-account` allowlist; messages name usernames only.
- New `backend/scripts/paper/` generators (`corpus_counts`,
  `mechanism_roundtrip_counts`, `selected_thermo_by_species`,
  `candidate_lineage`, `transition_state_evidence`,
  `mechanism_fixture_provenance`) with a registry hook for
  `hessian_reanalysis`; `generate_expected_outputs.py --output-dir` renders
  each as canonical JSON and Markdown, deterministically.
- New `tckdb_archive.py verify <tar>`: offline member-hash check of an
  archive against its own manifest.
- Fixed: `tckdb_archive.py restore` refused every database produced by
  `alembic upgrade head` (`Target contains rows in table
  'accepted_science_repair'`), because data-repair revisions declare
  themselves unconditionally. Restore now tolerates rows in the two
  migration-written repair tables, which are excluded from the archive in
  both directions; every other excluded table must still be empty. Found by
  the first out-of-process restore test
  (`tests/integration/test_deposit_round_trip.py`).
- Runbook: `cutting_a_dataset_release.md` now says verification re-hashes
  frozen bytes and `live_divergence` is the separate non-blocking comparison,
  and gains "Build the publication deposit". No schema or migration change.

### Molecular thermo contract (Phase A)

- Coordinated versions: backend 0.2.0, `tckdb-schemas` 0.45.0,
  `tckdb-client` 0.84.0, and `tckdb-chemkin` 0.3.0. Backend and client
  require schemas >=0.45.0. Deploy these contracts together; older clients
  may still send valid content but cannot be assumed to preserve the new fields.
- Scientific reads/search, selected NDJSON and contribution bundles now retain
  phase, reference pressure, 0 K formation enthalpy and its uncertainty.
  Selected NDJSON also retains the 298 K scalar uncertainties. Explicit null
  state survives replay. Both computed bundle routes and typed thermo builders
  carry these fields.
- Creation validation is stricter: finite scientific values; a property in
  each point; complete NASA7 bounds/coefficient blocks; and NASA9 intervals
  that meet exactly in index order. A finite 0 K value, including zero,
  suffices as content. Historical reads and recovery archives remain permissive.
- CHEMKIN requires complete NASA7 at known gas phase and exactly 1.01325 bar
  (1 atm). **The computed-upload default of 1 bar is not eligible.** Ineligible
  selected records produce gaps; export does not substitute candidates, invent
  bounds/coefficients or convert pressure. The importer preserves the card phase
  and declares its supported 1 atm profile. Structured exports retain other states.
- Before rollout, run `backend/scripts/inventory_thermo_contract.py` against
  the intended database and review stable references/reasons. Drain queued and
  processing uploads using the old worker before switching versions. Never
  rewrite queued scientific payloads or historical rows to satisfy validation.
  Incompatible contribution replay reports `thermo_upload_incompatible`;
  use the recovery archive to retain historical incomplete evidence.
- No schema migration or backfill. Frozen release bytes are unchanged.
  See [rollout and verification](docs/research/tckdb-phase-a-verification.md)
  and the [publication evidence checklist](docs/research/tckdb-publication-evidence-checklist.md).

### Rights basis for every released record (Phase B1)

- **A dataset release can no longer ship a record nobody agreed to license.**
  Before this change the only rights field was the per-release
  `data_license` string, which says what terms the operator applies, not who
  consented; `LICENSE-DATA`'s "a default is not consent" was enforced as prose.
  Now every upload request and `BundleSubmissionMetadata` carry an optional
  `rights` fragment (`tckdb_schemas.rights.DepositRights`: `license`,
  `depositor_attests_right_to_license: true`, optional `source_terms`), recorded
  at deposit as a `depositor_agreement` attestation by the depositor. Curators
  record the other bases (`operator_own_data`, `historical_review`,
  `source_terms`) through `POST /api/v1/submissions/{id}/rights-attestations`;
  `GET` lists the append-only chain. Selecting or publishing a record whose
  deposits do not all carry a standing attestation naming exactly the release's
  `data_license` is refused (`rights_basis_missing`,
  `rights_basis_incompatible`); publishing also checks every unselected
  candidate the release ships (`candidate_rights_basis_*`) and refuses rather
  than filters. Every line of `selected_records.ndjson` and
  `candidate_records.ndjson` carries a `rights` object; the manifest document
  is now `tckdb.dataset_release.v2` with a `rights` block, and `v1` manifests
  keep reproducing their recorded digest.
- Schema: new append-only table `submission_rights_attestation` (database
  trigger refuses UPDATE/DELETE/TRUNCATE), new enum `rights_basis_kind`, and
  nullable `release_manifest.rights_summary_json` — Alembic revision
  `9b1c7e2d4a68`. **No backfill:** records deposited before this change are
  unreleasable until a curator attests a basis with an actor. Existing
  releases and their frozen bytes are unchanged.
- Versions: `tckdb-schemas` 0.46.0 (new `rights` module and fields).

### Added

- **A conformer basin now says how many optimisations back it, not how many
  optimisation jobs were run.** A two-stage geometry optimisation deposits two
  `opt` calculations — a coarse pre-optimisation and the refinement it feeds,
  joined by `calculation_dependency.dependency_role = 'optimized_from'`. Both
  belong to the basin; between them they are one optimisation.
  `evidence_summary.optimization_chain_count` on the conformer-group read
  counts optimisation **chains**, so a staged optimisation contributes `1`.

  Measured on the hosted instance: **156 anchored `opt` rows across 66
  conformer groups collapse to 136 chains**, with the 20 collapsed chains
  falling across 16 groups. Carbon dioxide, the landing page's showcase
  panel, has 3 `opt` rows and 2 optimisations.

  Only `optimized_from` collapses. `freq_on` (63 both-anchored pairs),
  `single_point_on` (65) and `scan_parent` (46) also join two calculations,
  but a frequency job on an optimised geometry is genuinely different evidence
  from the optimisation that produced the geometry, and folding those together
  would be a scientific error rather than a tidier number. The collapse also
  stops at the observation boundary and is correct for a chain of any length.

  **No previously published number changes.** `evidence_coverage.opt` counts
  observations, not calculations, so a basin whose observation carries a
  two-stage optimisation already read `1` for it; `calculation_count` and
  `geometry_count` deliberately stay row counts, because each is the length of
  a list `include=calculations` / `include=geometries` will hand back. The
  field is additive.

### Changed

- **A depositor can now download their own artifacts. Until now, nobody could.**
  `GET /api/v1/scientific/artifacts/{sha256}/download` served raw bytes only
  when a curator had marked the owning calculation `approved`. Measured on the
  hosted instance, **563 of 563 artifacts** belonged to calculations still
  `not_reviewed`, so the gate had never opened for anybody, including the
  person who uploaded the file. Approval publishes evidence to every
  authenticated caller; it was never a statement about who may see their own
  upload, and using it as one locked every depositor out of everything they
  had deposited.

  The gate is now **approved OR deposited by the caller**. Ownership is not a
  new rule: it is the predicate the upload route already authorizes attaching
  artifacts with — the `created_by` of a live submission that deposited the
  calculation, or `calculation.created_by` for deposits that predate
  submissions — extracted to `app.services.deposit_ownership` so the read path
  and the write path cannot drift into two answers.

  **The authentication gate is unchanged.** Ownership is a reason to serve an
  authenticated caller and never a reason to serve an anonymous one; every
  download path still requires a credential, and public access remains a
  separate, unbuilt scrub-on-download design. A caller who owns nothing still
  gets the same anti-probing 404 for an unapproved digest as for an unknown
  one — 404 and not 403, because a 403 would confirm the digest exists.
  Stored bytes are still verified against their SHA-256 and byte count on
  every path, the owner's included, and a verification failure is still
  recorded as a custody break (ADR 0014).

  [ADR 0004](docs/adr/0004-store-artifacts-verbatim-gate-raw-log-access.md)'s
  argument for gating raw logs at all — they carry scratch paths, usernames
  and scheduler ids, and scrubbing them at rest would break
  content-addressing — is untouched and restated; only its unargued choice of
  *review status* as the gate is amended. No schema or migration change.

- **A transition state now says how many of its entries have the evidence,
  not that one does.** `GET /api/v1/scientific/transition-states/{ref}`
  returned an `evidence_summary` carrying seven `has_*` booleans —
  `has_opt`, `has_freq`, `has_sp`, `has_irc`, `has_path_search`,
  `has_geometry_validation`, `has_scf_stability` — OR-ed across **every
  entry** under the transition state. `has_sp: true` therefore meant "at
  least one entry somewhere under this TS has a single point", which a
  reader takes as "this transition state has a single point". The flag was
  asymmetrically informative: `false` was strong (nothing anywhere has it)
  while `true` was nearly empty, so it was reliable only in the direction
  nobody reads it for.

  TS-concept scope now reports **`entry_count`** and an
  **`evidence_coverage`** block: per evidence kind, how many of those
  entries have at least one calculation of that kind. Coverage counts
  *entries*, never calculations — an entry with three `freq` jobs
  contributes `1` — so a value can never exceed `entry_count` and
  `sp: 1` against `entry_count: 3` shows an unevenly covered TS at a
  glance. `count > 0` reproduces the retired boolean exactly.

  A **full** count says coverage is complete, not that the entries are
  **comparable**: they may sit at different levels of theory, come from
  different codes, and describe different geometries. Coverage is not
  consistency; that still requires `include=calculations`.

  **TS-entry scope keeps its booleans** and is unchanged — one entry, one
  set of calculations, nothing for a `true` to hide behind. That covers
  `GET /scientific/transition-state-entries/{ref}`, the search surface
  (which returns entry-grain records and therefore needed no quantifier
  knob), and the `transition_states[*].evidence_summary` block embedded in
  `/reaction-entries/{id}/full`.

  **Breaking for callers reading `has_*` off the TS-concept detail
  endpoint. Migration: `has_x` becomes `evidence_coverage.x > 0`.** The
  defect is **latent today** — measured against the hosted instance, all
  34 transition states have exactly one entry, so the OR cannot currently
  differ from that entry's own value and nobody has been misled yet. It is
  a bug waiting for its trigger: the first second entry deposited under one
  transition state (a re-optimisation at another level, say) makes it
  silently wrong, with nothing in the response to mark the change. Fixing
  it while it is still latent is why the migration costs one expression
  rather than a data audit. No database schema or migration impact — this
  is a read projection, not a table. No client or MCP change: `tckdb-client`
  types this block as an opaque JSON object and the MCP search allowlist
  gains no field.

  Sibling of the conformer-group fix in the same release, which replaced
  the same OR-across-children booleans with `evidence_coverage` at group
  scope.

- **Reaction search now means containment, and a one-sided query finally
  works.** `GET|POST /api/v1/scientific/reactions/search` accepted
  `reactants` or `products` alone, and the validator said so explicitly —
  but the matcher compared *both* roles for multiset equality. A query
  naming only reactants therefore asked for "entries whose reactants are
  exactly {X} **and** whose products are exactly {}". No reaction has zero
  products, so a reactants-only search could not match anything, for any
  input, ever. It returned `200` with an empty result set rather than an
  error: measured against the hosted instance, `?reactants=NN` answered
  "no reactions" while the database held 24 hydrazine reactions.

  A **`match`** parameter now selects the comparison, and it defaults to
  **`contains`**: set containment per role. Every species the caller names
  must appear in that role; a side left empty constrains nothing. So
  `?reactants=NN` means "every reaction with NN among its reactants,
  products unconstrained" — the question a chemist was asking. `match` is
  accepted on `/scientific/reactions/search` and on
  `/scientific/kinetics/search`, which resolves reaction identity through
  the same service and had the same defect.

  Containment is deliberately **set**-based, not multiset. A reaction
  consuming two NN matches `reactants=NN`, and `reactants=NN&reactants=NN`
  matches a reaction consuming one. Stoichiometry is not a filter in this
  mode; a caller who wants counts to line up is asking for a specific
  equation, and that is what `match=exact` is for. The opposite reading is
  defensible enough that it is written into the enum's docstring and into
  the OpenAPI description rather than left to fall out of the code.

  **Breaking for existing callers who supply both sides.** They got exact
  multiset equality; under the new default they get containment, which
  returns a superset — a two-species query will now also match the larger
  reactions that contain those two. **Migration: send `match=exact`**,
  which reproduces the previous behaviour byte for byte on both endpoints
  and remains a first-class query (`reaction_ref` does not replace it —
  you do not always hold the ref). Callers who supply one side were
  getting an empty list and cannot regress. `direction` is unchanged and
  composes with `match` on both axes: under `direction=either`,
  containment is tested in both orientations and `matched_direction`
  reports which one matched under the same semantics the matcher used.

  Per the pre-1.0 policy above, a minor bump may break an HTTP contract.
  This one does, which is why it is written down here rather than shipped
  quietly. No database schema or migration impact: this is a query
  projection, not a table. **`tckdb-client` 0.53.0 → 0.54.0** (new `match`
  keyword on `search_reactions` and `search_kinetics`; omitted means the
  server default applies). **`tckdb-mcp` 0.1.1 → 0.2.0** (new `match`
  field on `tckdb_search_reactions` and `tckdb_search_kinetics`).

### Added

- **The scientific data has a license: CC BY 4.0.** The code was MIT and said
  so; nothing anywhere said what a reader may do with the *numbers*. Since
  `dataset_release.data_license` is a required, non-blank column, that
  unanswered question was the hard blocker on publishing anything — every
  release must name a license, and no release had ever been cut.

  `LICENSE-DATA` at the repository root is the notice: CC BY 4.0 (attribution
  required, reuse otherwise unrestricted, and the attribution is a citation of
  the dataset release), stated by reference to the canonical deed and legal
  code rather than by vendoring a copy that would drift. It says what it
  covers — deposited records, derived products, raw calculation artifacts
  published in a release — and what it does not: the source code (MIT, see
  `LICENSE`) and the third-party test fixtures carried in from RMG-Py and ARC,
  which are code-adjacent test inputs under their own licenses and are
  excluded by name.

  `CC-BY-4.0` and `MIT` are now the defaults in both release-creation paths
  (`create_release`, `POST /api/v1/releases`), so a curator cutting an
  ordinary release does not restate the project's licensing on every request.
  An explicitly supplied license still wins and is stored verbatim; an empty
  string is still refused. No schema change: the column and its
  `data_license_nonblank` constraint are unchanged.

  Recorded alongside it, because it stops being moot the moment a second
  person uploads: **an operator may license their own deposits and nobody
  else's.** The license must become part of the upload contract *before* a
  deployment accepts deposits from a second contributor — not retrofitted
  afterwards. That constraint is written into `LICENSE-DATA`,
  `backend/docs/specs/ingestion_submission_model.md`, and
  `backend/docs/specs/dataset_release_and_profiles.md` §7b. The mechanism is
  deliberately not built here.

- **Every token TCKDB puts on the wire now has a published definition.**
  `matched_direction: "reverse"`, `under_review` beside `not_reviewed`,
  `"ts_graph_or_smiles_present": "missing"`, `spc_` beside `spe_` — each is
  a real distinction the code takes seriously and none is guessable from
  the string, and nothing in the API or the documentation said what they
  meant. `docs/guides/api_vocabulary.md` is a glossary of 438 of them: the
  review statuses, the trust badges and check outcomes, the 143 trust check
  names, the identifier prefixes with their content-derived/opaque split,
  the reaction-search vocabulary, and every refusal code a caller can
  receive.

  It is **generated** from the sources that define those tokens — the
  read-schema enums, `public_refs.PREFIXES`, the trust rubrics, the code
  catalogue — by `backend/scripts/generate_api_vocabulary.py`, and
  `backend/tests/scripts/test_api_vocabulary.py` fails if the committed
  document drifts from a fresh render, if an entry names a token no source
  has, or if an enum every response carries has no entry. A hand-written
  glossary would be stale the first time somebody added a status, and a
  stale glossary answers confidently and wrongly.

  Inclusion is two halves: a reader must be able to meet the token (checked
  mechanically against the enums the read schemas and the trust fragment can
  serialise) and chemistry must not already decode it (declared per
  vocabulary). Chemistry-valued vocabulary is deliberately out. Reaction
  family display names are recorded as a **gap** rather than invented:
  `reaction_family` holds only `id`, `name` and `created_at`, so
  "Hydrogen Abstraction" for `H_Abstraction` is not a fact this database
  holds.

- **The base URL now answers a person.** `https://<host>/` served a bare
  `404 application/json` blob, and so did `/docs`, `/redoc` and
  `/openapi.json`, because hosted deployments set `EXPOSE_API_DOCS=false`
  and no route was registered at `/` at all. Anyone following the URL a
  paper prints — a referee, most importantly — got an error object with
  nothing in it to say a database was there. `/` now serves a
  self-contained HTML landing page: what TCKDB is, a worked example of a
  real check refusing to accept a frequency list it cannot justify, the
  four write-behaviour roles every table plays, how to cite the software
  versus a dataset release, and where the API starts. It loads no CDN, no
  web font and no script, so it renders on a locked-down network and with
  JavaScript disabled. Nothing else about the route table changed: `/` is
  registered last, matches one exact path, and is excluded from the
  OpenAPI document.
- **`EXPOSE_API_REFERENCE`**, a new setting, defaulting to `false`. With
  `EXPOSE_API_DOCS=false` it registers ReDoc at `/redoc` and the schema at
  `/openapi.json` and still leaves Swagger UI at `/docs` unregistered.
  `EXPOSE_API_DOCS` was all-or-nothing, and the startup guard refuses to
  boot a hosted deployment with it on — correctly, because it brings
  Swagger's live request console with it. That is not the same risk as
  publishing the contract, which a hosted instance wants to do. Note that
  FastAPI's ReDoc page loads its renderer from `cdn.jsdelivr.net` and its
  fonts from Google Fonts; the landing page does not.

### Fixed

- **A species found by searching for its formula was served without one.**
  `GET /api/v1/scientific/species/search?formula=CH3` matched the methyl
  radical and returned it with `"formula": null` — the field was declared on
  `SpeciesScientificRecord` and assigned by nothing. The database had already
  computed the formula in order to answer the query, through the functional
  index `ix_species_formula_lookup` over
  `mol_formula(mol_from_smiles(smiles))`, and then the read path discarded it.
  The landing page renders a **Formula** row for every species; it had always
  read "not recorded".

  The formula is now **the same SQL expression that answered the filter**,
  projected onto the page's species rows. Not a second implementation in
  Python: search and display cannot disagree about the formula of one row
  because there is one expression, used twice, in one function. No stored
  column and no migration — `species` still has no `formula`, and the value
  stays derived.

  What arrives is Hill notation as the RDKit cartridge spells it: `H2O`,
  `C6H6`, and `CH3` for the `[CH3]` radical — **radicals carry no marker** —
  with a trailing charge suffix for ions (`HO-`, `H4N+`, `Fe+2`). Isotopes are
  not distinguished, so heavy water reports as `H2O`. The landing page needed
  no change: it subscripts a formula only when the parsed element/count
  sequence round-trips to the original string, so the ionic forms print
  verbatim rather than being rendered wrongly.

  `formula` stays `str | None` and is now null in exactly one case: a species
  whose stored SMILES will not parse, where the cartridge yields SQL NULL
  rather than raising.

- **The species search served cis- and trans-diazene as the same record, and
  a reader picking one had even odds of citing the other molecule.** `GET
  /api/v1/scientific/species/search?smiles=N=N` returned two species entries
  whose *every served field* was byte-identical apart from an opaque
  `species_entry_ref`: same `species_entry_kind`, same
  `electronic_state_kind`, same availability counts, same review badge. The
  database knew exactly what they were — `stereo_label = Z` (cis-diazene) and
  `stereo_label = E` (trans-diazene), two molecules with different
  thermochemistry — and the read projection dropped the column. Nothing on
  the wire hinted that anything had been withheld, so a reader asking for
  diazene statmech chose a ref at random. That is a wrong scientific answer,
  not a missing feature, and it was live and anonymous.

  The two **species-discovery** surfaces — `/species/search` and
  `/species/structure-search` — now serve the entry's identity columns:
  **`stereo_label`, `electronic_state_label`, `term_symbol` and
  `isotope_key`** (`electronic_state_kind` was already served), plus
  **`species_entry_label`** — those five rendered as one short string
  (`"E"`, `"excited T1"`). Those five columns are exactly
  `uq_species_entry_species_id` minus the species itself, which is what makes
  the set a real discriminator rather than a hint: two entries of one species
  differ in at least one of them by construction, so they cannot both render
  as `null` and cannot render the same. Both records also gain
  **`stereo_kind`** from the parent species, which is what makes a *null*
  stereo label readable — `achiral` says there is nothing to label,
  `ez_isomer` says stereoisomers exist and this entry has not been labelled.

  **The same defect was on nine further blocks, and they are fixed too.**
  Discovery is where a reader picks a ref, but it is not where they read a
  number. A statmech search for the deployed `N=N` species returns *eight*
  records spanning both diazene entries, and every one of their `species`
  context blocks reported `canonical_smiles: "N=N"` and nothing else — so a
  reader could not tell which partition function belonged to which molecule
  even after picking correctly. Each of these blocks now carries
  `species_entry_label`:

  | Block | Surface |
  |---|---|
  | `StatmechSpeciesContext` | `/statmech/search`, statmech detail |
  | `ThermoSearchSpeciesContext` | `/thermo/search` |
  | `TransportSpeciesContext` | `/transport/search`, transport detail |
  | `ConformerSpeciesContext` | conformer search + detail |
  | `SpeciesCalculationsSpeciesContext` | `/species/calculations/search` |
  | `SpeciesEntryOwnerSummary` | calculation detail |
  | `NetworkSpeciesSummary` | `/networks/{ref}?include=species` |
  | `ReactionParticipantSummary` | `/reactions/search` |
  | `ReactionFullSpeciesParticipant` | `/reaction-entries/{ref}/full` |

  A test enumerates `app.schemas.reads` and fails on **any** response block
  that names a species (`canonical_smiles` or `smiles`) together with a
  `species_entry_ref` but carries no `species_entry_label`. The guard is
  written over the schema package rather than over a list of surfaces,
  because the defect was on eleven blocks at once — a checklist would have
  missed the twelfth.

  **One thing deliberately not changed: the reaction `equation` string.** It
  is still rendered from participant `smiles` alone, so `NN <=> N=N + [H][H]`
  does not say which diazene. Fixing it would change a served value that
  consumers parse, which this release is not doing; the participant blocks
  now carry what is needed to render the equation unambiguously, and
  changing the default rendering is its own decision.

  The derivation is **not new**. `species_entry_label` already existed for
  the pressure-dependent network surface, which hit this same defect on this
  same species (the hydrazine network's two diazene wells) and solved it
  there — for network *state* composition only, while the network's own
  `include=species` block still had it. It has moved to
  `app/services/scientific_read/species_identity.py` and every surface
  imports it, so a species reads the same way in a species search, a
  structure search, a statmech record and a network state label. Two
  functions producing species labels that can disagree is a failure this
  project has hit repeatedly (`points`, `literature`, `parameters`,
  `workflow_tool_release`, `R`, `CO`, `F`). A second test asserts the
  derivation reads every column of `uq_species_entry_species_id`, read off
  the ORM model rather than restated, so adding a sixth identity column
  cannot silently turn the label back into a hint.

  **Default projection, not an `include=` token.** A field whose absence
  makes two records indistinguishable belongs in the default, because a
  reader who does not know to ask is exactly the reader who gets the wrong
  molecule. Every value comes from a row the surface already loaded — the
  discovery surfaces hold the ORM entity, the context builders gained four
  columns on a `SELECT` they were already running — so this costs no
  additional query anywhere.

  **A null column renders as `null`, never `""`.** Measured against the
  hosted instance: 60 species entries, of which **9** carry a stereo label
  (`S`x4, `E`x3, `Z`x1, `R`x1) and **51** carry none;
  `electronic_state_label`, `term_symbol`, `isotope_key`,
  `isotopologue_label` and non-`ground` `electronic_state_kind` are at **0**.
  `stereo_kind` is non-nullable and populated on all 59 species. So the acute
  case is narrow — `N=N` is the only species with two entries — and the
  under-description was broader. The four zero-population columns are served
  anyway because they are identity components: the first entry that carries
  one must be distinguishable the day it lands, not the day someone notices.

  `isotopologue_label` is deliberately **not** served. It is deprecated, is
  no longer part of the entry's unique identity, and is never written by the
  application, so it cannot discriminate between two entries; serving it
  would advertise an identity component that is not one.

  Purely additive — no served field is removed, renamed, or changed in value.
  No database schema or migration impact: this is a read projection, not a
  table. `tckdb-client` **0.55.0 -> 0.56.0** declares `stereo_kind` on
  `SpeciesRecord` and the five identity fields plus `stereo_kind` on
  `SpeciesStructureRecord` (`SpeciesRecord.entries` was already an untyped
  JSON list, so nested entry fields flow through either way). No MCP change:
  `tckdb-mcp` passes the search response through without knowing its shape.

- **A linear molecule could deposit a frequency list with one vibration
  missing, and nothing said a word.** A harmonic analysis of `N` atoms has
  `3N - 6` vibrations if the molecule is bent and `3N - 5` if it is linear —
  linear molecules have one *more*, because a bend that would be two distinct
  motions in a bent molecule stays degenerate along a straight axis. TCKDB
  already flagged one direction of that one-mode gap (a bent geometry carrying
  the linear count). The other direction was open: a **linear** geometry
  carrying `3N - 6` modes is one vibration short, and every check accepted it.
  The wire-side completeness floor could not catch it either — `3N - 6` *is*
  that floor and it warns strictly below, so the deposit landed exactly on the
  accepted line.

  The consequence is the one that matters: a consumer recomputing a partition
  function from that record gets a *number*, not an error, and the number is
  wrong. Silently.

  Now reported as `freq_list_bent_mode_count_for_linear_geometry`, an
  `UploadWarning` on an accepted 201 — advisory, never blocking, because the
  linear/bent boundary is a tolerance rather than a definition (ADR 0008) and
  because a genuine partial or frozen-atom Hessian produces the same count
  honestly. The message names the cause that actually produces it: **a linear
  molecule's bending modes are doubly degenerate** — CO2's four vibrations are
  two stretches and one bend counted twice — so a parser that de-duplicates
  equal frequencies drops one component and lands exactly on `3N - 6`. The
  generic short-list warning argues from partial Hessians and frozen-atom
  regions, and would have sent a depositor looking in the wrong place; that is
  why this is a second code rather than an extension of it.

  The two codes are mutually exclusive by construction, not by convention: the
  counts differ by one and each is admitted only under the opposite geometry
  verdict. Reaches every upload shape — the conformer route, the
  computed-species and computed-reaction bundles, standalone transition
  states, the statmech/thermo/transport product uploads and the
  pressure-dependent network route — because all six walkers already funnelled
  through one judgement function.

- **Two pull requests could claim the same package version, and `git` would
  merge it silently.** Every change to a published package bumps its version.
  Two branches that start from the same base therefore bump to the *same next
  number*, and when the second merges, the version line on both sides is
  **byte-identical** — so `git` reports no conflict, the file does not appear
  in the merge diffstat, and one version number ends up describing two
  different packages. This was caught by hand on 2026-08-17, one line-diff
  before it shipped.

  A pull-request check now refuses it. It makes **two** comparisons against
  **two different refs**, because one ref cannot do both: *monotonicity*
  against the merge base (`git merge-base(base.sha, head.sha)` — not `main`'s
  tip, or a branch merely behind would be blamed for someone else's merge),
  and *novelty* against `origin/main` fetched at job runtime plus existing
  `<name>-v<version>` tags. The near-miss above passes the first check and is
  caught only by the second. Comparison uses a PEP 440 sort key, not string
  order, so `0.10.0` correctly exceeds `0.9.0`. Covers `tckdb-client`,
  `tckdb-schemas`, `tckdb-chemkin` and `tckdb-mcp`; a test fails if a new
  `pyproject.toml` appears in neither the covered nor the excluded list.

- **Five `tckdb-schemas` versions already carry more than one package, and
  this is the record of them.** Measured twice, by two independently written
  scripts that agreed, comparing *tree contents* per version rather than
  commit counts:

  | Version | Distinct states |
  |---|---|
  | `0.2.0` | 3 |
  | `0.8.0` | 2 |
  | `0.14.0` | 2 |
  | `0.30.0` | 2 |
  | `0.33.0` | 2 |

  `tckdb-mcp` `0.1.0` carries four states — it has never been bumped at all.
  **`tckdb-client` is clean** across its whole history.

  The difference is not cosmetic. Across the two `0.2.0` states, one lacks
  `HessianSource`, `TunnelingModel`, and the `lindemann` / `troe` / `sri` /
  `plog` / `chebyshev` entries entirely: **one state rejects payloads the
  other accepts.**

  **`0.8.0` is the one to know about.** The annotated tag
  `tckdb-schemas-v0.8.0` reads *"pinned for tckdb-adapters/tckdb_arc (Phase
  1)"*, and holds the **first** of its two states —
  `git merge-base --is-ancestor 7ad5cb99a tckdb-schemas-v0.8.0` returns false.
  Commit `7ad5cb99a` changed `tckdb_schemas/enums.py` and
  `tckdb_schemas/workflows/computed_reaction_upload.py` and left
  `version = "0.8.0"` untouched, so the two states **accept different upload
  fields**.

  **No consumer resolves a version number**, which is why nothing is being
  re-published to correct this. These packages were only ever installed from
  git, never from a package index, and ARC — the only known downstream —
  installs from a branch with no tag, SHA, or version constraint at all. So a
  duplicated version cannot mis-resolve for anybody today.

  **The `v0.8.0` tag is deliberately left where it is.** Moving or deleting a
  tag is its own hazard: anyone who has already fetched it holds the old
  target either way, and a moved tag makes two clones disagree about what a
  name means. It is recorded here instead, which is the honest fix for
  something that has already happened.

- **The `mypy` gate could not see the wire-contract package, and said
  "Success" anyway.** `tckdb-schemas` is a first-party package that lives in
  this repository and is installed *editable*. `mypy` does not read an
  editable install's import hook, so it could not find the package by name and
  reported `import-not-found` on all **38** of its imports from
  `backend/app/schemas` — which `ignore_missing_imports = true` then absorbed
  silently. The gate reported `Success: no issues found in 149 source files`
  while every type error *inside* the wire package, and every type error in
  backend code arising from how it uses the wire package, was invisible to it.

  Made resolvable and made a check target: `mypy_path` points at the package,
  the package is listed in `files`, and `ignore_missing_imports` is now
  **off**. All three are necessary and none substitutes for another.
  `follow_imports = "silent"` means a merely-*imported* module is analysed but
  its own errors are suppressed, so `mypy_path` alone would still have said
  nothing about a broken annotation inside the package; and
  `ignore_missing_imports` cannot distinguish "third-party package with no
  stubs" from "first-party package we failed to point mypy at", which is what
  made the original failure silent by construction. Measured at the time of
  the change: nothing in scope needed the setting — all 38 suppressed errors
  were `tckdb_schemas`. A stubless third-party dependency now needs a narrow
  per-module override, the way `rdkit` already has one.

  **12 findings** the gate had been missing, all in code that merged green.
  Eleven are fixed here; one is a documented suppression with its argument on
  the line. The one that was a live defect rather than an annotation
  infelicity: `EnergyTransferIn.model` on the pressure-dependent network
  upload is a **required** `str`, but its normalizer was
  `normalize_optional_text`, which collapses a blank string to `None`. A
  whitespace-only energy-transfer model name passes `min_length=1` and then
  left the field holding `None`. It is now `normalize_required_text`, which
  refuses the blank with a 422 — which is what `min_length=1` was already
  promising. The rest: two `float | None` comparisons in the NASA polynomial
  temperature-bound validator that were guarded by a `None`-count the checker
  could not read; two loop variables reused across loops over differently
  shaped payloads; and one dict annotated with the ORM `CalculationType` while
  holding the wire `CalculationType` — two distinct classes with identical
  members, whose every comparison worked only because both subclass `str`.

  Gate scope went from 149 to 183 source files. Proven by mutation, because a
  configuration change that silently still ignores the package looks identical
  to one that works: a deliberate `return 12345` from a `-> str` function
  inside the wire package, a deliberate misuse of a `tckdb_schemas` symbol
  from `backend/app`, and a deliberately unresolvable `mypy_path` each fail
  the gate now and each passed it green before.

- **A full artifact store is no longer reported as "retry later", and no
  longer reports itself as healthy.** Two halves of one defect.

  *The wrong answer.* `artifact_storage.py` special-cased exactly one
  condition — the store answering "no such key" — and every other error a
  botocore `ClientError` can carry collapsed into a single
  `503 artifact_storage_unavailable ... Retry later.` A **full** store landed
  in that residue, so a depositor uploading into a store with no disk left was
  told to do the one thing that cannot work: retrying a full disk fails until
  an *operator* frees space. Uploads into a store that has no room now answer
  **`507 artifact_storage_full`**, whose body says an operator must act.
  507 rather than a second code at 503 because the *status* then carries the
  advice: it is registered with exactly this meaning (RFC 4918), it is what
  MinIO itself answers, and it is absent from `tckdb-client`'s default retry
  set — so a pinned client and a non-Python caller both stop after one attempt
  without knowing the code exists. The `Replay` vocabulary is deliberately not
  extended: `never_succeeds` would be a false claim (an operator frees space
  and the identical request then succeeds) and any declaration at 507 is inert
  by `is_replay_futile`'s own rule.

  The error codes are **measured, not inferred**. MinIO
  `RELEASE.2025-09-07T16-13-09Z` was filled on a size-capped scratch volume
  and answered `XMinioStorageFull` at HTTP 507; with a hard bucket quota it
  answered `XMinioAdminBucketQuotaExceeded` at HTTP 400. `EntityTooLarge` is
  deliberately *not* treated as a capacity signal — it is a fact about one
  object, not about free space.

  *The silent health check.* `/status` probed artifact storage with a
  `head_bucket`, and a full store answers a `head_bucket` with **200**: every
  read succeeds, and — measured — even a 1-byte write succeeds on a store that
  refuses a 4 MiB one, because MinIO's threshold check is sized against the
  incoming object. So `/status` reported green while every artifact upload
  failed for want of space, and nobody was told, including whoever was on
  call. No read-only probe can detect this and no *cheap* write probe can
  either; the S3 API exposes no capacity query to ask instead. `/status` now
  reports what the real write path was told, as
  `artifact_storage.storage_full` with the observation timestamp, and degrades
  on it. Its limits are documented rather than papered over: `false` means "no
  refusal is outstanding", not "there is space", and onset still needs one
  upload attempt to fire. See
  [`docs/deployment/troubleshooting.md`](docs/deployment/troubleshooting.md).

- **That observation is now durable, and clears only on evidence of the right
  size.** It lived in a module global, so a restart forgot it and `/status`
  reported healthy until the next upload failed. It is now an append-only
  `artifact_storage_capacity_event` log, with no `is_full` column — "is the
  store full?" is computed head-of-log, because a stored flag would be a
  second source of truth able to disagree with the log it summarises (the
  shape ADR 0007 rejected for curated selections).

  The clearing rule is the point, and the obvious version of it is wrong.
  "Clear when a write succeeds" would have restored a green light while every
  real upload still failed, because the same store **refused 8 MiB and
  accepted 1 byte in the same second**. So a refusal records the *size* it was
  refused at, and is answered only by a later write of at least that size, a
  free-space reading of at least that size, or an operator. There is
  deliberately **no time-based expiry**: a timer guesses, a size-qualified
  success measures, and a stale "full" an operator can clear in one command is
  safer than a flag that goes quiet while the disk is still full.

  `/status` additionally consults **MinIO's admin API** for free space while a
  refusal is outstanding, so recovery is noticed on the next poll instead of
  on the next sufficiently large upload. It needs no new credentials (the
  compose file already gives the API MinIO's root user), writes nothing, and
  is skipped entirely on a healthy store. It is supplementary, never
  authoritative: any failure — a non-MinIO store, a 403, a timeout — is "no
  opinion" and changes nothing. It is also, measured, **blind to bucket
  quotas** (418 MiB reported free while a 2 MiB write was refused), so it may
  not clear a quota refusal.

  New admin endpoints `GET`/`POST /admin/artifact-storage/capacity[/clear]`
  let an operator read the state and clear it with a required reason, which is
  recorded. Clearing appends; it never edits or deletes the refusal.

  Also fixed on the way: `artifact_persistence._store_and_record`'s broad
  `except Exception` caught the already-typed `ArtifactStorageUnavailable` and
  raised a *fresh* one, discarding every discriminator set upstream — so the
  pre-existing `missing` flag was being erased on the upload path too; and
  `store_artifact` let a raw `ClientError` from `create_bucket` escape past
  every `except ArtifactStorageUnavailable` downstream.

  Behaviour of `artifact_storage_unavailable` (503) and
  `artifact_object_missing` (502) is unchanged. **`tckdb-client`
  0.51.0 → 0.52.0** (documentation and one test; no code change — 507 was
  already outside the default retry set). No schema or migration impact.

- **A client no longer retries a lost artifact forever.**
  `GET /scientific/artifacts/{sha256}/download` reports two different storage
  failures: `503 artifact_storage_unavailable` when the object store did not
  answer, and `502 artifact_object_missing` when it answered and the bytes a
  still-published record points at are gone. Only the first can clear by
  waiting. Both statuses are in `tckdb-client`'s default retry set and the
  code that says which is which was excluded from `RejectionCode` by
  construction (the enum is `4xx` only, because a `5xx` refuses nothing the
  caller did) — so the server was honest and no client could act on it, and a
  custody break was replayed on a backoff schedule for a condition guaranteed
  never to clear.

  The code catalogue now carries a second, **declared** classification
  (`Replay`) beside the derived `is_client_facing`, and the generator emits it
  as **`NON_RETRYABLE_CODES`** — a `frozenset[str]`, not enum members, since
  these are not refusals. `RetryPolicy` reads the response body's `code` at the
  point it decides to retry (the whole response is in scope there) and stops
  after one attempt on a match. It is a **deny list**: an unrecognised code is
  retried exactly as before, so a pinned client never abandons a transient
  failure a newer server introduced. No HTTP status, code spelling or response
  body changed; no `RejectionCode` member was added or removed.
  **`tckdb-client` 0.49.0 → 0.50.0.** No schema or migration impact.

### Added

- **Imaginary modes get a determination, not just a threshold.**
  `GET /api/v1/scientific/calculations/{ref}?include=imaginary_mode_projections`
  projects each imaginary mode onto rigid-body motion and onto a dihedral
  rotation about each rotatable bond, as ADR 0012 asked for. The eigenvectors
  are recovered from the Hessian already stored in `calc_hessian`, so **nothing
  new is stored**: no table, no column, no migration, and the block is
  recomputed per request. The depositor's declared `imaginary_disposition` is
  reported *beside* the computed determination with the raw overlaps and the
  thresholds applied; a disagreement is surfaced as `agreement: conflicts` and
  never silently resolved. Where no Hessian is stored the block reads
  `hessian_not_stored` -- not determinable, which is a different answer from
  "no residue found". Detail-only and opt-in by name, like `include=trust`.
  `available_sections.has_hessian` is new on every calculation record.
  `backend/scripts/ops/project_imaginary_modes.py` runs the same projections
  over a whole corpus; run over all 18 live records carrying an imaginary mode
  and a Hessian, every one determines as `internal_vibration` with
  `rigid_body_overlap` 0.0000, and none carries a declared disposition. ADR 0013
  held that this was uncomputable because TCKDB stores no displacement vectors;
  that claim is corrected in place.

- **Curated vs exploratory read profiles.** Every `/api/v1/scientific/*`
  endpoint accepts `?profile=exploratory|curated`. `exploratory` is the default
  and is explicitly labelled as carrying **no TCKDB recommendation**; `curated`
  raises the review floor to `approved` (applied both to searches and to
  detail-by-ref reads) and reports `approved_floor_only` — it does not claim a
  curator selected those records. The release-backed endorsement
  (`tckdb_curated_release`) is emitted only by `/scientific/releases/*`, where
  records really are resolved through an attributed selection. The resolved
  profile is echoed in every scientific response and in every dataset manifest.
- **Attributed, append-only release selections.** New `curation_policy`,
  `dataset_release` and `release_selection` tables record which candidate a
  named curator chose for a subject, under which policy version, with what
  rationale, for which release. Selections never mutate the record they point
  at; superseding appends a new row.
- **Immutable, citable dataset manifests.** New `release_manifest` and
  `release_artifact` tables freeze the manifest document, each shipped file's
  bytes, and a SHA-256 over both, bound to the Alembic revision, backend and
  wire-schema package versions, curation-policy version and review-policy
  version. Publication is the only write: later uploads, review progressing, a
  DOI being attached or a withdrawal cannot change what a citation resolves to.
  Whether the live database still agrees is reported separately and
  non-fatally as `live_divergence`. A release ships its selections *and* the
  full candidate set and review history behind them, each line carrying
  chemical identity (SMILES/InChIKey) and level-of-theory/software provenance
  so a deposited file is interpretable offline.
- Selections may only name records at or above the `approved` review floor,
  checked on append and re-checked at publication; a release that selects
  nothing cannot be published.
- Public read surface: `GET /api/v1/scientific/releases`, `/{handle}`,
  `/{handle}/manifest` (with live re-verification), `/{handle}/selections`,
  `/{handle}/artifacts/{path}`. Curator-gated writes under `/api/v1/releases`.
- `CITATION.cff`, `SECURITY.md`, and this changelog.

### Changed

- **A calculation's citation is a paper on every upload root, not a row id on
  five of them.** `CalculationPayload.literature_id` is replaced by an inline
  `literature` fragment, resolved to a `literature` row by the workflow layer.
  The field was the calculation block of `/uploads/conformers`,
  `/uploads/transition-states`, `/uploads/statmech`, `/uploads/thermo` and
  `/uploads/transport` (and their `/jobs/*` twins). A depositor has a DOI, not
  our primary key: supplying one required having already queried this database.
  Resolution moves to `resolve_and_persist_calculation_with_results`, the one
  seam every upload root reaches, instead of being repeated in three
  workflows.

  **`tckdb-schemas` 0.32.0 → 0.33.0.** Breaking: `literature_id` no longer
  validates on those roots. `SchemaBase` is `extra="forbid"`, so an old payload
  gets a 422 naming the field rather than a 201 with the citation silently
  dropped. **No stored value changes** — `calculation.literature_id` is
  unaffected and no migration is involved.

  The same field had already been removed from the reaction bundle, the
  network-PDep route and `CalculationIn`, each time by hand, each time on one
  root. The reason it kept surviving is that the no-FK-ids invariant was
  asserted by exactly one test over exactly one upload root. That walker now
  runs over **every** upload root, discovered from the live route table so a
  new route is covered the moment it is registered
  (`backend/tests/schemas/test_upload_roots_expose_no_fk_ids.py`). Generalising
  it surfaced four further FK-shaped fields on depositor-facing surfaces
  (`SCFStabilityPayload.source_calculation_id` / `source_artifact_id`,
  `CalculationScanPointCreate.geometry_id`,
  `ReactionParticipantUpload.species_entry_id`); each is read by the server
  today, so each is frozen in a documented inventory with its reason rather
  than removed unreviewed.

- **Three codes a client could import but never receive are no longer
  exported.** `app/api/code_catalogue.py` gains a `Reach` field, so an entry
  can now record that no request produces it — the middle case of a three-way
  distinction that previously had no spelling: catalogued and client-facing (a
  caller can provoke it), catalogued and not client-facing (a real guard no
  request can trip), not catalogued at all (not a code). Before this, a guard
  could only be recorded by telling clients they might receive it, or by
  deleting the entry and leaving the next reader to rediscover the literal.
  `Reach` governs the client enum and nothing else; promotion
  (`MESSAGE_PREFIX_CODES`) deliberately does not consult it.

  **`tckdb-client` 0.39.0 → 0.40.0**, and three `RejectionCode` members are
  removed: `TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH`,
  `APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH` and
  `IDEMPOTENCY_IN_PROGRESS`. Removing a member is breaking for an importer
  even when the code was unreachable, hence the minor bump. **No code changes
  value**, and all three keep a catalogue entry stating why no request reaches
  them. The two ownership guards stay in the code: against a bug five lines up
  they are a cheap tripwire, and in a database where a mis-attached
  calculation is a scientific error rather than a crash that is worth keeping.
  `idempotency_in_progress` is a contingency, not a milestone —
  `docs/specs/upload-idempotency-key-spec.md` lists it under *Optional* ("Only
  implement `idempotency_in_progress` if needed by the chosen approach") and
  the approach chosen does not need it; `app/api/idempotency.py` used to say
  "for v0", which does not distinguish a deferral from a decision, and now
  says which one it is.

- **A refusal that was recorded as unreachable is reachable, and is now
  tested.** `statmech_torsion_scan_calculation_owner_mismatch` was classified
  alongside the two above on the rule "an ownership guard is reachable exactly
  when the field it guards accepts a foreign row id". That rule is
  incomplete: a guard is also reachable when its key resolves in a namespace
  wider than the target's owner. `_persist_statmech_block` is shared by the
  species bundle, where the calc-key map is one species entry's own, and the
  PDep bundle, where it spans every species and transition state — and the
  PDep schema narrows a *species* statmech's keys to that species's own
  calculations but does not do the same for a *transition state*'s. A TS
  torsion naming a species-owned rotor scan therefore reaches the guard.
  Measured on the wire at `POST /api/v1/uploads/networks/pdep`; the code
  stays exported and `tests/api/test_api_network_pdep_ownership.py` provokes
  it, and the sibling `statmech_source_calculation_owner_mismatch`, through
  the route.

- **Two refusals stopped naming the function they were raised in.** The
  `detail` of the group-additivity missing-thermo guard and of the two keyset
  argument guards used to begin `create_applied_group_additivity: ` and
  `keyset_predicate: ` — the enclosing function in the position a client reads
  as a code. #164 stopped either being *promoted* into the `code` field; this
  reworks the messages themselves, so what a caller reads says what went wrong.
  Both entries are gone from `app/api/code_catalogue.py`, because a message
  with no token in the code position is not a code by any spelling.
  **No `RejectionCode` member is removed and `tckdb-client` is unchanged at
  0.38.0**: an `accidental_prefix` entry was never client-facing, so neither
  string was ever generated into the enum — checked with
  `generate_client_rejection_codes.py --check`, which reports the committed
  file up to date. Neither code was ever emitted, either: the runtime observer
  recorded 101 distinct `(status, code)` pairs across all three gates and
  neither appears. Both guards are reachable only by a direct programmatic
  call — `persist_thermo_upload` passes a row it has just flushed, so
  `session.get` cannot return `None` from any HTTP path — and the catalogue
  said otherwise, which is corrected.
- **The origin guard can now tell a code from a function of the same name.**
  `test_every_origin_still_defines_its_code` matched the code anywhere a
  double quote preceded it, so `"keyset_predicate"` in `__all__` satisfied the
  entry for `keyset_predicate` — the guard was blindest exactly where that
  class of defect lives, and #164 reworded that message with the guard staying
  green. Matching is now by syntactic position, shared with the catalogue's
  closure scan so the two cannot drift. That scan also reads `*_code=`
  arguments at any call, not only at a `raise`, which brings the six
  `*_handle_conflict` codes into static view (five of them are emitted by no
  test) and revealed one uncatalogued code, `database_error` — a dead
  `fallback_code` in the operational-error handler, now listed and annotated.
- `backend/pyproject.toml` now declares the repository's actual MIT license
  instead of `TBD — see repository root`.

### Notes

- **No DOI is minted.** The release machinery, manifest and checksums are
  implemented; depositing a release and recording its DOI is a documented
  manual step (`backend/docs/deployment/cutting_a_dataset_release.md`) to be
  run when a paper tag is cut. A DOI is not retractable and the corpus is not
  yet publishable.

## Earlier work

Before this file existed, changes were tracked only in the git history and in
`docs/decisions/`. Notable recent milestones, newest first:

- Stage 2 — scientific integrity blockers closed (#66).
- Execution-environment manifests recorded as provenance, not graded (#62, #64).
- Durable leases and heartbeats for async upload jobs.
- PDep scientific-integrity hardening: explicit pathway identity and solve
  inputs.
- Atom-resolved isotope identity for geometries and species entries.
- Raw artifact downloads gated behind authentication (ADR 0004, #48).
- Single-point energy and Cartesian Hessian extraction from uploaded ESS
  artifacts (#49, #51, #52).
- Lossless `tckdb.archive.v1` operator archive and restore path.
- Reproducibility assessments as an append-only curation projection.
