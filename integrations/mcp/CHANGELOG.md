# Changelog

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
