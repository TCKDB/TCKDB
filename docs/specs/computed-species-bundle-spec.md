# Computed-species bundle upload endpoint — implementation spec

## Goal

Implement `POST /api/v1/uploads/computed-species` per [DR-0029](../decisions/0029-computed-species-bundle-upload-endpoint.md). One contributor/workflow-facing bundle endpoint that accepts a complete computed-species result (identity + conformers + per-conformer calculations and artifacts + optional thermo) using **local string keys** for in-bundle cross-references, and persists the whole thing in a single SQL transaction with full-batch artifact compensation on failure.

> **Invariants for the implementer.**
> 1. The bundle is self-contained — no `existing_calculation_id` anywhere in the bundle schema.
> 2. Local keys are global within a bundle; every cross-reference is a string.
> 3. One SQL transaction per bundle. Artifact compensation reuses `persist_artifact_batch` from DR-0027.
> 4. Conformer-group dedup via basin matching reuses an existing group; conformer_observation always appends (Requirement 6 of DR-0029).
> 5. The new endpoint does NOT replace primitive endpoints. `/uploads/conformers`, `/uploads/thermo`, `/calculations/{id}/artifacts` keep working unchanged.

## Required reading

- [DR-0029](../decisions/0029-computed-species-bundle-upload-endpoint.md) — the architectural decision this spec implements.
- [DR-0028](../decisions/0028-thermo-upload-reference-existing-calculations.md) — role/type compatibility rules and error semantics this spec inherits.
- [DR-0027](../decisions/0027-offline-payload-bundle-format-and-replay-engine-location.md) — artifact compensation pattern reused here; `payload_kind = "computed_species"` for offline replay.
- [DR-0026](../decisions/0026-calculation-origin-and-reuse-provenance.md) — `parameters_json.tckdb_origin` flows through unchanged.
- [DR-0024](../decisions/0024-upload-idempotency-keys.md) — server-side cache contract.

## Out of scope (deferred per DR-0029)

- statmech, transport, kinetics blocks in the bundle.
- `dry_run=true` query parameter.
- AEC/BAC applied corrections — the FSF row included via the existing `applied_energy_corrections` shape per DR-0028; AEC/BAC defer to milestone 7.5 when ARC emits applied totals.
- `Thermo.parameters_json` (no schema column today; tckdb_origin for thermo deferred).
- archive/tarball bundle upload (directories only).
- concurrency within a single bundle (workflow runs sequentially).
- Alembic migration of any kind.

---

## Files to add / change

| status | path | what |
|---|---|---|
| **NEW** | `backend/app/schemas/workflows/computed_species_upload.py` | All bundle Pydantic schemas (request + sub-shapes + response). |
| **NEW** | `backend/app/workflows/computed_species.py` | Bundle workflow orchestrator: `persist_computed_species_upload(session, request, *, created_by) -> ComputedSpeciesUploadOutcome`. |
| change | `backend/app/api/routes/uploads.py` | Add `POST /uploads/computed-species` route handler + `ComputedSpeciesUploadResult` response type. ~25 lines, mirrors existing `/uploads/conformers` shape. |
| change | `backend/app/api/router.py` | Confirm the new route is mounted (it should be already if `uploads_router` is wired). |
| **NEW** | `backend/tests/api/test_api_upload_computed_species.py` | Endpoint-level tests covering the 14+ cases from DR-0029's test plan. |
| **NEW** | `backend/tests/workflows/test_computed_species_upload.py` | Workflow-level tests for direct `persist_computed_species_upload` calls. |
| no change | `backend/app/services/calculation_resolution.py` | Reuse `resolve_and_persist_calculation_with_results` and the existing `_DEPENDENCY_ROLE_FOR_TYPE` auto-creation. |
| no change | `backend/app/services/conformer_resolution.py` | Reuse `resolve_conformer_group` (the basin-match-or-create function `/uploads/conformers` already calls). |
| no change | `backend/app/services/artifact_persistence.py` | Reuse `persist_artifact_batch` per calculation. |
| no change | `backend/app/services/thermo_resolution.py` | Reuse `resolve_thermo_upload` + `persist_thermo` after building a synthetic `ThermoUploadRequest` from the bundle's thermo block (or extend with a bundle-shaped variant — see *Workflow* below). |
| no change | `backend/app/db/models/*.py` | No DB column changes. |
| no change | `backend/alembic/versions/*` | No new revision. |

