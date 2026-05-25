"""Generic current scientific read/search MCP tools.

These tools expose stable public read/query endpoints added after the
original MCP MVP. They are intentionally small wrappers: validate the
agent-facing public-ref/include policy, make one HTTP request, and
return the backend response unchanged except for artifact body/download
field stripping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

Method = Literal["GET", "POST"]

REVIEW_FIELDS = frozenset({"min_review_status", "include_rejected", "include_deprecated"})
PAGING_FIELDS = frozenset({"offset", "limit"})
COMMON_SEARCH_FIELDS = REVIEW_FIELDS | PAGING_FIELDS | frozenset({"include", "sort"})


@dataclass(frozen=True)
class SearchTool:
    name: str
    description: str
    method: Method
    path: str
    fields: frozenset[str]
    include_tokens: frozenset[str]
    rejected_integer_fields: frozenset[str]
    required_one_of: tuple[str, ...] = ()
    defaults: dict[str, Any] | None = None
    artifact_safe: bool = False


@dataclass(frozen=True)
class DetailTool:
    name: str
    description: str
    path_template: str
    ref_field: str
    ref_prefix: str
    include_tokens: frozenset[str]
    rejected_integer_fields: frozenset[str]
    defaults: dict[str, Any] | None = None


CALCULATION_INCLUDES = frozenset(
    {
        "artifacts",
        "dependencies",
        "geometry",
        "parameters",
        "provenance",
        "results",
        "review",
        "scf_stability",
        "validation",
        "all",
    }
)
STRUCTURE_INCLUDES = frozenset({"review", "all"})
ARTIFACT_INCLUDES = frozenset({"calculation", "owner", "review", "all"})
TRANSITION_STATE_INCLUDES = frozenset({"entries", "calculations", "geometries", "review", "all"})
CONFORMER_INCLUDES = frozenset({"observations", "selections", "calculations", "geometries", "review", "all"})
STATMECH_INCLUDES = frozenset({"conformers", "frequencies", "source_calculations", "torsions", "review", "all"})
TRANSPORT_INCLUDES = frozenset({"source_calculations", "review", "all"})
NETWORK_INCLUDES = frozenset(
    {"species", "reactions", "states", "channels", "solves", "kinetics", "source_calculations", "review", "all"}
)
NETWORK_SOLVE_INCLUDES = frozenset({"bath_gas", "energy_transfer", "kinetics", "source_calculations", "review", "all"})
NETWORK_KINETICS_INCLUDES = frozenset({"coefficients", "plog", "points", "source_calculations", "review", "all"})
LITERATURE_RECORD_INCLUDES = frozenset({"review", "all"})
FSF_INCLUDES = frozenset({"literature", "used_by", "all"})
ECS_INCLUDES = frozenset({"literature", "corrections", "used_by", "all"})

SEARCH_TOOLS: dict[str, SearchTool] = {}
DETAIL_TOOLS: dict[str, DetailTool] = {}


def _register_search(tool: SearchTool) -> None:
    SEARCH_TOOLS[tool.name] = tool


def _register_detail(tool: DetailTool) -> None:
    DETAIL_TOOLS[tool.name] = tool


_register_search(
    SearchTool(
        name="tckdb_species_structure_search",
        description="Search species by RDKit-backed structure query. Metadata-only, public-ref-first.",
        method="POST",
        path="/scientific/species/structure-search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset({"query_smiles", "query_smarts", "query_inchi", "query_inchi_key", "mode", "similarity_threshold"}),
        include_tokens=STRUCTURE_INCLUDES,
        rejected_integer_fields=frozenset({"species_id", "species_entry_id"}),
        required_one_of=("query_smiles", "query_smarts", "query_inchi", "query_inchi_key"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_calculation_search",
        description="Search calculation summaries by owner refs, method/basis/software, result/artifact flags, and review state.",
        method="POST",
        path="/scientific/calculations/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "species_entry_ref",
                "transition_state_entry_ref",
                "species_ref",
                "transition_state_ref",
                "owner_kind",
                "calculation_type",
                "quality",
                "has_result",
                "has_artifacts",
                "has_input_geometry",
                "has_output_geometry",
                "artifact_kind",
                "created_before",
                "created_after",
                "method",
                "basis",
                "lot_ref",
                "lot_hash",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
                "geometry_validation_status",
                "scf_stability_status",
                "dependency_role",
                "parent_calculation_ref",
                "child_calculation_ref",
                "parameter_key",
                "parameter_value",
                "canonical_parameter_key",
                "canonical_parameter_value",
                "include_rejected_quality",
            }
        ),
        include_tokens=CALCULATION_INCLUDES,
        rejected_integer_fields=frozenset(
            {"calculation_id", "species_id", "species_entry_id", "transition_state_id", "transition_state_entry_id", "lot_id"}
        ),
        required_one_of=("species_entry_ref", "transition_state_entry_ref", "species_ref", "transition_state_ref", "calculation_type", "method", "basis", "software", "lot_ref", "has_artifacts"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_transition_state_search",
        description="Search transition-state summaries by reaction/TS refs, calculation flags, method/basis/software, and review state.",
        method="POST",
        path="/scientific/transition-states/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "reaction_ref",
                "reaction_entry_ref",
                "transition_state_ref",
                "transition_state_entry_ref",
                "status",
                "charge",
                "multiplicity",
                "has_calculations",
                "has_opt",
                "has_freq",
                "has_sp",
                "has_irc",
                "has_path_search",
                "has_geometry_validation",
                "has_scf_stability",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=TRANSITION_STATE_INCLUDES,
        rejected_integer_fields=frozenset({"reaction_id", "reaction_entry_id", "transition_state_id", "transition_state_entry_id"}),
        required_one_of=("reaction_ref", "reaction_entry_ref", "transition_state_ref", "transition_state_entry_ref", "status", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_conformer_search",
        description="Search conformer groups/observations by species refs, selection/origin, calculation flags, method, and review state.",
        method="POST",
        path="/scientific/conformers/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "species_ref",
                "species_entry_ref",
                "conformer_group_ref",
                "conformer_observation_ref",
                "selection_kind",
                "has_selection",
                "assignment_scheme_ref",
                "has_observations",
                "has_calculations",
                "has_geometries",
                "has_opt",
                "has_freq",
                "has_sp",
                "has_geometry_validation",
                "has_scf_stability",
                "scientific_origin",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=CONFORMER_INCLUDES,
        rejected_integer_fields=frozenset({"species_id", "species_entry_id", "conformer_group_id", "conformer_observation_id", "assignment_scheme_id"}),
        required_one_of=("species_ref", "species_entry_ref", "conformer_group_ref", "conformer_observation_ref", "selection_kind", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_statmech_search",
        description="Search statmech records by species/statmech/conformer refs, model/source flags, method, and review state.",
        method="POST",
        path="/scientific/statmech/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "species_ref",
                "species_entry_ref",
                "statmech_ref",
                "conformer_group_ref",
                "conformer_observation_ref",
                "model_kind",
                "has_source_calculations",
                "has_freq_calculation",
                "has_rotor_scans",
                "has_torsions",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=STATMECH_INCLUDES,
        rejected_integer_fields=frozenset({"species_id", "species_entry_id", "statmech_id", "conformer_group_id", "conformer_observation_id"}),
        required_one_of=("species_ref", "species_entry_ref", "statmech_ref", "conformer_group_ref", "conformer_observation_ref", "model_kind", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_transport_search",
        description="Search transport records by species/transport refs, model/source flags, method, and review state.",
        method="POST",
        path="/scientific/transport/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "species_ref",
                "species_entry_ref",
                "transport_ref",
                "model_kind",
                "has_source_calculations",
                "has_lj_parameters",
                "has_dipole_moment",
                "has_polarizability",
                "has_rotational_relaxation",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=TRANSPORT_INCLUDES,
        rejected_integer_fields=frozenset({"species_id", "species_entry_id", "transport_id"}),
        required_one_of=("species_ref", "species_entry_ref", "transport_ref", "model_kind", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_network_search",
        description="Search network/PDep records by network/species/reaction refs, content flags, conditions, method, and review state.",
        method="POST",
        path="/scientific/networks/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "network_ref",
                "species_ref",
                "species_entry_ref",
                "reaction_ref",
                "reaction_entry_ref",
                "has_species",
                "has_reactions",
                "has_states",
                "has_channels",
                "has_solves",
                "has_kinetics",
                "has_chebyshev",
                "has_plog",
                "has_point_kinetics",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
                "temperature_min",
                "temperature_max",
                "pressure_min",
                "pressure_max",
            }
        ),
        include_tokens=NETWORK_INCLUDES,
        rejected_integer_fields=frozenset({"network_id", "species_id", "species_entry_id", "reaction_id", "reaction_entry_id"}),
        required_one_of=("network_ref", "species_ref", "species_entry_ref", "reaction_ref", "reaction_entry_ref", "has_kinetics", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_network_solve_search",
        description="Search network solve records by solve/network refs, conditions, kinetics flags, method, and review state.",
        method="POST",
        path="/scientific/network-solves/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "network_solve_ref",
                "network_ref",
                "solve_method",
                "temperature_min",
                "temperature_max",
                "pressure_min",
                "pressure_max",
                "has_bath_gas",
                "has_energy_transfer",
                "has_source_calculations",
                "has_kinetics",
                "has_chebyshev",
                "has_plog",
                "has_point_kinetics",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=NETWORK_SOLVE_INCLUDES,
        rejected_integer_fields=frozenset({"network_solve_id", "network_id"}),
        required_one_of=("network_solve_ref", "network_ref", "solve_method", "has_kinetics", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_network_kinetics_search",
        description="Search network kinetics by nkin/network/solve refs, model/condition flags, method, and review state.",
        method="POST",
        path="/scientific/network-kinetics/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "network_kinetics_ref",
                "network_ref",
                "network_solve_ref",
                "model_kind",
                "temperature_min",
                "temperature_max",
                "pressure_min",
                "pressure_max",
                "has_chebyshev",
                "has_plog",
                "has_points",
                "has_source_calculations",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
            }
        ),
        include_tokens=NETWORK_KINETICS_INCLUDES,
        rejected_integer_fields=frozenset({"network_kinetics_id", "network_id", "network_solve_id"}),
        required_one_of=("network_kinetics_ref", "network_ref", "network_solve_ref", "model_kind", "has_chebyshev", "has_plog", "has_points", "method", "basis", "software"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_artifact_search",
        description="Search artifact metadata only. Does not request or expose artifact bodies or download URLs.",
        method="POST",
        path="/scientific/artifacts/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "artifact_kind",
                "filename",
                "filename_contains",
                "sha256",
                "has_sha256",
                "has_bytes",
                "bytes_min",
                "bytes_max",
                "calculation_ref",
                "calculation_type",
                "quality",
                "method",
                "basis",
                "software",
                "software_version",
                "workflow_tool",
                "workflow_tool_version",
                "species_entry_ref",
                "transition_state_entry_ref",
                "conformer_observation_ref",
                "created_after",
                "created_before",
            }
        ),
        include_tokens=ARTIFACT_INCLUDES,
        rejected_integer_fields=frozenset({"artifact_id", "calculation_id", "species_entry_id", "transition_state_entry_id", "conformer_observation_id"}),
        required_one_of=("artifact_kind", "filename", "filename_contains", "sha256", "calculation_ref", "species_entry_ref", "transition_state_entry_ref", "conformer_observation_ref"),
        artifact_safe=True,
    )
)
_register_search(
    SearchTool(
        name="tckdb_frequency_scale_factor_search",
        description="Search frequency-scale-factor reference records by ref, value, method/basis/software, literature, and usage.",
        method="POST",
        path="/scientific/frequency-scale-factors/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "frequency_scale_factor_ref",
                "value",
                "value_min",
                "value_max",
                "scale_kind",
                "model_kind",
                "method",
                "basis",
                "software",
                "software_version",
                "literature_ref",
                "used_by_statmech",
            }
        ),
        include_tokens=FSF_INCLUDES,
        rejected_integer_fields=frozenset({"frequency_scale_factor_id", "literature_id"}),
        required_one_of=("frequency_scale_factor_ref", "value", "scale_kind", "model_kind", "method", "basis", "software", "literature_ref", "used_by_statmech"),
    )
)
_register_search(
    SearchTool(
        name="tckdb_energy_correction_scheme_search",
        description="Search energy-correction-scheme reference records by ref/name/version, method/basis/software, literature, and usage.",
        method="POST",
        path="/scientific/energy-correction-schemes/search",
        fields=COMMON_SEARCH_FIELDS
        | frozenset(
            {
                "energy_correction_scheme_ref",
                "name",
                "version",
                "scheme_kind",
                "method",
                "basis",
                "software",
                "software_version",
                "literature_ref",
                "has_corrections",
                "used_by_thermo",
                "used_by_calculation",
            }
        ),
        include_tokens=ECS_INCLUDES,
        rejected_integer_fields=frozenset({"energy_correction_scheme_id", "literature_id"}),
        required_one_of=("energy_correction_scheme_ref", "name", "scheme_kind", "method", "basis", "software", "literature_ref", "has_corrections", "used_by_thermo", "used_by_calculation"),
    )
)

_register_detail(
    DetailTool(
        name="tckdb_calculation_detail",
        description="Fetch a calculation detail by public calculation_ref (calc_*).",
        path_template="/scientific/calculations/{ref}",
        ref_field="calculation_ref",
        ref_prefix="calc_",
        include_tokens=CALCULATION_INCLUDES,
        rejected_integer_fields=frozenset({"calculation_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_transition_state_detail",
        description="Fetch a transition-state concept detail by public transition_state_ref (ts_*).",
        path_template="/scientific/transition-states/{ref}",
        ref_field="transition_state_ref",
        ref_prefix="ts_",
        include_tokens=TRANSITION_STATE_INCLUDES,
        rejected_integer_fields=frozenset({"transition_state_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_transition_state_entry_detail",
        description="Fetch a transition-state entry detail by public transition_state_entry_ref (tse_*).",
        path_template="/scientific/transition-state-entries/{ref}",
        ref_field="transition_state_entry_ref",
        ref_prefix="tse_",
        include_tokens=TRANSITION_STATE_INCLUDES,
        rejected_integer_fields=frozenset({"transition_state_entry_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_conformer_group_detail",
        description="Fetch a conformer group detail by public conformer_group_ref (cg_*).",
        path_template="/scientific/conformer-groups/{ref}",
        ref_field="conformer_group_ref",
        ref_prefix="cg_",
        include_tokens=CONFORMER_INCLUDES,
        rejected_integer_fields=frozenset({"conformer_group_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_conformer_observation_detail",
        description="Fetch a conformer observation detail by public conformer_observation_ref (co_*).",
        path_template="/scientific/conformer-observations/{ref}",
        ref_field="conformer_observation_ref",
        ref_prefix="co_",
        include_tokens=CONFORMER_INCLUDES,
        rejected_integer_fields=frozenset({"conformer_observation_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_statmech_detail",
        description="Fetch a statmech detail by public statmech_ref (sm_*).",
        path_template="/scientific/statmech/{ref}",
        ref_field="statmech_ref",
        ref_prefix="sm_",
        include_tokens=STATMECH_INCLUDES,
        rejected_integer_fields=frozenset({"statmech_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_transport_detail",
        description="Fetch a transport detail by public transport_ref (trn_*).",
        path_template="/scientific/transport/{ref}",
        ref_field="transport_ref",
        ref_prefix="trn_",
        include_tokens=TRANSPORT_INCLUDES,
        rejected_integer_fields=frozenset({"transport_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_network_detail",
        description="Fetch a network/PDep detail by public network_ref (net_*).",
        path_template="/scientific/networks/{ref}",
        ref_field="network_ref",
        ref_prefix="net_",
        include_tokens=NETWORK_INCLUDES,
        rejected_integer_fields=frozenset({"network_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_network_solve_detail",
        description="Fetch a network solve detail by public network_solve_ref (nsolve_*).",
        path_template="/scientific/network-solves/{ref}",
        ref_field="network_solve_ref",
        ref_prefix="nsolve_",
        include_tokens=NETWORK_SOLVE_INCLUDES,
        rejected_integer_fields=frozenset({"network_solve_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_network_kinetics_detail",
        description="Fetch network kinetics by public network_kinetics_ref (nkin_*). Points require explicit include=['points'].",
        path_template="/scientific/network-kinetics/{ref}",
        ref_field="network_kinetics_ref",
        ref_prefix="nkin_",
        include_tokens=NETWORK_KINETICS_INCLUDES,
        rejected_integer_fields=frozenset({"network_kinetics_id"}),
    )
)
_register_detail(
    DetailTool(
        name="tckdb_literature_records",
        description="Fetch inverse records attached to a public literature_ref (lit_*).",
        path_template="/scientific/literature/{ref}/records",
        ref_field="literature_ref",
        ref_prefix="lit_",
        include_tokens=LITERATURE_RECORD_INCLUDES,
        rejected_integer_fields=frozenset({"literature_id"}),
        defaults={"offset": 0},
    )
)


def list_tool_payloads() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": _search_schema(t)}
        for t in SEARCH_TOOLS.values()
    ] + [
        {"name": t.name, "description": t.description, "inputSchema": _detail_schema(t)}
        for t in DETAIL_TOOLS.values()
    ]


def run_search(client: TCKDBHttpClient, config: Config, tool: SearchTool, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = dict(arguments or {})
    _reject_integer_fields(args, tool.rejected_integer_fields)
    _reject_unknown(args, tool.fields)
    _require_one(args, tool.required_one_of)
    include = _validate_include(args.get("include", []), tool.include_tokens)
    offset = _validate_offset(args.get("offset", 0))
    limit = _validate_limit(config, args.get("limit"))
    body = {k: v for k, v in args.items() if k in tool.fields and k not in PAGING_FIELDS and k != "include"}
    body["offset"] = offset
    body["limit"] = limit
    body["include"] = include
    body = {k: v for k, v in body.items() if v is not None}
    url = client.scientific_url(tool.path)
    if tool.method == "POST":
        response = client.post_json(url, body)
    else:
        response = client.get(url, params=body)
    if tool.artifact_safe:
        response = _strip_artifact_payload_fields(response)
    return response


def run_detail(client: TCKDBHttpClient, config: Config, tool: DetailTool, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = dict(arguments or {})
    accepted = frozenset({tool.ref_field, "include"})
    if tool.name == "tckdb_literature_records":
        accepted = accepted | frozenset({"offset", "limit", "record_type", "include_rejected", "include_deprecated", "sort"})
    _reject_integer_fields(args, tool.rejected_integer_fields)
    _reject_unknown(args, accepted)
    ref = validate_path_handle(args.get(tool.ref_field), field_name=tool.ref_field, expected_prefix=tool.ref_prefix)
    include = _validate_include(args.get("include", []), tool.include_tokens)
    quoted = quote(ref, safe="")
    params: dict[str, Any] = {"include": include}
    if tool.name == "tckdb_literature_records":
        params.update(
            {
                "record_type": args.get("record_type"),
                "include_rejected": args.get("include_rejected"),
                "include_deprecated": args.get("include_deprecated"),
                "sort": args.get("sort"),
                "offset": _validate_offset(args.get("offset", 0)),
                "limit": _validate_limit(config, args.get("limit")),
            }
        )
    url = client.scientific_url(tool.path_template.format(ref=quoted))
    return client.get(url, params=params)


def _search_schema(tool: SearchTool) -> dict[str, Any]:
    props = {field: _schema_for_field(field) for field in sorted(tool.fields) if field != "include"}
    props["include"] = {
        "type": "array",
        "items": {"type": "string", "enum": sorted(tool.include_tokens)},
        "description": "Safe include tokens. 'internal_ids' is deliberately not exposed.",
    }
    return {"type": "object", "properties": props, "additionalProperties": False}


def _detail_schema(tool: DetailTool) -> dict[str, Any]:
    props: dict[str, Any] = {
        tool.ref_field: {
            "type": "string",
            "pattern": f"^{tool.ref_prefix}[A-Za-z0-9_-]+$",
            "minLength": len(tool.ref_prefix) + 1,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "include": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(tool.include_tokens)},
            "description": "Safe include tokens. 'internal_ids' is deliberately not exposed.",
        },
    }
    if tool.name == "tckdb_literature_records":
        props.update(
            {
                "record_type": {"type": "string"},
                "include_rejected": {"type": "boolean"},
                "include_deprecated": {"type": "boolean"},
                "sort": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            }
        )
    return {"type": "object", "required": [tool.ref_field], "properties": props, "additionalProperties": False}


def _schema_for_field(field: str) -> dict[str, Any]:
    if field in PAGING_FIELDS:
        return {"type": "integer", "minimum": 0 if field == "offset" else 1}
    if field.startswith("has_") or field.startswith("include_") or field.startswith("used_by_"):
        return {"type": "boolean"}
    if field.endswith("_min") or field.endswith("_max") or field in {"pressure", "value", "similarity_threshold"}:
        return {"type": "number"}
    if field in {"reactants", "products"}:
        return {"type": "array", "items": {"type": "string", "minLength": 1}}
    return {"type": "string"}


def _reject_integer_fields(args: dict[str, Any], fields: frozenset[str]) -> None:
    rejected = sorted(fields & args.keys())
    if rejected:
        raise invalid_input(f"integer-id fields are not accepted by the MCP: {rejected!r}. Use public *_ref handles.")


def _reject_unknown(args: dict[str, Any], accepted: frozenset[str]) -> None:
    unknown = sorted(args.keys() - accepted)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")


def _require_one(args: dict[str, Any], fields: tuple[str, ...]) -> None:
    if fields and not any(_present(args.get(f)) for f in fields):
        raise invalid_input(f"at least one search discriminator must be supplied: {list(fields)!r}")


def _validate_include(value: Any, legal: frozenset[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise invalid_input(f"include must be a list of strings; got {value!r}")
    if "internal_ids" in value:
        raise invalid_input("include=internal_ids is not exposed by the MCP; the agent-facing surface never returns integer DB ids.")
    illegal = [t for t in value if t not in legal]
    if illegal:
        raise invalid_input(f"unknown include token(s): {illegal!r}. Legal tokens: {sorted(legal)!r}")
    return list(value)


def _validate_offset(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise invalid_input(f"offset must be a non-negative integer; got {value!r}")
    return value


def _validate_limit(config: Config, value: Any) -> int:
    try:
        return config.cap_limit(value)
    except ValueError as exc:
        raise invalid_input(str(exc)) from exc


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return True


_ARTIFACT_DANGEROUS_KEYS = frozenset(
    {
        "body",
        "body_bytes",
        "content",
        "raw_content",
        "download_url",
        "download_urls",
        "presigned_url",
        "presigned_urls",
        "signed_url",
        "signed_urls",
    }
)


def _strip_artifact_payload_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_artifact_payload_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_artifact_payload_fields(item)
            for key, item in value.items()
            if key not in _ARTIFACT_DANGEROUS_KEYS
        }
    return value


__all__ = [
    "DETAIL_TOOLS",
    "SEARCH_TOOLS",
    "list_tool_payloads",
    "run_detail",
    "run_search",
]
