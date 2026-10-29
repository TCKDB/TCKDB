"""Read schemas for /api/v1/scientific/species-entries/{id}/thermo.

See docs/specs/browse_order.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator
from tckdb_schemas.thermo_declarations import StoredThermoProtocolDeclaration

from app.db.models.common import (
    EnthalpyReferenceKind,
    GroupAdditivityComponentKind,
    PhaseKind,
    RecordReviewStatus,
    ScientificOriginKind,
    ThermoTargetKind,
)
from app.schemas.reads._method_family import DftRungBound, MethodFamilyFilter
from app.schemas.reads.removed_parameters import THERMO as _REMOVED_PARAMETERS
from app.schemas.reads.scientific_assessment import PublicAssessmentSummary
from app.schemas.reads.scientific_common import (
    CalculationEvidenceSummary,
    CursorToken,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    OrderEcho,
    Pagination,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
    ScientificLevelsSummary,
    ScientificResponse,
    SoftwareReleaseSummary,
    SupersessionNotice,
    TemperatureCoverage,
    WatermarkEcho,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_deposit_warnings import RecordWarnings
from app.services.trust.models import TrustFragment


class ThermoModelKindQuery(str, Enum):
    """Thermo model kinds surfaced by the read API.

    ``nasa``     record is a NASA-7 fit (has a ThermoNASA row).
    ``nasa9``    record is a NASA-9 fit (has ThermoNASA9Interval rows).
    ``wilhoit``  record is a Wilhoit fit (has a ThermoWilhoit row).
    ``points``   record has ThermoPoint rows.
    ``scalar``   record has none of those — only h298/s298 scalar columns.

    ``nasa``/``points``/``scalar`` are retained unchanged for backward
    compatibility; ``nasa9`` and ``wilhoit`` were added alongside the
    NASA-9 / Wilhoit representations.
    """

    nasa = "nasa"
    nasa9 = "nasa9"
    wilhoit = "wilhoit"
    points = "points"
    scalar = "scalar"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ThermoReadRequest(BaseModel):
    """Service-layer request for species-entry thermo read.

    A thermo read lists every matching record in display order; it never picks one (``collapse`` and
    ``selection_policy`` are refused, see :mod:`app.schemas.reads.removed_parameters`). The coverage window below
    filters nothing: it sets what each record's ``temperature_coverage`` is computed against.
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_removed_parameters(cls, data):
        return _REMOVED_PARAMETERS.guard_body(data)

    #: The window ``temperature_coverage`` is computed against. Not a filter: records outside it are still returned.
    coverage_temperature_min_k: float | None = None
    coverage_temperature_max_k: float | None = None
    model_kind: ThermoModelKindQuery | None = None
    # Matches when ANY source calculation linked to the thermo record ran at this level of theory. There is no
    # "primary" calculation for a filter, and no role priority: a record is kept when any linked calculation matches.
    level_of_theory_id: int | None = None
    # Phase C: LoT may be supplied by ref instead of (or alongside) id.
    level_of_theory_ref: str | None = None
    # Matches when ANY source calculation linked to the thermo record ran in a software of this name (what ran the
    # electronic structure; ``ThermoProvenance.primary_calculation.software`` is one of them). There is no "primary"
    # calculation for a filter: a record is kept when any of its linked calculations matches. It does NOT filter on
    # ``ThermoProvenance.software_release`` (the thermo's own software, e.g. Arkane): a record with no linked
    # calculation named "Arkane" is not matched by ``?software=Arkane``.
    software: str | None = None

    # Further provenance filters. ONE source calculation has to match every supplied provenance field at once
    # (``level_of_theory_*``, ``software``, and these). ``method_family`` and ``dft_rung_*`` are a classification of the
    # calculation's method, not a measure of accuracy (``app/schemas/reads/_method_family.py``).
    method: str | None = None
    basis: str | None = None
    software_version: str | None = None
    workflow_tool: str | None = None
    workflow_tool_version: str | None = None
    method_family: MethodFamilyFilter = None
    dft_rung_min: DftRungBound = None
    dft_rung_max: DftRungBound = None
    #: computed, experimental or estimated: the record's own ``scientific_origin``.
    scientific_origin: ScientificOriginKind | None = None

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected non-None per v0 sort policy.
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    cursor: CursorToken = None
    limit: int = 50
    #: Whether ``include=assessments`` compares each stored assessment's inputs with today's (opt-in; at most 10 records).
    check_inputs: bool = False


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class ThermoNASABlock(BaseModel):
    """Two-segment NASA polynomial block matching ThermoNASA columns."""

    t_low: float | None = None
    t_mid: float | None = None
    t_high: float | None = None
    low_temperature_coefficients: list[float | None] = Field(default_factory=list)
    high_temperature_coefficients: list[float | None] = Field(default_factory=list)