---

## Request schemas

All in `backend/app/schemas/workflows/computed_species_upload.py`. Imports omitted for brevity; follow the existing `conformer_upload.py` pattern.

```python
class CalculationDependencyInBundle(SchemaBase):
    """A calculation_dependency edge declared by local keys.

    Auto-creation for additional_calculations → primary opt continues to
    fire (per app/services/calculation_resolution.py:_DEPENDENCY_ROLE_FOR_TYPE).
    This explicit list is for non-auto edges (e.g., an opt restart that
    optimized_from another opt in the same bundle).
    """

    parent_calculation_key: str = Field(min_length=1)
    role: CalculationDependencyRole


class CalculationInBundle(SchemaBase):
    """One calculation within a conformer's calc list.

    Carries everything the primitive `CalculationWithResultsPayload` carries
    plus a local `key`, plus optional `depends_on` and `artifacts`. Crucially
    does NOT carry `existing_calculation_id` (DR-0029 Requirement 1) — the
    bundle is self-contained.
    """

    key: str = Field(min_length=1)
    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    level_of_theory: LevelOfTheoryRef
    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    literature: LiteratureUploadRequest | None = None

    parameters_json: dict | None = None     # incl. tckdb_origin per DR-0026
    parameters: list[CalculationParameterObservation] | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    # Result blocks (one matching `type`)
    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None
    irc_result: IRCResultPayload | None = None
    neb_result: NEBResultPayload | None = None
    scan_result: ScanResultPayload | None = None

    # Calc-level dependencies referenced by local key (non-auto edges)
    depends_on: list[CalculationDependencyInBundle] = Field(default_factory=list)

    # Artifacts attached to this calc — same shape as ArtifactIn
    artifacts: list[ArtifactIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """One result block, matching `type` (mirrors CalculationWithResultsPayload)."""
        # Same logic as CalculationWithResultsPayload.validate_result_matches_type.
        ...

    @model_validator(mode="after")
    def reject_existing_calculation_id(self) -> Self:
        """DR-0029 Requirement 1: bundle is self-contained.

        If a producer accidentally serializes an existing_calculation_id
        into parameters_json or via Pydantic extra-fields, reject explicitly.
        """
        # Implementation: walk model_extra for unexpected fields; reject any
        # named existing_calculation_id, source_calculation_id, etc.
        return self


class ConformerInBundle(SchemaBase):
    """One conformer with its primary opt + additional calcs."""

    key: str = Field(min_length=1)
    geometry: GeometryPayload                       # xyz_text + provenance fields
    primary_calculation: CalculationInBundle        # MUST be `type=opt` (validator)
    additional_calculations: list[CalculationInBundle] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_primary_is_opt(self) -> Self:
        if self.primary_calculation.type is not CalculationType.opt:
            raise ValueError(
                "ConformerInBundle.primary_calculation.type must be 'opt'."
            )
        return self


class ThermoSourceCalcInBundle(SchemaBase):
    """Thermo → calc link by local key.

    Only `calculation_key` is allowed inside a bundle. `existing_calculation_id`
    (DR-0028) is the primitive-endpoint mechanism and is intentionally
    not present here (DR-0029 Requirement 1).
    """

    calculation_key: str = Field(min_length=1)
    role: ThermoCalculationRole


class AppliedEnergyCorrectionInBundle(AppliedEnergyCorrectionUploadPayload):
    """Same shape as the primitive applied-correction payload but with
    bundle-level local-key references.

    The base class's `source_calculation_key` already points at a local
    string key; in the bundle context, that key resolves against the
    bundle's global calc-key namespace, not against an inline calcs list
    in the same upload.
    """
    # No new fields; the resolution semantics are bundle-level.


class ThermoInBundle(SchemaBase):
    """Thermo block within a bundle. Lives at bundle level (one thermo per
    species_entry); references calcs from any conformer via the bundle's
    global calc-key namespace.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)
    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    note: str | None = None

    nasa: ThermoNASACreate | None = None
    points: list[ThermoPointCreate] = Field(default_factory=list)

    source_calculations: list[ThermoSourceCalcInBundle] = Field(default_factory=list)
    applied_energy_corrections: list[AppliedEnergyCorrectionInBundle] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        # Same as ThermoUploadRequest.validate_temperature_range
        ...

    @model_validator(mode="after")
    def validate_has_scientific_content(self) -> Self:
        # Same as ThermoUploadRequest.validate_has_scientific_content
        ...

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        pairs = [(sc.calculation_key, sc.role) for sc in self.source_calculations]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "thermo.source_calculations must be unique by "
                "(calculation_key, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_nasa_midpoint_consistency(self) -> Self:
        """nasa.t_low ≤ t_mid ≤ t_high; mirror existing thermo upload validator."""
        ...


class ComputedSpeciesUploadRequest(SchemaBase):
    """Bundle upload payload for one computed species result."""

    species_entry: SpeciesEntryIdentityPayload

    conformers: list[ConformerInBundle] = Field(min_length=1)
    thermo: ThermoInBundle | None = None

    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    # ----- Bundle-wide validators -----

    @model_validator(mode="after")
    def validate_unique_conformer_keys(self) -> Self:
        keys = [c.key for c in self.conformers]
        if len(set(keys)) != len(keys):
            raise ValueError("conformers must have unique keys.")
        return self

    @model_validator(mode="after")
    def validate_unique_calculation_keys_global(self) -> Self:
        """Calc keys are GLOBAL across the bundle. See *Local-key namespace*
        below for rationale."""
        all_keys = []
        for conf in self.conformers:
            all_keys.append(conf.primary_calculation.key)
            all_keys.extend(c.key for c in conf.additional_calculations)
        if len(set(all_keys)) != len(all_keys):
            raise ValueError("calculation keys must be unique across the bundle.")
        return self

    @model_validator(mode="after")
    def validate_dependency_keys_resolve(self) -> Self:
        """Every depends_on.parent_calculation_key references a calc declared
        in the bundle."""
        defined = self._all_calc_keys()
        for conf in self.conformers:
            for calc in (conf.primary_calculation, *conf.additional_calculations):
                for dep in calc.depends_on:
                    if dep.parent_calculation_key not in defined:
                        raise ValueError(
                            f"calculation '{calc.key}' depends_on undefined "
                            f"calculation_key '{dep.parent_calculation_key}'."
                        )
        return self

    @model_validator(mode="after")
    def validate_thermo_source_keys_resolve(self) -> Self:
        if self.thermo is None:
            return self
        defined = self._all_calc_keys()
        for sc in self.thermo.source_calculations:
            if sc.calculation_key not in defined:
                raise ValueError(
                    f"thermo.source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'."
                )
        # Same for applied_energy_corrections[*].source_calculation_key
        for i, ac in enumerate(self.thermo.applied_energy_corrections):
            if (
                ac.source_calculation_key is not None
                and ac.source_calculation_key not in defined
            ):
                raise ValueError(
                    f"thermo.applied_energy_corrections[{i}].source_calculation_key "
                    f"references undefined calculation_key "
                    f"'{ac.source_calculation_key}'."
                )
        return self

    def _all_calc_keys(self) -> set[str]:
        keys: set[str] = set()
        for conf in self.conformers:
            keys.add(conf.primary_calculation.key)
            keys.update(c.key for c in conf.additional_calculations)
        return keys
```

