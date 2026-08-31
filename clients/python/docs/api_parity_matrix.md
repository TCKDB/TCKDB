# TCKDB Python client — API parity matrix

<!-- GENERATED FILE — do not edit by hand.
     Source: clients/python/src/tckdb_client/_parity.py
     Regenerate: python scripts/generate_parity_matrix.py
     Enforced by: tests/test_parity_matrix_doc.py -->

Every operation in the backend's OpenAPI document (`backend/tests/api/golden/openapi.json`) is triaged here exactly once. `tests/test_openapi_parity.py` fails when the backend gains an operation that has not been classified, so this table cannot silently fall behind the API.

**Every** operation is reachable over raw HTTP via `TCKDBClient.request_json()` / `get_json()` / `post_json()` — the client never hides an endpoint. The `typed client` column records where a first-class method exists on top of that.

| Classification | Operations |
|---|---|
| typed | 96 |
| raw_only | 106 |
| not_applicable | 39 |
| **total** | **241** |

## Typed coverage

A first-class client method exists for these operations.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/auth/me` | yes | `me` | — | — | `tests/test_client.py` |
| `POST /api/v1/bundles/dry-run` | yes | `bundle_dry_run` | — | `examples/submit_bundle.py` | `tests/test_client.py` |
| `POST /api/v1/bundles/submit` | yes | `bundle_submit` | — | `examples/submit_bundle.py` | `tests/test_client.py` |
| `POST /api/v1/calculations/{calculation_id}/artifacts` | yes | `upload_artifacts` | — | — | `tests/test_upload_artifacts_batch.py` |
| `GET /api/v1/health` | yes | `health` | — | — | `tests/test_client.py` |
| `POST /api/v1/jobs/computed-reaction` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/conformer` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/kinetics` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/network` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/network/pdep` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/reaction` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/thermo` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/transition-state` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/jobs/transport` | yes | `enqueue_job` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/jobs/{job_id}` | yes | `get_job_status` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/meta` | yes | `get_meta` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/readyz` | yes | `readyz` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/analytics/calculations` | yes | `search_calculation_analytics` | `iter_calculation_analytics` | `examples/query_cookbook.py` | `tests/test_scientific_analytics.py` |
| `GET /api/v1/scientific/analytics/kinetics` | yes | `search_kinetics_analytics` | `iter_kinetics_analytics` | `examples/query_cookbook.py` | `tests/test_scientific_analytics.py` |
| `GET /api/v1/scientific/analytics/statmech` | yes | `search_statmech_analytics` | `iter_statmech_analytics` | `examples/query_cookbook.py` | `tests/test_scientific_analytics.py` |
| `GET /api/v1/scientific/analytics/thermo` | yes | `search_thermo_analytics` | `iter_thermo_analytics` | `examples/query_cookbook.py` | `tests/test_scientific_analytics.py` |
| `GET /api/v1/scientific/artifacts/search` | yes | `search_artifacts` | `iter_artifacts` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/artifacts/search` | yes | `search_artifacts` | `iter_artifacts` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/artifacts/{sha256}/download` | yes | `download_artifact` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/calculations/search` | yes | `search_calculations` | — | — | `tests/test_scientific_search.py` |
| `POST /api/v1/scientific/calculations/search` | yes | `search_calculations` | — | — | `tests/test_scientific_search.py` |
| `GET /api/v1/scientific/calculations/{calculation_ref_or_id}` | yes | `get_calculation` | — | — | `tests/test_scientific_search.py` |
| `GET /api/v1/scientific/calculations/{calculation_ref_or_id}/irc` | yes | `get_calculation_irc` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/calculations/{calculation_ref_or_id}/path-search` | yes | `get_calculation_path_search` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/calculations/{calculation_ref_or_id}/scan` | yes | `get_calculation_scan` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/conformer-groups/{conformer_group_ref_or_id}` | yes | `get_conformer_group` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/conformer-observations/{conformer_observation_ref_or_id}` | yes | `get_conformer_observation` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/conformers/search` | yes | `search_conformers` | `iter_conformers` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/conformers/search` | yes | `search_conformers` | `iter_conformers` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/energy-correction-schemes/search` | yes | `search_energy_correction_schemes` | `iter_energy_correction_schemes` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/energy-correction-schemes/search` | yes | `search_energy_correction_schemes` | `iter_energy_correction_schemes` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}` | yes | `get_energy_correction_scheme` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/export/chemkin` | yes | `export_chemkin` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/export/ml/reactions.ndjson` | yes | `export_ml_reactions` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/export/ml/species.ndjson` | yes | `export_ml_species` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/export/ndjson` | yes | `export_ndjson` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/frequency-scale-factors/search` | yes | `search_frequency_scale_factors` | `iter_frequency_scale_factors` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/frequency-scale-factors/search` | yes | `search_frequency_scale_factors` | `iter_frequency_scale_factors` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}` | yes | `get_frequency_scale_factor` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/geometries/{geometry_handle}` | yes | `get_geometry` | — | `examples/query_cookbook.py` | `tests/test_get_geometry.py` |
| `GET /api/v1/scientific/kinetics/search` | yes | `search_kinetics` | `iter_kinetics` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `POST /api/v1/scientific/kinetics/search` | yes | `search_kinetics` | `iter_kinetics` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `GET /api/v1/scientific/literature/{literature_ref_or_id}` | yes | `get_literature` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/literature/{literature_ref_or_id}/records` | yes | `get_literature_records` | `iter_literature_records` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/basis-sets` | yes | `get_meta_basis_sets` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/methods` | yes | `get_meta_methods` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/reaction-families` | yes | `get_meta_reaction_families` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/software` | yes | `get_meta_software` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/software-versions` | yes | `get_meta_software_versions` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/workflow-tool-versions` | yes | `get_meta_workflow_tool_versions` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/meta/workflow-tools` | yes | `get_meta_workflow_tools` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/network-kinetics/search` | yes | `search_network_kinetics` | `iter_network_kinetics` | — | `tests/test_typed_scientific.py` |
| `POST /api/v1/scientific/network-kinetics/search` | yes | `search_network_kinetics` | `iter_network_kinetics` | — | `tests/test_typed_scientific.py` |
| `GET /api/v1/scientific/network-solves/search` | yes | `search_network_solves` | `iter_network_solves` | — | `tests/test_network_solve_client.py` |
| `POST /api/v1/scientific/network-solves/search` | yes | `search_network_solves` | `iter_network_solves` | — | `tests/test_network_solve_client.py` |
| `GET /api/v1/scientific/network-solves/{network_solve_ref_or_id}` | yes | `get_network_solve` | — | — | `tests/test_network_solve_client.py` |
| `GET /api/v1/scientific/networks/search` | yes | `search_networks` | `iter_networks` | — | `tests/test_typed_scientific.py` |
| `POST /api/v1/scientific/networks/search` | yes | `search_networks` | `iter_networks` | — | `tests/test_typed_scientific.py` |
| `GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/full` | yes | `get_reaction_full` | — | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics` | yes | `get_reaction_kinetics` | — | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `GET /api/v1/scientific/reactions/search` | yes | `search_reactions` | `iter_reactions` | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `POST /api/v1/scientific/reactions/search` | yes | `search_reactions` | `iter_reactions` | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `GET /api/v1/scientific/species-calculations/search` | yes | `search_species_calculations` | `iter_species_calculations` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `POST /api/v1/scientific/species-calculations/search` | yes | `search_species_calculations` | `iter_species_calculations` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `GET /api/v1/scientific/species-entries/{species_entry_id}/thermo` | yes | `get_species_thermo` | — | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `GET /api/v1/scientific/species/browse` | yes | `browse_species` | `iter_species_browse` | — | `tests/test_scientific.py` |
| `GET /api/v1/scientific/species/search` | yes | `search_species` | `iter_species` | `examples/scientific_reads.py` | `tests/test_scientific.py` |
| `GET /api/v1/scientific/species/structure-search` | yes | `search_species_structures` | `iter_species_structures` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/species/structure-search` | yes | `search_species_structures` | `iter_species_structures` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/statmech/search` | yes | `search_statmech` | `iter_statmech` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/statmech/search` | yes | `search_statmech` | `iter_statmech` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/thermo/search` | yes | `search_thermo` | `iter_thermo` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `POST /api/v1/scientific/thermo/search` | yes | `search_thermo` | `iter_thermo` | `examples/query_cookbook.py` | `tests/test_scientific_search.py` |
| `GET /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}` | yes | `get_transition_state_entry` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/transition-states/search` | yes | `search_transition_states` | `iter_transition_states` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/transition-states/search` | yes | `search_transition_states` | `iter_transition_states` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/transition-states/{transition_state_ref_or_id}` | yes | `get_transition_state` | — | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/scientific/transport/search` | yes | `search_transport` | `iter_transport` | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/scientific/transport/search` | yes | `search_transport` | `iter_transport` | — | `tests/test_typed_parity_methods.py` |
| `GET /api/v1/status` | yes | `status` | — | — | `tests/test_typed_parity_methods.py` |
| `POST /api/v1/uploads/computed-reaction` | yes | `upload` | — | `examples/builder_computed_reaction_demo.py` | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/computed-species` | yes | `upload` | — | `examples/builder_computed_species_demo.py` | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/conformers` | yes | `upload` | — | `examples/upload_json_file.py` | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/kinetics` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/networks` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/networks/pdep` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/reactions` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/statmech` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/thermo` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/transition-states` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |
| `POST /api/v1/uploads/transport` | yes | `upload` | — | — | `tests/test_client_upload_dispatch.py` |

## Raw HTTP only

Deliberately reachable only through the generic request helpers. Each group states why a typed method would not earn its keep.

> Legacy per-table entity read superseded by the /scientific/* read contract, which returns the same data with provenance and review state attached. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/applied-energy-corrections` | yes | — | — | — | — |
| `GET /api/v1/applied-energy-corrections/{correction_id}` | yes | — | — | — | — |
| `GET /api/v1/calculations` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/artifacts` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/constraints` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/dependencies` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/freq-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/geometry-validation` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/input-geometries` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/irc-points` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/irc-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/opt-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/output-geometries` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/parameters` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/path-search-points` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/path-search-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/scan-coordinates` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/scan-points` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/scan-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/scf-stability` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/sp-result` | yes | — | — | — | — |
| `GET /api/v1/calculations/{calculation_id}/wavefunction-diagnostic` | yes | — | — | — | — |
| `GET /api/v1/conformer-groups` | yes | — | — | — | — |
| `GET /api/v1/conformer-groups/{group_id}` | yes | — | — | — | — |
| `GET /api/v1/conformer-groups/{group_id}/selections` | yes | — | — | — | — |
| `GET /api/v1/conformer-observations` | yes | — | — | — | — |
| `GET /api/v1/conformer-observations/{observation_id}` | yes | — | — | — | — |
| `GET /api/v1/energy-correction-schemes` | yes | — | — | — | — |
| `GET /api/v1/energy-correction-schemes/{scheme_id}` | yes | — | — | — | — |
| `GET /api/v1/frequency-scale-factors` | yes | — | — | — | — |
| `GET /api/v1/frequency-scale-factors/{fsf_id}` | yes | — | — | — | — |
| `GET /api/v1/geometries` | yes | — | — | — | — |
| `GET /api/v1/geometries/{geometry_id}` | yes | — | — | — | — |
| `GET /api/v1/kinetics` | yes | — | — | — | — |
| `GET /api/v1/kinetics/{kinetics_id}` | yes | — | — | — | — |
| `GET /api/v1/levels-of-theory` | yes | — | — | — | — |
| `GET /api/v1/levels-of-theory/{lot_id}` | yes | — | — | — | — |
| `GET /api/v1/literature` | yes | — | — | — | — |
| `GET /api/v1/literature/{literature_id}` | yes | — | — | — | — |
| `GET /api/v1/networks` | yes | — | — | — | — |
| `GET /api/v1/networks/{network_id}` | yes | — | — | — | — |
| `GET /api/v1/networks/{network_id}/channels/{channel_id}/kinetics` | yes | — | — | — | — |
| `GET /api/v1/networks/{network_id}/solves` | yes | — | — | — | — |
| `GET /api/v1/networks/{network_id}/solves/{solve_id}` | yes | — | — | — | — |
| `GET /api/v1/reaction-entries/{entry_id}` | yes | — | — | — | — |
| `GET /api/v1/reaction-entries/{entry_id}/kinetics` | yes | — | — | — | — |
| `GET /api/v1/reaction-entries/{entry_id}/transition-states` | yes | — | — | — | — |
| `GET /api/v1/reactions` | yes | — | — | — | — |
| `GET /api/v1/reactions/{reaction_id}` | yes | — | — | — | — |
| `GET /api/v1/software` | yes | — | — | — | — |
| `GET /api/v1/software-releases` | yes | — | — | — | — |
| `GET /api/v1/software-releases/{release_id}` | yes | — | — | — | — |
| `GET /api/v1/software/{software_id}` | yes | — | — | — | — |
| `GET /api/v1/species` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/conformer-groups` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/conformer-observations/lowest-sp` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/conformers` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/statmech` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/thermo` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{entry_id}/transport` | yes | — | — | — | — |
| `GET /api/v1/species/{species_id}` | yes | — | — | — | — |
| `GET /api/v1/statmech` | yes | — | — | — | — |
| `GET /api/v1/statmech/{statmech_id}` | yes | — | — | — | — |
| `GET /api/v1/thermo` | yes | — | — | — | — |
| `GET /api/v1/thermo/{thermo_id}` | yes | — | — | — | — |
| `GET /api/v1/transition-states` | yes | — | — | — | — |
| `GET /api/v1/transition-states/entries/{entry_id}` | yes | — | — | — | — |
| `GET /api/v1/transition-states/{ts_id}` | yes | — | — | — | — |
| `GET /api/v1/transport` | yes | — | — | — | — |
| `GET /api/v1/transport/{transport_id}` | yes | — | — | — | — |
| `GET /api/v1/workflow-tool-releases` | yes | — | — | — | — |
| `GET /api/v1/workflow-tool-releases/{workflow_tool_release_id}` | yes | — | — | — | — |
| `GET /api/v1/workflow-tools` | yes | — | — | — | — |
| `GET /api/v1/workflow-tools/{workflow_tool_id}` | yes | — | — | — | — |

> Lookup endpoints return a deliberately loose {query, match, results} envelope whose shape is client-agnostic by design; binding it to a typed method would freeze a contract that is meant to stay open. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/lookup/calculations` | yes | — | — | — | — |
| `GET /api/v1/lookup/geometry` | yes | — | — | — | — |
| `GET /api/v1/lookup/kinetics` | yes | — | — | — | — |
| `GET /api/v1/lookup/network` | yes | — | — | — | — |
| `GET /api/v1/lookup/reaction` | yes | — | — | — | — |
| `GET /api/v1/lookup/reaction-kinetics` | yes | — | — | — | — |
| `GET /api/v1/lookup/species` | yes | — | — | — | — |
| `GET /api/v1/lookup/species-calculation` | yes | — | — | — | — |
| `GET /api/v1/lookup/statmech` | yes | — | — | — | — |
| `GET /api/v1/lookup/thermo` | yes | — | — | — | — |
| `GET /api/v1/lookup/transport` | yes | — | — | — | — |

