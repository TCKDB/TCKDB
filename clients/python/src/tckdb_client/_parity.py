"""Declared client coverage for every backend API operation.

Why this exists
---------------
The backend grows faster than the client. Without a checked-in ledger,
new endpoints land and nobody notices the Python client never grew a way
to reach them — the gap is only discovered by a user who needed it.

This module is that ledger. Every operation in the backend's OpenAPI
document must appear here exactly once, classified as one of:

``typed``
    A typed client method exists and is named.
``raw_only``
    Deliberately reachable only through ``get_json`` / ``post_json`` /
    ``request_json``. Requires a reason.
``not_applicable``
    Admin, auth, or curator-internal surface that the producer/consumer
    client is not meant to drive. Requires a reason.

``tests/test_openapi_parity.py`` compares this map against
``backend/tests/api/golden/openapi.json`` and **fails** when the backend
gains an operation nobody has triaged. The fix for that failure is to add
an entry here — either a new typed method, or an explicit decision that
raw HTTP is enough and why.

Paths are stored exactly as the OpenAPI document spells them, including
the ``/api/v1`` prefix. Client methods take the suffix, because
``TCKDBClient.base_url`` already carries the prefix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

CoverageStatus = Literal["typed", "raw_only", "not_applicable"]

_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


@dataclass(frozen=True)
class OperationCoverage:
    """How one backend operation is (or is deliberately not) surfaced."""

    method: str
    path: str
    status: CoverageStatus
    client_method: str | None = None
    iterator: str | None = None
    example: str | None = None
    contract_test: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.method != self.method.upper():
            raise ValueError(f"{self.key}: method must be upper-case.")
        if not self.path.startswith("/"):
            raise ValueError(f"{self.key}: path must start with '/'.")
        if self.status == "typed":
            if not self.client_method:
                raise ValueError(f"{self.key}: typed coverage must name a client method.")
            if not self.contract_test:
                raise ValueError(f"{self.key}: typed coverage must name a contract test.")
            if self.reason:
                raise ValueError(f"{self.key}: typed coverage does not take a reason.")
        else:
            if not self.reason:
                raise ValueError(f"{self.key}: {self.status} coverage must give a reason.")
            if self.client_method or self.iterator:
                raise ValueError(
                    f"{self.key}: {self.status} coverage must not name a client method."
                )

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def operation(self) -> str:
        return f"{self.method} {self.path}"


# ---------------------------------------------------------------------------
# Reasons, spelled once so the ledger stays honest about *why*
# ---------------------------------------------------------------------------

_LEGACY_ENTITY = (
    "Legacy per-table entity read superseded by the /scientific/* read "
    "contract, which returns the same data with provenance and review "
    "state attached. Reachable via get_json()."
)
_LOOKUP_ENVELOPE = (
    "Lookup endpoints return a deliberately loose {query, match, results} "
    "envelope whose shape is client-agnostic by design; binding it to a "
    "typed method would freeze a contract that is meant to stay open. "
    "Reachable via get_json()."
)
_SUBMISSION_READ = (
    "Contributor submission bookkeeping, not scientific data. The client "
    "returns submission ids from uploads; reading the submission back is "
    "an occasional operator action via get_json()."
)
_RELEASE_READ = (
    "Dataset-release surface is still stabilising (DR-0026); binding it "
    "typed now would lock in a shape that is expected to change. "
    "Reachable via get_json()."
)
_CURATION_WORKFLOW = (
    "Curator/reviewer workflow. Driving review state from a producer-side "
    "upload client would let a contributor grade their own submission."
)
_ADMIN_ONLY = (
    "Admin-only operation, gated on the admin role. Out of scope for the "
    "contributor/consumer client."
)
_AUTH_SESSION = (
    "Interactive credential and session management. The client only ever "
    "carries a pre-minted API key; issuing or revoking one is an "
    "out-of-band operator action."
)

# ---------------------------------------------------------------------------
# typed: (method, path, client_method, iterator, example, contract_test)
# ---------------------------------------------------------------------------

_NEW_METHOD_TESTS = "tests/test_typed_parity_methods.py"
_ANALYTICS_TESTS = "tests/test_scientific_analytics.py"

_TYPED: tuple[tuple[str, str, str, str | None, str | None, str], ...] = (
    # --- Probes and deployment metadata -----------------------------
    ("GET", "/api/v1/health", "health", None, None, "tests/test_client.py"),
    ("GET", "/api/v1/readyz", "readyz", None, None, _NEW_METHOD_TESTS),
    ("GET", "/api/v1/status", "status", None, None, _NEW_METHOD_TESTS),
    ("GET", "/api/v1/meta", "get_meta", None, None, _NEW_METHOD_TESTS),
    ("GET", "/api/v1/auth/me", "me", None, None, "tests/test_client.py"),
    # --- Contribution bundles ---------------------------------------
    (
        "POST", "/api/v1/bundles/dry-run", "bundle_dry_run", None,
        "examples/submit_bundle.py", "tests/test_client.py",
    ),
    (
        "POST", "/api/v1/bundles/submit", "bundle_submit", None,
        "examples/submit_bundle.py", "tests/test_client.py",
    ),
    # --- Synchronous uploads ----------------------------------------
    (
        "POST", "/api/v1/uploads/conformers", "upload", None,
        "examples/upload_json_file.py", "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/reactions", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/kinetics", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/thermo", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/statmech", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/transport", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/transition-states", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/networks", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/networks/pdep", "upload", None, None,
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/computed-reaction", "upload", None,
        "examples/builder_computed_reaction_demo.py",
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/uploads/computed-species", "upload", None,
        "examples/builder_computed_species_demo.py",
        "tests/test_client_upload_dispatch.py",
    ),
    (
        "POST", "/api/v1/calculations/{calculation_id}/artifacts",
        "upload_artifacts", None, None, "tests/test_upload_artifacts_batch.py",
    ),
    # --- Async job lifecycle ----------------------------------------
    (
        "POST", "/api/v1/jobs/computed-reaction", "enqueue_job", None, None,
        _NEW_METHOD_TESTS,
    ),
    ("POST", "/api/v1/jobs/conformer", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("POST", "/api/v1/jobs/kinetics", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("POST", "/api/v1/jobs/network", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("POST", "/api/v1/jobs/network/pdep", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("POST", "/api/v1/jobs/reaction", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("POST", "/api/v1/jobs/thermo", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    (
        "POST", "/api/v1/jobs/transition-state", "enqueue_job", None, None,
        _NEW_METHOD_TESTS,
    ),
    ("POST", "/api/v1/jobs/transport", "enqueue_job", None, None, _NEW_METHOD_TESTS),
    ("GET", "/api/v1/jobs/{job_id}", "get_job_status", None, None, _NEW_METHOD_TESTS),
    # --- Species and structure --------------------------------------
    (
        "GET", "/api/v1/scientific/species/search", "search_species",
        "iter_species", "examples/scientific_reads.py", "tests/test_scientific.py",
    ),
    (
        "GET", "/api/v1/scientific/species/structure-search",
        "search_species_structures", "iter_species_structures", None,
        _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/species/structure-search",
        "search_species_structures", "iter_species_structures", None,
        _NEW_METHOD_TESTS,
    ),
    # --- Reactions ---------------------------------------------------
    (
        "GET", "/api/v1/scientific/reactions/search", "search_reactions",
        "iter_reactions", "examples/scientific_reads.py", "tests/test_scientific.py",
    ),
    (
        "POST", "/api/v1/scientific/reactions/search", "search_reactions",
        "iter_reactions", "examples/scientific_reads.py", "tests/test_scientific.py",
    ),
    (
        "GET", "/api/v1/scientific/reaction-entries/{reaction_entry_id}/full",
        "get_reaction_full", None, "examples/scientific_reads.py",
        "tests/test_scientific.py",
    ),
    (
        "GET", "/api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics",
        "get_reaction_kinetics", None, "examples/scientific_reads.py",
        "tests/test_scientific.py",
    ),
    # --- Species-entry subresources ---------------------------------
    (
        "GET", "/api/v1/scientific/species-entries/{species_entry_id}/thermo",
        "get_species_thermo", None, "examples/scientific_reads.py",
        "tests/test_scientific.py",
    ),
    # --- Scientific product searches --------------------------------
    (
        "GET", "/api/v1/scientific/thermo/search", "search_thermo", "iter_thermo",
        "examples/query_cookbook.py", "tests/test_scientific_search.py",
    ),
    (
        "POST", "/api/v1/scientific/thermo/search", "search_thermo", "iter_thermo",
        "examples/query_cookbook.py", "tests/test_scientific_search.py",
    ),
    (
        "GET", "/api/v1/scientific/kinetics/search", "search_kinetics",
        "iter_kinetics", "examples/query_cookbook.py",
        "tests/test_scientific_search.py",
    ),
    (
        "POST", "/api/v1/scientific/kinetics/search", "search_kinetics",
        "iter_kinetics", "examples/query_cookbook.py",
        "tests/test_scientific_search.py",
    ),
    (
        "GET", "/api/v1/scientific/statmech/search", "search_statmech",
        "iter_statmech", None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/statmech/search", "search_statmech",
        "iter_statmech", None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/transport/search", "search_transport",
        "iter_transport", None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/transport/search", "search_transport",
        "iter_transport", None, _NEW_METHOD_TESTS,
    ),
    # --- Calculations ------------------------------------------------
    (
        "GET", "/api/v1/scientific/calculations/search", "search_calculations",
        None, None, "tests/test_scientific_search.py",
    ),
    (
        "POST", "/api/v1/scientific/calculations/search", "search_calculations",
        None, None, "tests/test_scientific_search.py",
    ),
    (
        "GET", "/api/v1/scientific/calculations/{calculation_ref_or_id}",
        "get_calculation", None, None, "tests/test_scientific_search.py",
    ),
    (
        "GET", "/api/v1/scientific/calculations/{calculation_ref_or_id}/irc",
        "get_calculation_irc", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/calculations/{calculation_ref_or_id}/scan",
        "get_calculation_scan", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/calculations/{calculation_ref_or_id}/path-search",
        "get_calculation_path_search", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/species-calculations/search",
        "search_species_calculations", "iter_species_calculations",
        "examples/query_cookbook.py", "tests/test_scientific_search.py",
    ),
    (
        "POST", "/api/v1/scientific/species-calculations/search",
        "search_species_calculations", "iter_species_calculations",
        "examples/query_cookbook.py", "tests/test_scientific_search.py",
    ),
    # --- Conformers --------------------------------------------------
    (
        "GET", "/api/v1/scientific/conformers/search", "search_conformers",
        "iter_conformers", None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/conformers/search", "search_conformers",
        "iter_conformers", None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/conformer-groups/{conformer_group_ref_or_id}",
        "get_conformer_group", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET",
        "/api/v1/scientific/conformer-observations/"
        "{conformer_observation_ref_or_id}",
        "get_conformer_observation", None, None, _NEW_METHOD_TESTS,
    ),
    # --- Transition states -------------------------------------------
    (
        "GET", "/api/v1/scientific/transition-states/search",
        "search_transition_states", "iter_transition_states", None,
        _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/transition-states/search",
        "search_transition_states", "iter_transition_states", None,
        _NEW_METHOD_TESTS,
    ),
    (
        "GET",
        "/api/v1/scientific/transition-states/{transition_state_ref_or_id}",
        "get_transition_state", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET",
        "/api/v1/scientific/transition-state-entries/"
        "{transition_state_entry_ref_or_id}",
        "get_transition_state_entry", None, None, _NEW_METHOD_TESTS,
    ),
    # --- Networks ----------------------------------------------------
    (
        "GET", "/api/v1/scientific/networks/search", "search_networks",
        "iter_networks", None, "tests/test_typed_scientific.py",
    ),
    (
        "POST", "/api/v1/scientific/networks/search", "search_networks",
        "iter_networks", None, "tests/test_typed_scientific.py",
    ),
    (
        "GET", "/api/v1/scientific/network-kinetics/search",
        "search_network_kinetics", "iter_network_kinetics", None,
        "tests/test_typed_scientific.py",
    ),
    (
        "POST", "/api/v1/scientific/network-kinetics/search",
        "search_network_kinetics", "iter_network_kinetics", None,
        "tests/test_typed_scientific.py",
    ),
    (
        "GET", "/api/v1/scientific/network-solves/search",
        "search_network_solves", "iter_network_solves", None,
        "tests/test_network_solve_client.py",
    ),
    (
        "POST", "/api/v1/scientific/network-solves/search",
        "search_network_solves", "iter_network_solves", None,
        "tests/test_network_solve_client.py",
    ),
    (
        "GET", "/api/v1/scientific/network-solves/{network_solve_ref_or_id}",
        "get_network_solve", None, None, "tests/test_network_solve_client.py",
    ),
    # --- Geometry ----------------------------------------------------
    (
        "GET", "/api/v1/scientific/geometries/{geometry_handle}", "get_geometry",
        None, "examples/query_cookbook.py", "tests/test_get_geometry.py",
    ),
    # --- Artifacts ---------------------------------------------------
    (
        "GET", "/api/v1/scientific/artifacts/search", "search_artifacts",
        "iter_artifacts", None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/artifacts/search", "search_artifacts",
        "iter_artifacts", None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/artifacts/{sha256}/download",
        "download_artifact", None, None, _NEW_METHOD_TESTS,
    ),
    # --- Reference libraries -----------------------------------------
    (
        "GET", "/api/v1/scientific/energy-correction-schemes/search",
        "search_energy_correction_schemes", "iter_energy_correction_schemes",
        None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/energy-correction-schemes/search",
        "search_energy_correction_schemes", "iter_energy_correction_schemes",
        None, _NEW_METHOD_TESTS,
    ),
    (
        "GET",
        "/api/v1/scientific/energy-correction-schemes/"
        "{energy_correction_scheme_ref_or_id}",
        "get_energy_correction_scheme", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/frequency-scale-factors/search",
        "search_frequency_scale_factors", "iter_frequency_scale_factors", None,
        _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/frequency-scale-factors/search",
        "search_frequency_scale_factors", "iter_frequency_scale_factors", None,
        _NEW_METHOD_TESTS,
    ),
    (
        "GET",
        "/api/v1/scientific/frequency-scale-factors/"
        "{frequency_scale_factor_ref_or_id}",
        "get_frequency_scale_factor", None, None, _NEW_METHOD_TESTS,
    ),
    # --- Literature ---------------------------------------------------
    (
        "GET", "/api/v1/scientific/literature/{literature_ref_or_id}",
        "get_literature", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/literature/{literature_ref_or_id}/records",
        "get_literature_records", "iter_literature_records", None,
        _NEW_METHOD_TESTS,
    ),
    # --- Vocabularies -------------------------------------------------
    (
        "GET", "/api/v1/scientific/meta/methods", "get_meta_methods", None, None,
        _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/meta/basis-sets", "get_meta_basis_sets", None,
        None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/meta/software", "get_meta_software", None, None,
        _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/meta/reaction-families",
        "get_meta_reaction_families", None, None, _NEW_METHOD_TESTS,
    ),
    # --- Analytics ----------------------------------------------------
    #
    # Typed rather than raw_only, deliberately. This surface exists for
    # quantitative dataset construction, which is the Python client's
    # primary job; leaving it raw-only would mean the one surface designed
    # for programmatic dataset building is the one surface the typed client
    # cannot drive. Each carries a keyset (cursor) iterator, because offset
    # paging over a live corpus can silently skip or duplicate rows and a
    # dataset built that way is not reproducible.
    (
        "GET", "/api/v1/scientific/analytics/kinetics",
        "search_kinetics_analytics", "iter_kinetics_analytics",
        "examples/query_cookbook.py", _ANALYTICS_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/analytics/thermo",
        "search_thermo_analytics", "iter_thermo_analytics",
        "examples/query_cookbook.py", _ANALYTICS_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/analytics/statmech",
        "search_statmech_analytics", "iter_statmech_analytics",
        "examples/query_cookbook.py", _ANALYTICS_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/analytics/calculations",
        "search_calculation_analytics", "iter_calculation_analytics",
        "examples/query_cookbook.py", _ANALYTICS_TESTS,
    ),
    # --- Exports ------------------------------------------------------
    (
        "GET", "/api/v1/scientific/export/ndjson", "export_ndjson", None, None,
        _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/export/ml/species.ndjson", "export_ml_species",
        None, None, _NEW_METHOD_TESTS,
    ),
    (
        "GET", "/api/v1/scientific/export/ml/reactions.ndjson",
        "export_ml_reactions", None, None, _NEW_METHOD_TESTS,
    ),
    (
        "POST", "/api/v1/scientific/export/chemkin", "export_chemkin", None, None,
        _NEW_METHOD_TESTS,
    ),
)

# ---------------------------------------------------------------------------
# raw_only: (method, path, reason)
# ---------------------------------------------------------------------------

_RAW_ONLY: tuple[tuple[str, str, str], ...] = tuple(
    (method, path, _LEGACY_ENTITY)
    for method, path in (
        ("GET", "/api/v1/applied-energy-corrections"),
        ("GET", "/api/v1/applied-energy-corrections/{correction_id}"),
        ("GET", "/api/v1/calculations"),
        ("GET", "/api/v1/calculations/{calculation_id}"),
        ("GET", "/api/v1/calculations/{calculation_id}/artifacts"),
        ("GET", "/api/v1/calculations/{calculation_id}/constraints"),
        ("GET", "/api/v1/calculations/{calculation_id}/dependencies"),
        ("GET", "/api/v1/calculations/{calculation_id}/freq-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/geometry-validation"),
        ("GET", "/api/v1/calculations/{calculation_id}/input-geometries"),
        ("GET", "/api/v1/calculations/{calculation_id}/irc-points"),
        ("GET", "/api/v1/calculations/{calculation_id}/irc-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/opt-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/output-geometries"),
        ("GET", "/api/v1/calculations/{calculation_id}/parameters"),
        ("GET", "/api/v1/calculations/{calculation_id}/path-search-points"),
        ("GET", "/api/v1/calculations/{calculation_id}/path-search-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/scan-coordinates"),
        ("GET", "/api/v1/calculations/{calculation_id}/scan-points"),
        ("GET", "/api/v1/calculations/{calculation_id}/scan-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/scf-stability"),
        ("GET", "/api/v1/calculations/{calculation_id}/sp-result"),
        ("GET", "/api/v1/calculations/{calculation_id}/wavefunction-diagnostic"),
        ("GET", "/api/v1/conformer-groups"),
        ("GET", "/api/v1/conformer-groups/{group_id}"),
        ("GET", "/api/v1/conformer-groups/{group_id}/selections"),
        ("GET", "/api/v1/conformer-observations"),
        ("GET", "/api/v1/conformer-observations/{observation_id}"),
        ("GET", "/api/v1/energy-correction-schemes"),
        ("GET", "/api/v1/energy-correction-schemes/{scheme_id}"),
        ("GET", "/api/v1/frequency-scale-factors"),
        ("GET", "/api/v1/frequency-scale-factors/{fsf_id}"),
        ("GET", "/api/v1/geometries"),
        ("GET", "/api/v1/geometries/{geometry_id}"),
        ("GET", "/api/v1/kinetics"),
        ("GET", "/api/v1/kinetics/{kinetics_id}"),
        ("GET", "/api/v1/levels-of-theory"),
        ("GET", "/api/v1/levels-of-theory/{lot_id}"),
        ("GET", "/api/v1/literature"),
        ("GET", "/api/v1/literature/{literature_id}"),
        ("GET", "/api/v1/networks"),
        ("GET", "/api/v1/networks/{network_id}"),
        (
            "GET",
            "/api/v1/networks/{network_id}/channels/{channel_id}/kinetics",
        ),
        ("GET", "/api/v1/networks/{network_id}/solves"),
        ("GET", "/api/v1/networks/{network_id}/solves/{solve_id}"),
        ("GET", "/api/v1/reaction-entries/{entry_id}"),
        ("GET", "/api/v1/reaction-entries/{entry_id}/kinetics"),
        ("GET", "/api/v1/reaction-entries/{entry_id}/transition-states"),
        ("GET", "/api/v1/reactions"),
        ("GET", "/api/v1/reactions/{reaction_id}"),
        ("GET", "/api/v1/software"),
        ("GET", "/api/v1/software-releases"),
        ("GET", "/api/v1/software-releases/{release_id}"),
        ("GET", "/api/v1/software/{software_id}"),
        ("GET", "/api/v1/species"),
        ("GET", "/api/v1/species-entries/{entry_id}"),
        ("GET", "/api/v1/species-entries/{entry_id}/conformer-groups"),
        (
            "GET",
            "/api/v1/species-entries/{entry_id}/conformer-observations/lowest-sp",
        ),
        ("GET", "/api/v1/species-entries/{entry_id}/conformers"),
        ("GET", "/api/v1/species-entries/{entry_id}/statmech"),
        ("GET", "/api/v1/species-entries/{entry_id}/thermo"),
        ("GET", "/api/v1/species-entries/{entry_id}/transport"),
        ("GET", "/api/v1/species/{species_id}"),
        ("GET", "/api/v1/statmech"),
        ("GET", "/api/v1/statmech/{statmech_id}"),
        ("GET", "/api/v1/thermo"),
        ("GET", "/api/v1/thermo/{thermo_id}"),
        ("GET", "/api/v1/transition-states"),
        ("GET", "/api/v1/transition-states/entries/{entry_id}"),
        ("GET", "/api/v1/transition-states/{ts_id}"),
        ("GET", "/api/v1/transport"),
        ("GET", "/api/v1/transport/{transport_id}"),
        ("GET", "/api/v1/workflow-tool-releases"),
        ("GET", "/api/v1/workflow-tool-releases/{workflow_tool_release_id}"),
        ("GET", "/api/v1/workflow-tools"),
        ("GET", "/api/v1/workflow-tools/{workflow_tool_id}"),
    )
) + tuple(
    (method, path, _LOOKUP_ENVELOPE)
    for method, path in (
        ("GET", "/api/v1/lookup/calculations"),
        ("GET", "/api/v1/lookup/geometry"),
        ("GET", "/api/v1/lookup/kinetics"),
        ("GET", "/api/v1/lookup/network"),
        ("GET", "/api/v1/lookup/reaction"),
        ("GET", "/api/v1/lookup/reaction-kinetics"),
        ("GET", "/api/v1/lookup/species"),
        ("GET", "/api/v1/lookup/species-calculation"),
        ("GET", "/api/v1/lookup/statmech"),
        ("GET", "/api/v1/lookup/thermo"),
        ("GET", "/api/v1/lookup/transport"),
    )
) + tuple(
    (method, path, _SUBMISSION_READ)
    for method, path in (
        ("GET", "/api/v1/submissions/mine"),
        ("GET", "/api/v1/submissions/{submission_id}"),
        ("GET", "/api/v1/submissions/{submission_id}/audit-events"),
        ("GET", "/api/v1/submissions/{submission_id}/record-links"),
        ("GET", "/api/v1/submissions/{submission_id}/ai-review-summary"),
    )
) + tuple(
    (method, path, _RELEASE_READ)
    for method, path in (
        ("GET", "/api/v1/scientific/releases"),
        ("GET", "/api/v1/scientific/releases/{release_handle}"),
        (
            "GET",
            "/api/v1/scientific/releases/{release_handle}/artifacts/"
            "{artifact_path}",
        ),
        ("GET", "/api/v1/scientific/releases/{release_handle}/manifest"),
        ("GET", "/api/v1/scientific/releases/{release_handle}/selections"),
    )
) + (
    (
        "GET",
        "/api/v1/scientific/species-entries/{species_entry_id}/statmech",
        "Species-entry statmech subresource; the equivalent query is "
        "search_statmech(species_entry_ref=...), which also carries "
        "evidence and provenance. Reachable via get_json().",
    ),
    (
        "GET",
        "/api/v1/scientific/species-entries/{species_entry_id}/transport",
        "Species-entry transport subresource; the equivalent query is "
        "search_transport(species_entry_ref=...), which also carries "
        "evidence and provenance. Reachable via get_json().",
    ),
    (
        "GET",
        "/api/v1/scientific/network-kinetics/{network_kinetics_ref_or_id}",
        "Single-record read of a row already returned in full by "
        "search_network_kinetics(network_kinetics_ref=...). Reachable via "
        "get_json() when only the ref is known.",
    ),
    (
        "GET",
        "/api/v1/scientific/networks/{network_ref_or_id}",
        "Single-record read of a row already returned in full by "
        "search_networks(network_ref=...). Reachable via get_json() when "
        "only the ref is known.",
    ),
    (
        "GET",
        "/api/v1/scientific/statmech/{statmech_ref_or_id}",
        "Single-record read of a row already returned in full by "
        "search_statmech(statmech_ref=...). Reachable via get_json() when "
        "only the ref is known.",
    ),
    (
        "GET",
        "/api/v1/scientific/transport/{transport_ref_or_id}",
        "Single-record read of a row already returned in full by "
        "search_transport(transport_ref=...). Reachable via get_json() when "
        "only the ref is known.",
    ),
    (
        "GET",
        "/api/v1/scientific/artifacts/integrity",
        "Custody of the deployment's own stored objects: expected against "
        "observed digest and size, and the object store's ETag and paths at "
        "the moment of detection. Curator/admin only, and operational detail "
        "about a bucket rather than science about a record, so a typed method "
        "on a contributor client would be aimed at the wrong reader. "
        "Reachable via get_json().",
    ),
    (
        "GET",
        "/api/v1/scientific/artifacts/{sha256}/integrity",
        "Full observation history for one stored object, oldest first, for "
        "an operator deciding whether to trust a bucket. Same audience and "
        "same reasoning as the integrity listing above. Reachable via "
        "get_json().",
    ),
)

# ---------------------------------------------------------------------------
# not_applicable: (method, path, reason)
# ---------------------------------------------------------------------------

_NOT_APPLICABLE: tuple[tuple[str, str, str], ...] = tuple(
    (method, path, _ADMIN_ONLY)
    for method, path in (
        ("GET", "/api/v1/admin/machine-review/curator-tasks"),
        (
            "POST",
            "/api/v1/admin/machine-review/curator-tasks/build-for-submission/"
            "{submission_id}",
        ),
        ("GET", "/api/v1/admin/machine-review/curator-tasks/{task_id}"),
        ("POST", "/api/v1/admin/machine-review/curator-tasks/{task_id}/assign"),
        ("POST", "/api/v1/admin/machine-review/curator-tasks/{task_id}/reopen"),
        ("POST", "/api/v1/admin/machine-review/curator-tasks/{task_id}/resolve"),
        (
            "POST",
            "/api/v1/admin/machine-review/curator-tasks/{task_id}/start-review",
        ),
        (
            "POST",
            "/api/v1/admin/machine-review/records/{record_type}/{record_id}/"
            "run-fake",
        ),
        (
            "GET",
            "/api/v1/admin/submissions/{submission_id}/machine-review-inspection",
        ),
        ("PATCH", "/api/v1/admin/users/{user_id}/role"),
    )
) + tuple(
    (method, path, _AUTH_SESSION)
    for method, path in (
        ("GET", "/api/v1/auth/api-keys"),
        ("POST", "/api/v1/auth/api-keys"),
        ("DELETE", "/api/v1/auth/api-keys/{key_id}"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/register"),
    )
) + tuple(
    (method, path, _CURATION_WORKFLOW)
    for method, path in (
        (
            "POST",
            "/api/v1/curation/reproducibility-assessments/{record_type}/"
            "{record_id}/evaluate",
        ),
        (
            "GET",
            "/api/v1/curation/reproducibility-assessments/{record_type}/"
            "{record_id}/latest",
        ),
        ("POST", "/api/v1/curation/scientific-record-supersessions"),
        ("GET", "/api/v1/record-reviews"),
        ("GET", "/api/v1/record-reviews/{record_type}/{record_id}"),
        ("PATCH", "/api/v1/record-reviews/{record_type}/{record_id}"),
        ("GET", "/api/v1/species-entries/{species_entry_id}/reviews"),
        ("POST", "/api/v1/species-entries/{species_entry_id}/reviews"),
        ("POST", "/api/v1/conformer-groups/{conformer_group_id}/selections"),
        ("GET", "/api/v1/submissions/for-review"),
        ("POST", "/api/v1/submissions/{submission_id}/approve"),
        ("POST", "/api/v1/submissions/{submission_id}/reject"),
        ("POST", "/api/v1/submissions/{submission_id}/supersede"),
        ("POST", "/api/v1/releases"),
        ("POST", "/api/v1/releases/policies"),
        ("POST", "/api/v1/releases/{release_handle}/doi"),
        ("POST", "/api/v1/releases/{release_handle}/publish"),
        ("POST", "/api/v1/releases/{release_handle}/selections"),
        (
            "POST",
            "/api/v1/releases/{release_handle}/selections/{selection_ref}/"
            "supersede",
        ),
        (
            "POST",
            "/api/v1/releases/{release_handle}/selections/{selection_ref}/"
            "withdraw",
        ),
        ("POST", "/api/v1/releases/{release_handle}/withdraw"),
    )
)


def _build_coverage() -> tuple[OperationCoverage, ...]:
    entries: list[OperationCoverage] = []
    for method, path, client_method, iterator, example, contract_test in _TYPED:
        entries.append(
            OperationCoverage(
                method=method,
                path=path,
                status="typed",
                client_method=client_method,
                iterator=iterator,
                example=example,
                contract_test=contract_test,
            )
        )
    for method, path, reason in _RAW_ONLY:
        entries.append(
            OperationCoverage(
                method=method, path=path, status="raw_only", reason=reason
            )
        )
    for method, path, reason in _NOT_APPLICABLE:
        entries.append(
            OperationCoverage(
                method=method, path=path, status="not_applicable", reason=reason
            )
        )
    seen: dict[tuple[str, str], OperationCoverage] = {}
    for entry in entries:
        if entry.key in seen:
            raise ValueError(f"Duplicate coverage entry for {entry.operation}.")
        seen[entry.key] = entry
    return tuple(sorted(entries, key=lambda e: (e.path, e.method)))


COVERAGE: tuple[OperationCoverage, ...] = _build_coverage()

COVERAGE_BY_OPERATION: Mapping[tuple[str, str], OperationCoverage] = {
    entry.key: entry for entry in COVERAGE
}


# ---------------------------------------------------------------------------
# OpenAPI helpers
# ---------------------------------------------------------------------------


def openapi_operations(spec: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Return every ``(METHOD, path)`` operation declared by ``spec``."""

    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("OpenAPI document has no 'paths' object.")
    operations: set[tuple[str, str]] = set()
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method in item:
            if isinstance(method, str) and method.lower() in _HTTP_METHODS:
                operations.add((method.upper(), path))
    return operations