class ThermoNASA9IntervalBlock(BaseModel):
    """One NASA-9 polynomial interval matching ThermoNASA9Interval columns."""

    interval_index: int
    t_min_k: float
    t_max_k: float
    a1: float
    a2: float
    a3: float
    a4: float
    a5: float
    a6: float
    a7: float
    a8: float
    a9: float


class ThermoWilhoitBlock(BaseModel):
    """Wilhoit heat-capacity form matching ThermoWilhoit columns."""

    cp0_j_mol_k: float
    cp_inf_j_mol_k: float
    b_k: float
    a0: float
    a1: float
    a2: float
    a3: float
    h0_kj_mol: float | None = None
    s0_j_mol_k: float | None = None


class ThermoPointBlock(BaseModel):
    """One temperature-evaluated thermo point row."""

    temperature_k: float
    cp_j_mol_k: float | None = None
    h_kj_mol: float | None = None
    s_j_mol_k: float | None = None
    g_kj_mol: float | None = None


class ThermoProvenance(BaseModel):
    """Thermo provenance block — keys always present, ``null`` when absent.

    Phase B: integer ``*_id`` fields keep their place; ``*_ref`` siblings
    carry the public stable handles.

    Two distinct kinds of "software" can appear on a thermo record, and
    issue #284 was exactly the two getting collapsed into one field:

    - ``software_release`` / ``workflow_tool_release`` — what computed
      *this thermochemistry* (e.g. Arkane, run by ARC). Sourced strictly
      from ``thermo.software_release_id`` / ``thermo.workflow_tool_release_id``
      — the thermo row's own FKs, never a calculation's. When the thermo
      carries no software of its own (a value fitted or transcribed
      without a recorded fitting tool), both are ``null``. This is a
      complete, honest answer for records whose only real evidence is a
      software attribution and no source-calculation chain — it must
      never be backfilled from ``primary_calculation.software``.
    - ``primary_calculation.software`` — what ran the *electronic
      structure* underneath (e.g. Gaussian), nested inside the primary
      source-calculation summary where it has always correctly lived.

    A client that wants "what tool do I cite for this thermochemistry"
    reads ``software_release`` / ``workflow_tool_release``; one that wants
    "what ran the QM" reads ``primary_calculation.software``. Neither
    substitutes for the other.

    ``conformer_observation_id`` / ``conformer_observation_ref`` and
    ``conformer_group_id`` / ``conformer_group_ref`` resolve the same
    single calculation used for ``primary_calculation`` one hop further,
    through ``calculation.conformer_observation_id`` to the conformer
    basin it belongs to. All four are ``null`` when the record has no
    resolvable primary calculation (no source calc, direct or from the
    linked statmech) — a record with no calculation has no conformer to
    report, and this block does not invent one.
    """

    primary_calculation: CalculationEvidenceSummary | None = None
    level_of_theory: LevelOfTheorySummary | None = None
    #: The thermo's OWN software provenance — never the calculation's.
    #: See class docstring.
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    statmech_id: int | None = None
    statmech_ref: str | None = None
    freq_calculation_id: int | None = None
    freq_calculation_ref: str | None = None
    sp_calculation_id: int | None = None
    sp_calculation_ref: str | None = None
    conformer_observation_id: int | None = None
    conformer_observation_ref: str | None = None
    conformer_group_id: int | None = None
    conformer_group_ref: str | None = None


class GroupAdditivityComponentBlock(BaseModel):
    """One Benson-group (or correction) contribution in a GA breakdown."""

    component_kind: GroupAdditivityComponentKind
    group_label: str
    count: int
    h298_contribution_kj_mol: float | None = None
    s298_contribution_j_mol_k: float | None = None
    cp298_contribution_j_mol_k: float | None = None


class GroupAdditivityBlock(BaseModel):
    """Group-additivity estimation provenance for an estimated thermo record.

    Present only when the thermo record has an ``applied_group_additivity``
    row (``scientific_origin=estimated``). Surfaces which GA scheme produced
    the estimate and the per-group contribution breakdown.
    """

    scheme_id: int
    scheme_ref: str
    scheme_name: str
    scheme_version: str | None = None
    code_commit: str | None = None
    note: str | None = None
    components: list[GroupAdditivityComponentBlock] = Field(default_factory=list)


class ThermoTargetBlock(BaseModel):
    """The thermodynamic target a record's depositor declared.

    :param kind: ``equilibrium_ensemble`` or ``single_conformer``.
    :param conformer_group_ref: Public ref of the conformer group a
        ``single_conformer`` target names; ``null`` for an equilibrium target.
    """

    kind: ThermoTargetKind
    conformer_group_ref: str | None = None


