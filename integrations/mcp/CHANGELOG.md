# Changelog

## 0.15.0 - 2026-10-26

The thermo and kinetics select tools take the full provenance filters.

- `tckdb_select_species_thermo` and `tckdb_select_reaction_kinetics` accept `filters` of `model_kind`, `scientific_origin`, `level_of_theory_ref`,
  `method`, `basis`, `software`, `software_version`, `workflow_tool` and `workflow_tool_version`. ONE source calculation has to match every provenance
  field supplied (until now level of theory and software were matched through any calculation each, independently). They also take
  `method_family` (one family name or a list) and `dft_rung_min` / `dft_rung_max` (integers 1 to 5), a classification of the method and not an accuracy measure.
- `tckdb_select_statmech_models` accepts `filters.scientific_origin`.
- `tckdb_statmech_search` accepts `scientific_origin` (`computed`, `experimental` or `estimated`), matched on the record itself.

## 0.14.0 - 2026-10-25

The paged search tools take `cursor`.

- `tckdb_search_species`, `tckdb_get_species_entry_thermo`, `tckdb_search_reactions`, `tckdb_search_thermo`,
  `tckdb_get_reaction_entry_kinetics`, `tckdb_search_kinetics` and the scientific search and literature-record tools accept `cursor`: the previous response's `next_cursor`.
  The answer is returned unchanged, so `next_cursor` (null on the last page) and `pagination.mode` reach the agent.
- `cursor` cannot be combined with a non-zero `offset`; the tool refuses it before calling the server. `offset` is random access and can
  skip or repeat a record when the database changes between pages, so an agent that walks a whole list should follow the cursor.

## 0.13.0 - 2026-10-24

The `tckdb_select_*` tools take `filters` and `eligibility`.

- `filters` narrows the candidates by facts before anything is judged, with the matching search's vocabulary (thermo and kinetics:
  `model_kind`, `level_of_theory_ref`, `software`; network and statmech: `model_kind` and the six provenance fields; the structure tools:
  the provenance fields). `eligibility` lifts named defaults (`include_rejected`, `include_deprecated`, and where the record has them
  `include_hard_failed_evidence`, `include_confirmed_findings`). An unknown filter or default is refused here with a teaching error.
- Both are forwarded unchanged and the server's answer is returned unchanged: its `filters`, `eligibility` and `funnel` fields say what each step
  did. The `curated` profile cannot be overridden.

## 0.12.0 - 2026-10-23

List reads no longer pick a record.

- `tckdb_search_species`, `tckdb_search_reactions`, `tckdb_search_thermo`, `tckdb_search_kinetics`, `tckdb_get_species_entry_thermo` and
  `tckdb_get_reaction_entry_kinetics` lose `collapse`. Every matching record is returned in display order; for one record under a stated
  policy use the `tckdb_select_*` tools.
- On the four thermo and kinetics tools, `temperature_min` / `temperature_max` are now `coverage_temperature_min_k` /
  `coverage_temperature_max_k`: they never filtered, they only set the window `temperature_coverage` is computed against. `pressure` on the
  kinetics tools is unchanged (it does filter).

## 0.11.0 - 2026-10-22

The selection tools follow the removal of the method-preference rules.

- `tckdb_select_species_entry_thermo`, `tckdb_select_reaction_entry_kinetics`, `tckdb_select_network_kinetics`: `policy` is `default` (the
  default), `most_reviewed` or `latest`; `method_preferred` is retired. Every policy is an administrative display order. The descriptions say that
  `incomparable_alternatives` means several records are eligible and none is preferred, and that an administrative selection is first in display order.
- `tckdb_select_network_kinetics` drops `objective`, `reference_model_ref` and `reference_outputs`.
- The structure selection tools drop `apply_rules`, `objective` and `reference_model`; `protocol_coverage` lists the protocols that cover every
  requested member in display order.
- `tckdb_select_statmech_models` drops `comparison_objective` and `reference_model_ref`.

## 0.10.0 - 2026-10-18

Two read-only tools for relative conformer energies: `tckdb_species_entry_relative_conformer_energies` and
`tckdb_compare_relative_conformer_energies`, wrapping `POST .../species-entries/{spe_ref}/conformers/relative-energies` and
`.../compare`. The reference conformer (`cg_`), energy kind, certificate and the level (exactly one of `lot_` and `csch_`) are
required with no default; the comparison also requires `candidate`, `reference` and `pairing`. Ref prefixes are checked before any
request. The response is returned unchanged; quote `outcome`, `basis` and `claims.statement` verbatim. The comparison is
descriptive only and every deviation depends on the named reference conformer.