### Local-key namespace — global, not per-conformer

**Decision:** calculation keys are unique across the entire bundle, not scoped to a conformer.

**Rationale:**
- Thermo source links and applied-correction source links reference any calc from any conformer. Per-conformer scoping would force every reference to be a `(conformer_key, calculation_key)` tuple; global scoping makes it a single string.
- Cross-conformer dependencies are rare but legal. Global scoping expresses them naturally; per-conformer scoping would need an escape hatch.
- Producers that want disambiguation across conformers can use prefixes (`conf0_opt`, `conf1_opt`, …) — that's a local convention, not a schema constraint.
- Conformer keys remain global for the same reasons.

The validator `validate_unique_calculation_keys_global` enforces this at parse time.

---

## Response schema

```python
class CalculationUploadRefInBundle(SchemaBase):
    """Bundle-flavored CalculationUploadRef carrying the local key plus
    the assigned id."""
    key: str
    calculation_id: int
    type: CalculationType
    role: Literal["primary", "additional"]


class ConformerUploadRefInBundle(SchemaBase):
    """Per-conformer ref in the bundle response."""
    key: str
    conformer_group_id: int
    conformer_observation_id: int
    primary_calculation: CalculationUploadRefInBundle
    additional_calculations: list[CalculationUploadRefInBundle]


class ThermoUploadRefInBundle(SchemaBase):
    thermo_id: int


class ComputedSpeciesUploadResult(BaseModel):
    species_entry_id: int
    type: str = "computed_species"
    conformers: list[ConformerUploadRefInBundle]
    thermo: ThermoUploadRefInBundle | None = None
    warnings: list[UploadWarning] = []
```