def find_openapi_golden(start: Path | None = None) -> Path | None:
    """Locate ``backend/tests/api/golden/openapi.json`` in this checkout.

    Walks up from this module (or ``start``) looking for the backend's
    golden snapshot. Returns ``None`` when the client is installed
    outside the monorepo — the parity test then has nothing to compare
    against and says so explicitly rather than passing vacuously.
    """

    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidate = parent / "backend" / "tests" / "api" / "golden" / "openapi.json"
        if candidate.is_file():
            return candidate
    return None


def load_openapi(path: Path) -> Mapping[str, Any]:
    """Read and parse an OpenAPI document from disk."""

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class ParityDiff:
    """What the ledger and the live OpenAPI document disagree about."""

    #: In the OpenAPI document, absent from the coverage map.
    untriaged: tuple[tuple[str, str], ...]
    #: In the coverage map, absent from the OpenAPI document.
    stale: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return not self.untriaged and not self.stale

    def describe(self) -> str:
        lines: list[str] = []
        if self.untriaged:
            lines.append(
                "Backend operations with no entry in tckdb_client._parity "
                "(classify each as typed / raw_only / not_applicable):"
            )
            lines.extend(f"  + {method} {path}" for method, path in self.untriaged)
        if self.stale:
            lines.append(
                "Coverage entries for operations the backend no longer exposes "
                "(remove them from tckdb_client._parity):"
            )
            lines.extend(f"  - {method} {path}" for method, path in self.stale)
        return "\n".join(lines)


