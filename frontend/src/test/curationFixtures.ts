// Response shapes of the curation routes (`backend/app/schemas/entities/structure_evidence_finding.py` and
// `reproducibility_assessment.py`), checked against the golden OpenAPI by `curationFixtures.parity.test.ts`.

export const SENTINEL = {
    id: 918273,
    recordId: 5550123,
    assessorUserId: 7770456,
    submissionId: 6660789,
    calculationId: 4440321,
    artifactId: 3330654,
    levelOfTheoryId: 2220987,
}

export function findingRead(overrides: Record<string, unknown> = {}) {
    return {
        finding_ref: "sef_first",
        scope: "calculation",
        subject_ref: "calc_freq_one",
        kind: "role_invalidation",
        role: "energy",
        verdict: "invalidates",
        authority: "authorized_adjudication",
        rationale: "The energy comes from a different structure.",
        source_calculation_ref: "calc_opt_one",
        literature_ref: null,
        applied_kind: "role_invalidation",
        applied_role: "energy",
        supersedes_finding_ref: null,
        settled_by_finding_ref: null,
        in_force: true,
        removed_adjudications: [],
        created_at: "2026-07-21T12:06:50.748258",
        ...overrides,
    }
}

export function removedAdjudication(overrides: Record<string, unknown> = {}) {
    return {
        finding_ref: "sef_gone",
        deleted_calculation_ref: "calc_deleted",
        deleted_at: "2026-09-02T08:00:00.000000",
        ...overrides,
    }
}

export function findingPage(findings: unknown[], overrides: Record<string, unknown> = {}) {
    return { subject_ref: "calc_freq_one", findings, offset: 0, limit: 20, ...overrides }
}

export function assessmentRead(overrides: Record<string, unknown> = {}) {
    return {
        id: SENTINEL.id,
        assessment_ref: "rpa_latest",
        record_type: "calculation",
        record_id: SENTINEL.recordId,
        grade: "auditable",
        rubric_name: "tckdb_reproducibility",
        rubric_version: 3,
        context_hash: "c0ffee".repeat(10) + "abcd",
        context_json: { calculations: { [String(SENTINEL.calculationId)]: "opt" } },
        passed: [
            { name: "target_identity", level: "described", outcome: "passed", evidence: { reference: "calc_x" } },
            { name: "scientific_context", level: "described", outcome: "passed", evidence: {} },
            { name: "source_role_preservation", level: "auditable", outcome: "not_applicable", evidence: { reason: "calculation_is_the_evidence_root" } },
        ],
        missing: [
            { name: "preserved_input_artifacts", level: "rerunnable", outcome: "missing", evidence: { input_artifacts: [] } },
            { name: "execution_parameter_snapshot", level: "rerunnable", outcome: "missing", evidence: { level_of_theory_id: SENTINEL.levelOfTheoryId } },
            { name: "verified_output_artifact_bytes", level: "auditable", outcome: "missing", evidence: { artifact_id: SENTINEL.artifactId } },
        ],
        warnings: [
            { code: "artifact_integrity_failed", evidence: { artifact_id: SENTINEL.artifactId, sha256: "ab".repeat(32), integrity_event_ref: "aie_event" } },
        ],
        assessor_kind: "system",
        assessor_user_id: SENTINEL.assessorUserId,
        source_submission_id: SENTINEL.submissionId,
        assessed_at: "2026-11-02T09:30:00.000000",
        created_at: "2026-11-02T09:30:00.000000",
        ...overrides,
    }
}