The response is structured per local key so consumers can map back. Order of `conformers` matches request order; order of `additional_calculations` within each conformer matches request order.

---

## Validation rules — where each lives

| # | rule | layer |
|---|---|---|
| 1 | At least one conformer | Pydantic `min_length=1` on `conformers` |
| 2 | Unique conformer keys | `validate_unique_conformer_keys` model_validator |
| 3 | Unique calculation keys (global) | `validate_unique_calculation_keys_global` model_validator |
| 4 | Primary calculation type must be `opt` | `ConformerInBundle.validate_primary_is_opt` |
| 5 | Calculation result block matches `type` (one-of) | Per-calc `validate_result_matches_type` (mirrors `CalculationWithResultsPayload`) |
| 6 | Exactly one result block per calc type | Same validator as #5 |
| 7 | `depends_on.parent_calculation_key` exists | `validate_dependency_keys_resolve` model_validator |
| 8 | Dependency role/type compatibility (per DR-0028) | **Workflow-level**, after key resolution to actual `Calculation.type`. Helper `_assert_dependency_role_type_compatible(parent_calc, role)` mirrors DR-0028 Requirement 1. |
| 9 | `thermo.source_calculations[*].calculation_key` exists | `validate_thermo_source_keys_resolve` model_validator |
| 10 | Thermo source role/type compatibility (per DR-0028) | **Workflow-level**, same helper as #8 against `ThermoCalculationRole` mapping. |
| 11 | Artifact aggregate size cap (whole bundle) | **Workflow-level**, before any storage write — `validate_total_upload_size(declared_or_actual_bytes_for_all_artifacts)` from `app/services/artifact_storage.py`. |
| 12 | Artifact validation pass before storage writes | **Workflow-level**, `validate_and_decode_all_artifacts(all_artifacts)` from `app/services/artifact_persistence.py` (DR-0027 two-pass batch). |
| 13 | NASA low/high midpoint consistency | `validate_nasa_midpoint_consistency` on `ThermoInBundle` |
| 14 | Thermo scientific content exists if `thermo` provided | `validate_has_scientific_content` on `ThermoInBundle` |
| 15 | No `existing_calculation_id` accepted in bundle schemas | `CalculationInBundle.reject_existing_calculation_id` model_validator + structural absence on `ThermoSourceCalcInBundle` (the field literally doesn't exist on the model). |
| 16 | Unique `(calculation_key, role)` pairs in `thermo.source_calculations` | `validate_unique_source_calculation_pairs` on `ThermoInBundle` |
| 17 | Authorization (caller can write to species_entry) | **Route-level**, via `current_user` + species_entry resolution. The bundle creates all calcs/conformers/etc., so caller's `created_by` flows through naturally; the species_entry resolution either creates a new entry (caller becomes creator) or finds an existing one (no per-row authz check today; consistent with primitive endpoints). |

Validators #11, #12 cannot run at Pydantic time because they need decoded byte counts. They run as the first step of the workflow's pass-1.

---

## Workflow persistence order

`backend/app/workflows/computed_species.py:persist_computed_species_upload(session, request, *, created_by) -> ComputedSpeciesUploadOutcome`

```python
@dataclass
class ComputedSpeciesUploadOutcome:
    species_entry: SpeciesEntry
    conformers: list[ConformerUploadOutcomeInBundle]
    thermo: Thermo | None


@dataclass
class ConformerUploadOutcomeInBundle:
    conformer_in_bundle: ConformerInBundle    # for key → response mapping
    observation: ConformerObservation
    group_id: int
    primary_calculation: Calculation
    additional_calculations: list[Calculation]   # parallel to request order
```

### Step-by-step workflow

```
PASS 1 (in-memory, before any DB or S3 write)
  1a. Walk all artifacts across all calcs, decode + validate via
      validate_and_decode_all_artifacts(all_artifacts).
      → On failure: 422 from ArtifactValidationError.
  1b. Compute aggregate decoded byte total.
      validate_total_upload_size([decoded.bytes for ...]).
      → On failure: 422.

PASS 2 (DB writes; transaction wraps everything from here)
  2.  resolve_species_entry(session, request.species_entry, created_by=...)
      → returns SpeciesEntry (existing or new)

  3.  For each ConformerInBundle (in request order):
      a. resolve_geometry_payload(session, conformer.geometry)
         → returns Geometry (existing or new)
      b. resolve_conformer_group(session, species_entry_id, geometry,
                                  created_by=...)
         → reuses existing group on basin match; creates new otherwise.
         (Requirement 6 of DR-0029: same semantics as /uploads/conformers.)
      c. Create ConformerObservation row (always new — observations append
         per Requirement 6).

  4.  For each ConformerInBundle:
      a. Build a CalculationWithResultsPayload from the primary calc
         (omit `key`, `depends_on`, `artifacts` — those are bundle-level).
      b. resolve_and_persist_calculation_with_results(session, primary,
         species_entry_id=species_entry.id,
         conformer_observation_id=observation.id,
         created_by=...)
         → returns the primary Calculation row.
      c. For each additional calc:
         persist_additional_calculations(...) — same service, which auto-
         creates freq_on/single_point_on dependency rows to the primary
         (per app/services/calculation_resolution.py:_DEPENDENCY_ROLE_FOR_TYPE).
      d. Maintain a `calc_keys_to_id: dict[str, Calculation]` map for
         later cross-references.

  5.  For each calc with a non-empty `depends_on`:
      a. For each CalculationDependencyInBundle:
         - Look up parent: parent_calc = calc_keys_to_id[dep.parent_calculation_key].
         - Validate role/type compatibility (per DR-0028 Requirement 1):
           _assert_dependency_role_type_compatible(parent_calc, dep.role).
           → On failure: 422.
         - Insert CalculationDependency(parent=parent_calc, child=calc,
                                        role=dep.role).
         (Skip if the auto-creation in step 4c already produced this exact
         (parent, child, role) — idempotent within a transaction.)

  6.  For each calc with non-empty `artifacts`:
      a. persist_artifact_batch(session, calculation_id=calc.id,
                                artifacts=calc.artifacts,
                                created_by=created_by)
         → atomic per-batch storage + row creation; flushes inside the
         service. On any failure during this step, the workflow's outer
         compensation (see *Transaction & compensation* below) deletes any
         S3 objects already stored for THIS bundle and rolls back the SQL.

  7.  If request.thermo is not None:
      a. Build a synthetic ThermoUploadRequest from request.thermo +
         species_entry. The synthetic request has empty `calculations` and
         empty `source_calculations` (we'll splice resolved source links
         in step 8c).
      b. resolve_thermo_upload(session, synthetic_request,
                               species_entry_id=species_entry.id)
         → returns a ThermoCreate.
      c. (See step 8 for source-calc linking before persisting.)

  8.  If request.thermo is not None:
      a. For each ThermoSourceCalcInBundle:
         - parent_calc = calc_keys_to_id[sc.calculation_key].
         - Validate role/type compatibility (per DR-0028 Requirement 1):
           _assert_thermo_role_type_compatible(parent_calc, sc.role).
           → On failure: 422.
         - Build ThermoSourceCalculationCreate(calculation_id=parent_calc.id,
                                               role=sc.role).
      b. Splice the resolved source_calculations into the ThermoCreate
         (model_copy with update={"source_calculations": resolved}).
      c. persist_thermo(session, thermo_create, created_by=created_by)
         → returns the Thermo row.

  9.  If request.thermo.applied_energy_corrections is non-empty:
      For each AppliedEnergyCorrectionInBundle:
        a. Resolve source_calculation_key (if present) via calc_keys_to_id.
        b. Validate role/type compatibility for the FSF source if applicable.
        c. create_applied_energy_correction(session, payload,
            target_species_entry_id=species_entry.id,
            source_calculation_id=resolved_id,
            created_by=created_by)
        (For v0, this fires only for FSF entries that ARC supplies; AEC/BAC
        scheme rows are deferred per DR-0028.)

 10.  session.flush()  # persists everything in the transaction view
```

The route handler then commits via `get_write_db`'s success path, or rolls back via its exception path.

### Transaction & compensation

- The whole workflow runs inside the route's `get_write_db` transaction. SQL rollback on any exception is automatic via that dependency.
- **Artifact compensation is the load-bearing part.** Step 6 calls `persist_artifact_batch`, which handles intra-batch compensation (DR-0027): if storage fails partway through a single calc's artifacts, that batch's already-stored objects are deleted before re-raising.
- **Cross-step S3 leakage** is the case where calc A's artifacts succeed (S3 + rows), then calc B's artifacts fail. Calc A's bytes are durable in S3, but the SQL rollback discards calc A's rows — orphan bytes. Mitigation: maintain a `_bundle_stored_shas: list[str]` accumulator across all `persist_artifact_batch` calls, and on any post-step-6 failure, run `_compensate_stored_objects(bundle_stored_shas)` to delete them.

```python
def persist_computed_species_upload(session, request, *, created_by):
    # ... pass 1, steps 2-5 ...

    bundle_stored_shas: list[str] = []
    try:
        for conformer_outcome in conformer_outcomes:
            for calc in conformer_outcome.all_calcs():
                if calc_in_bundle.artifacts:
                    artifact_rows = persist_artifact_batch(
                        session,
                        calculation_id=calc.id,
                        artifacts=calc_in_bundle.artifacts,
                        created_by=created_by,
                    )
                    # Track stored shas for cross-step compensation.
                    bundle_stored_shas.extend(
                        r.sha256 for r in artifact_rows if r.sha256
                    )
        # ... thermo persistence ...
        session.flush()
    except Exception:
        # SQL rollback handled by get_write_db; compensate S3 here.
        _compensate_stored_objects(bundle_stored_shas)
        raise

    return outcome
```

`_compensate_stored_objects` already exists in `app/services/artifact_persistence.py` — reuse it.

The MVP-acceptable orphan window from DR-0027 still applies: if pass-2 succeeds but the route's commit fails, bytes are content-addressed orphans. GC sweep cleans them up later.

---

## Local-key resolution (one shared map per bundle)

Across pass 2, the workflow maintains a single dict:

```python
calc_keys_to_id: dict[str, Calculation] = {}
```

- Steps 4a-4c populate it as calcs are created.
- Steps 5, 6, 8, 9 read from it.

This dict is the entire local-key resolver. It does not need to be a class — a local variable in the workflow function. If a key is not present at lookup time, it's a workflow bug (the validators in pass 1 should have caught it) — raise an internal error, not a user-facing 422.

---

## Idempotency

The route handler uses the standard `idempotency_dependency`:

```python
@router.post(
    "/computed-species",
    response_model=ComputedSpeciesUploadResult,
    status_code=201,
)
def upload_computed_species(
    request: ComputedSpeciesUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry(request.species_entry)
    outcome = persist_computed_species_upload(
        session, request, created_by=current_user.id
    )
    result = _build_response(outcome, warnings=warnings)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result
```

Same key + same body → cached response. Same key + different body → 409 (existing middleware behavior).

The route has no path parameters, so DR-0027's "idempotency must include path params" concern does not apply here.

---

## Test plan

`backend/tests/api/test_api_upload_computed_species.py` — endpoint-level. `backend/tests/workflows/test_computed_species_upload.py` — direct workflow calls.

| # | case | asserts |
|---|---|---|
| 1 | Happy path: 1 species, 1 conformer, opt+freq+sp, 6 artifacts, thermo with NASA + 4 Cp points + freq-scale-factor applied row | 201; species_entry, conformer_group, conformer_observation, 3 calcs, opt_result, freq_result, sp_result, 6 artifacts, freq_on dep, single_point_on dep, thermo, thermo_nasa, 4 thermo_points, 3 thermo_source_calculation rows, 1 applied_energy_correction row. Response maps every local key to an id. |
| 2 | Multiple conformers (3) | 3 conformer_observation rows, 3 distinct conformer_group rows (assuming distinct basins). All linked to one species_entry. |
| 3 | Multiple conformers, 2 of them basin-match | 3 conformer_observation rows, **2 conformer_group rows** (one shared by the two matching conformers, one for the third). Confirms Requirement 6 reuse semantics. |
| 4 | Chemistry-only bundle without thermo | `thermo` field is `null` in response; no thermo row created. |
| 5 | `parent_calculation_key` references undeclared calc | 422 at Pydantic validation; response error message names the offending field. No DB or S3 writes attempted. |
| 6 | `thermo.source_calculations[*].calculation_key` references undeclared calc | Same as #5. |
| 7 | Duplicate `conformer.key` | 422 at Pydantic validation. |
| 8 | Duplicate `calculation.key` (across conformers — global namespace) | 422 at Pydantic validation. |
| 9 | Calc with `type=freq` providing `opt_result` | 422 at Pydantic validation (per `validate_result_matches_type`). |
| 10 | `depends_on.role=freq_on` pointing at a calc with `type=sp` | 422 at workflow validation step 5 (DR-0028 Requirement 1). |
| 11 | `thermo.source_calculations[*].role=opt` referencing a calc with `type=freq` | 422 at workflow validation step 8 (DR-0028 Requirement 1). |
| 12 | Invalid artifact (ESS signature mismatch on artifact #5 of 12 across the bundle) | 422 from pass 1; **zero DB writes**, **zero S3 writes** (assert via mocked storage call count). |
| 13 | Storage failure on artifact #3 (MinIO unavailable) | 503; SQL rolled back; artifacts 1-2 compensated (sha-deletes attempted). |
| 14 | Idempotency replay: same key + same body | Cached `ComputedSpeciesUploadResult` returned. No new DB rows, no new S3 writes, no new conformer_observation appended. |
| 15 | Idempotency conflict: same key + different body | 409 with `code=idempotency_conflict`. |
| 16 | Cross-call isolation: same body, two different bundles in series | Two distinct species/conformer/calc/artifact/thermo row sets. |
| 17 | `existing_calculation_id` field accidentally serialized into a calc payload | 422; `reject_existing_calculation_id` validator catches it (assuming the schema uses `extra="forbid"` from `SchemaBase`, which all bundle schemas do). |
| 18 | Conformer-group reuse: bundle A creates a group, then bundle B (different idempotency key, same molecule, same basin) is uploaded | Bundle B's conformer_observation reuses A's conformer_group; B creates a fresh observation. Confirms DR-0029 Requirement 6 across bundles. |
| 19 | Thermo NASA mapping | `nasa.a1..a7` ↔ low-T poly, `nasa.b1..b7` ↔ high-T poly, `nasa.t_low/t_mid/t_high` consistent. |
| 20 | Thermo points mapping | Each `points[*]` becomes a `thermo_point` row with correct temperature_k, cp_j_mol_k. Unique-temperature validator still applies. |
| 21 | Thermo source links by local calc key | Each `source_calculations[*]` becomes a `thermo_source_calculation` row with correct calculation_id (resolved from local key) and role. |
| 22 | Authorization: unauthenticated request | 401 from `get_current_user`. |
| 23 | Empty `conformers` list | 422 (Pydantic `min_length=1`). |
| 24 | Existing primitive endpoints regression | All `/uploads/conformers`, `/uploads/thermo`, `/calculations/{id}/artifacts` tests in the existing suite continue to pass without modification. |

### Test infrastructure

- Reuse `stub_store_artifact` and `stub_delete_artifact` fixtures from `backend/tests/api/test_api_calculation_artifacts.py` (or hoist them to a shared conftest).
- For test #14 (idempotency replay): use the existing idempotency-test pattern from `test_api_calculation_artifacts.py:TestIdempotency`.
- Test #18 (cross-bundle group reuse): two POSTs in the same test, with a DB query between to confirm group_id is shared.
- Test #24 (regression) runs by simply not modifying existing tests; CI catches regressions automatically.

---

## Implementation order

The order isolates risk: refactors first (none needed for v0), schemas and validators second (testable in isolation), workflow third (testable in isolation), route fourth (thin), tests last.

1. **Schemas in `app/schemas/workflows/computed_species_upload.py`.** All Pydantic models with full validators. Unit tests against these schemas alone (no DB) for cases 5-9, 17, 23. Run `pytest backend/tests/schemas/test_computed_species_upload_schema.py -v`.

2. **Workflow in `app/workflows/computed_species.py`.** The orchestrator function plus the two role/type compatibility helpers (`_assert_dependency_role_type_compatible`, `_assert_thermo_role_type_compatible` — both reuse DR-0028's mapping table). Workflow-level tests against an in-memory session for cases 1, 2, 3, 4, 10, 11, 18, 19, 20, 21.

3. **Route in `app/api/routes/uploads.py`.** Thin wrapper around the workflow. API-level tests for cases 12, 13, 14, 15, 16, 22 (these need the FastAPI test client + fixture stubs).

4. **Test #18 (cross-bundle group reuse) + test #24 (regression).** Last because they exercise the full system.

5. **Final smoke test**: a hand-crafted bundle JSON posted via `curl` against a local TCKDB, confirming the response shape matches DR-0029's example.

---

## Open questions

These are not blocking the spec but worth implementer judgment.

1. **Should `depends_on` be allowed to reference cross-conformer calcs?** Today the design allows it via the global key namespace. The workflow doesn't actively prevent it. Use case: an opt restart in conformer B that started from conformer A's converged geometry. Recommend: allow, no validator restriction. If a real abuse case appears, restrict it later.

2. **Should `applied_energy_corrections` validate that `source_calculation_key` references a calc owned by the same species_entry as the thermo?** Today the schema doesn't enforce this. The workflow could add the check. Recommend: yes, mirror DR-0028 Requirement 1's owner check via `_assert_calculation_owned_by`. Belt-and-braces against producer typos.

3. **Should the response include the `calculation_dependency` rows that were created (auto + explicit)?** The DR-0029 example response doesn't. Adding it would help curators verify the DAG was built as expected. Recommend: defer; consumers can `GET /calculations/{id}/dependencies` if they need to inspect.

4. **The synthetic `ThermoUploadRequest` (step 7a) is a workaround.** A cleaner refactor would extract the thermo-persistence logic from the existing workflow into a reusable function that doesn't require constructing a synthetic request. Recommend: file as follow-up; v0 uses the synthetic-request approach to avoid scope creep.

5. **`reject_existing_calculation_id` validator for `parameters_json`.** The current proposal walks model_extra; what if a producer puts `existing_calculation_id` *inside* `parameters_json` (which is `dict | None`)? The validator should walk that dict too. Recommend: add a recursive check looking for any field literally named `existing_calculation_id` anywhere in the payload tree. The cost is one tree-walk; the benefit is the bundle's self-contained property is guaranteed even against creative misuse.

---

## Deliverables summary

| | |
|---|---|
| Files added | 4 (schemas, workflow, two test files) |
| Files modified | 1 (`uploads.py` route + result type) |
| Existing services modified | 0 — all reused |
| DB schema changes | 0 |
| Alembic revisions | 0 |
| New endpoints | 1 (`POST /uploads/computed-species`) |
| Existing endpoints affected | 0 |
| Estimated LOC | ~600 source + ~800 tests = ~1400 total |
| Risk profile | Low — all DB/S3 surfaces already in production via primitive endpoints; bundle is orchestration on top |