class ThermoRecord(BaseModel):
    """One thermo record returned by the thermo endpoint.

    The underlying schema does not store cp units explicitly; per
    ``app/db/models/thermo.py``, ``ThermoPoint.cp_j_mol_k`` is the canonical
    (and only) cp representation in v0. Future schema work may surface a
    cp-units field; v0 omits it from the response.

    Phase B: ``thermo_ref`` is the public stable handle alongside the
    integer ``thermo_id``.
    """

    thermo_id: int
    thermo_ref: str
    #: Deposition time: when this row was created in TCKDB. The second key of the display order
    #: (``docs/specs/browse_order.md``), shown so a client can check the order it was served.
    created_at: datetime
    scientific_origin: ScientificOriginKind
    model_kind: ThermoModelKindQuery
    review: RecordReviewBadge
    #: Correction notice, present only when this record has been replaced.
    #: ``null`` on a current record — and always computed, never behind an
    #: ``include=`` token. See :class:`SupersessionNotice`.
    supersession: SupersessionNotice | None = None
    phase: PhaseKind | None = None
    reference_pressure_bar: float | None = None
    #: Declared reference for every enthalpy this record carries; ``null``
    #: means the deposit did not state one. See
    #: ``docs/guides/depositing_a_thermo_record.md``.
    enthalpy_reference_kind: EnthalpyReferenceKind | None = None
    #: What the depositor declared the values describe; ``null`` means no
    #: target was declared (every record deposited before declarations
    #: existed). Never inferred from a statmech link.
    thermodynamic_target: ThermoTargetBlock | None = None
    #: The depositor's versioned protocol declaration (recipe, formation
    #: reference, thermal approximation, departures, supporting calculations by
    #: public ref); ``null`` means none was declared. An attributed claim, not
    #: a verified fact. See ``docs/guides/depositing_a_thermo_record.md``.
    protocol: StoredThermoProtocolDeclaration | None = None
    #: ``true`` when a protocol is stored but no longer validates against the declaration
    #: schema (a value written outside the upload path). ``protocol`` is then ``null`` and
    #: the rest of the record is served; ``null`` alone still means "not stated".
    protocol_unreadable: bool = False
    enthalpy_formation_0k_kj_mol: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = None
    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    h298_uncertainty_kj_mol: float | None = None
    s298_uncertainty_j_mol_k: float | None = None
    nasa: ThermoNASABlock | None = None
    nasa9: list[ThermoNASA9IntervalBlock] | None = None
    wilhoit: ThermoWilhoitBlock | None = None
    points: list[ThermoPointBlock] | None = None
    temperature_coverage: TemperatureCoverage | None = None
    evidence_completeness: EvidenceCompletenessBreakdown
    provenance: ThermoProvenance
    #: Geometry / frequency / energy levels of theory, derived at read
    #: time from this record's own source calculations, falling back to
    #: the statmech it is linked to (``statmech_id``) for any role this
    #: record does not cover (R6: a thermo derived from a statmech record
    #: inherits its levels). Never borrowed from an unlinked statmech on the
    #: same entry: an unlinked thermo with no source calculations has none
    #: (#636). See :class:`ScientificLevelsSummary`.
    levels: ScientificLevelsSummary = Field(default_factory=ScientificLevelsSummary)
    # Group-additivity estimation breakdown; null unless the record is an
    # estimated thermo with an attached GA breakdown (DR-0035).
    group_additivity: GroupAdditivityBlock | None = None
    trust: TrustFragment | None = None
    assessments: PublicAssessmentSummary | None = None


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: OrderEcho
    include: list[str]
    #: Whether this read compared each stored assessment's inputs with today's (``check_inputs=true``).
    check_inputs: bool = False


class ScientificSpeciesThermoResponse(ScientificResponse):
    """Response envelope for /api/v1/scientific/species-entries/{id}/thermo.

    Phase B: ``species_entry_ref`` mirrors ``species_entry_id`` as the
    public stable handle for the response's path-parameter resource.
    """

    #: The deposit-time warnings of each record this response names, with its public ref (see
    #: ``scientific_deposit_warnings``). Present by default; list and search responses do not carry it.
    record_warnings: list[RecordWarnings] = Field(default_factory=list)
    request: RequestEcho
    species_entry_id: int
    species_entry_ref: str
    review_summary: ReviewStatusSummary
    records: list[ThermoRecord]
    pagination: Pagination
    #: Where the next page starts: pass it back as ``cursor``. ``null`` on the last page. See ``Pagination``.
    next_cursor: str | None = None
    #: The snapshot a cursor traversal is pinned to (set on every page that can continue a traversal).
    watermark: WatermarkEcho | None = None