> Custody of the deployment's own stored objects: expected against observed digest and size, and the object store's ETag and paths at the moment of detection. Curator/admin only, and operational detail about a bucket rather than science about a record, so a typed method on a contributor client would be aimed at the wrong reader. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/artifacts/integrity` | yes | — | — | — | — |

> Full observation history for one stored object, oldest first, for an operator deciding whether to trust a bucket. Same audience and same reasoning as the integrity listing above. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/artifacts/{sha256}/integrity` | yes | — | — | — | — |

> Single-record read of a row already returned in full by search_network_kinetics(network_kinetics_ref=...). Reachable via get_json() when only the ref is known.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/network-kinetics/{network_kinetics_ref_or_id}` | yes | — | — | — | — |

> Single-record read of a row already returned in full by search_networks(network_ref=...). Reachable via get_json() when only the ref is known.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/networks/{network_ref_or_id}` | yes | — | — | — | — |

> Dataset-release surface is still stabilising (DR-0026); binding it typed now would lock in a shape that is expected to change. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/releases` | yes | — | — | — | — |
| `GET /api/v1/scientific/releases/{release_handle}` | yes | — | — | — | — |
| `GET /api/v1/scientific/releases/{release_handle}/artifacts/{artifact_path}` | yes | — | — | — | — |
| `GET /api/v1/scientific/releases/{release_handle}/manifest` | yes | — | — | — | — |
| `GET /api/v1/scientific/releases/{release_handle}/selections` | yes | — | — | — | — |

> Species-entry statmech subresource; the equivalent query is search_statmech(species_entry_ref=...), which also carries evidence and provenance. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/species-entries/{species_entry_id}/statmech` | yes | — | — | — | — |