def diff_against_spec(spec: Mapping[str, Any]) -> ParityDiff:
    """Compare the declared coverage map against an OpenAPI document."""

    declared = set(COVERAGE_BY_OPERATION)
    actual = openapi_operations(spec)
    return ParityDiff(
        untriaged=tuple(sorted(actual - declared, key=lambda k: (k[1], k[0]))),
        stale=tuple(sorted(declared - actual, key=lambda k: (k[1], k[0]))),
    )


def coverage_counts() -> dict[str, int]:
    """Number of operations per classification."""

    counts = {"typed": 0, "raw_only": 0, "not_applicable": 0}
    for entry in COVERAGE:
        counts[entry.status] += 1
    return counts


def typed_client_methods() -> tuple[str, ...]:
    """Every distinct client method named by a ``typed`` entry."""

    return tuple(
        sorted({entry.client_method for entry in COVERAGE if entry.client_method})
    )


def typed_iterators() -> tuple[str, ...]:
    """Every distinct iterator named by a ``typed`` entry."""

    return tuple(sorted({entry.iterator for entry in COVERAGE if entry.iterator}))


def entries_with_status(status: CoverageStatus) -> tuple[OperationCoverage, ...]:
    """Coverage entries carrying ``status``, in document order."""

    return tuple(entry for entry in COVERAGE if entry.status == status)


def iter_coverage() -> Iterable[OperationCoverage]:
    """Iterate the coverage ledger in stable ``(path, method)`` order."""

    return iter(COVERAGE)


__all__ = [
    "COVERAGE",
    "COVERAGE_BY_OPERATION",
    "CoverageStatus",
    "OperationCoverage",
    "ParityDiff",
    "coverage_counts",
    "diff_against_spec",
    "entries_with_status",
    "find_openapi_golden",
    "iter_coverage",
    "load_openapi",
    "openapi_operations",
    "typed_client_methods",
    "typed_iterators",
]