> Species-entry transport subresource; the equivalent query is search_transport(species_entry_ref=...), which also carries evidence and provenance. Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/species-entries/{species_entry_id}/transport` | yes | — | — | — | — |

> Single-record read of a row already returned in full by search_statmech(statmech_ref=...). Reachable via get_json() when only the ref is known.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/statmech/{statmech_ref_or_id}` | yes | — | — | — | — |

> Identifier-free catalogue listing for the hosted web Browse page, sibling to search_transition_states() rather than a replacement for it -- same record shape, no owner/parent ref filters. Its audience is the Browse page's own frontend, not the producer/consumer scripts this client serves, which already reach the same records through search_transition_states(). Reachable via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/transition-states/browse` | yes | — | — | — | — |

> Single-record read of a row already returned in full by search_transport(transport_ref=...). Reachable via get_json() when only the ref is known.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/scientific/transport/{transport_ref_or_id}` | yes | — | — | — | — |

> Contributor submission bookkeeping, not scientific data. The client returns submission ids from uploads; reading the submission back is an occasional operator action via get_json().

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/submissions/mine` | yes | — | — | — | — |
| `GET /api/v1/submissions/{submission_id}` | yes | — | — | — | — |
| `GET /api/v1/submissions/{submission_id}/ai-review-summary` | yes | — | — | — | — |
| `GET /api/v1/submissions/{submission_id}/audit-events` | yes | — | — | — | — |
| `GET /api/v1/submissions/{submission_id}/record-links` | yes | — | — | — | — |

## Not applicable

Admin, auth, and curator-internal surface that a producer/consumer client is not meant to drive.

> Admin-only operation, gated on the admin role. Out of scope for the contributor/consumer client.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/admin/artifact-storage/capacity` | yes | — | — | — | — |
| `POST /api/v1/admin/artifact-storage/capacity/clear` | yes | — | — | — | — |
| `GET /api/v1/admin/machine-review/curator-tasks` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/curator-tasks/build-for-submission/{submission_id}` | yes | — | — | — | — |
| `GET /api/v1/admin/machine-review/curator-tasks/{task_id}` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/curator-tasks/{task_id}/assign` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/curator-tasks/{task_id}/reopen` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/curator-tasks/{task_id}/resolve` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/curator-tasks/{task_id}/start-review` | yes | — | — | — | — |
| `POST /api/v1/admin/machine-review/records/{record_type}/{record_id}/run-fake` | yes | — | — | — | — |
| `GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection` | yes | — | — | — | — |
| `PATCH /api/v1/admin/users/{user_id}/role` | yes | — | — | — | — |

> Interactive credential and session management. The client only ever carries a pre-minted API key; issuing or revoking one is an out-of-band operator action.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `GET /api/v1/auth/api-keys` | yes | — | — | — | — |
| `POST /api/v1/auth/api-keys` | yes | — | — | — | — |
| `DELETE /api/v1/auth/api-keys/{key_id}` | yes | — | — | — | — |
| `POST /api/v1/auth/login` | yes | — | — | — | — |
| `POST /api/v1/auth/logout` | yes | — | — | — | — |
| `POST /api/v1/auth/register` | yes | — | — | — | — |

> Curator/reviewer workflow. Driving review state from a producer-side upload client would let a contributor grade their own submission.

| Operation | raw HTTP | typed client | iterator | example | contract test |
|---|---|---|---|---|---|
| `POST /api/v1/conformer-groups/{conformer_group_id}/selections` | yes | — | — | — | — |
| `POST /api/v1/curation/reproducibility-assessments/{record_type}/{record_id}/evaluate` | yes | — | — | — | — |
| `GET /api/v1/curation/reproducibility-assessments/{record_type}/{record_id}/latest` | yes | — | — | — | — |
| `POST /api/v1/curation/scientific-record-supersessions` | yes | — | — | — | — |
| `GET /api/v1/record-reviews` | yes | — | — | — | — |
| `GET /api/v1/record-reviews/{record_type}/{record_id}` | yes | — | — | — | — |
| `PATCH /api/v1/record-reviews/{record_type}/{record_id}` | yes | — | — | — | — |
| `POST /api/v1/releases` | yes | — | — | — | — |
| `POST /api/v1/releases/policies` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/doi` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/publish` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/selections` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/selections/{selection_ref}/supersede` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/selections/{selection_ref}/withdraw` | yes | — | — | — | — |
| `POST /api/v1/releases/{release_handle}/withdraw` | yes | — | — | — | — |
| `GET /api/v1/species-entries/{species_entry_id}/reviews` | yes | — | — | — | — |
| `POST /api/v1/species-entries/{species_entry_id}/reviews` | yes | — | — | — | — |
| `GET /api/v1/submissions/for-review` | yes | — | — | — | — |
| `POST /api/v1/submissions/{submission_id}/approve` | yes | — | — | — | — |
| `POST /api/v1/submissions/{submission_id}/reject` | yes | — | — | — | — |
| `POST /api/v1/submissions/{submission_id}/supersede` | yes | — | — | — | — |
