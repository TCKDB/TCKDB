// One builder per response shape the machine-review pages read. Ids are large and distinctive so an
// "id is not shown" check cannot pass by coincidence. The parity test keeps every key set equal to the API schema.

export const SUBMISSION_ID = 123456
export const KINETICS_RECORD_ID = 987654
export const CALC_RECORD_ID = 987655
export const AEC_RECORD_ID = 987656
export const KINETICS_REF = "kin_4k2mq9x4ta8ndrwe5hvzcbj6y1"
export const CALC_REF = "calc_7k2mq9x4ta8ndrwe5hvzcbj6y1"
export const REACTION_ENTRY_REF = "rxe_9m2mq9x4ta8ndrwe5hvzcbj6y1"
export const SPECIES_ENTRY_REF = "spe_3q2mq9x4ta8ndrwe5hvzcbj6y1"
export const SUBMISSION_REF = "sub_5v2mq9x4ta8ndrwe5hvzcbj6y1"

type Json = Record<string, unknown>

export function finding(over: Json = {}): Json {
    return {
        severity: "warning",
        category: "kinetics",
        message: "REVIEWER_MESSAGE the rate constant is far above the collision limit",
        evidence_keys: ["kinetics.a", "kinetics.ea_kj_mol"],
        recommended_action: "REVIEWER_ACTION re-check the A factor units",
        curator_task_id: null,
        ...over,
    }
}

export function recordReview(over: Json = {}): Json {
    return {
        audit_event_id: 7001,
        reviewed_at: "2026-09-14T10:00:00Z",
        status: "machine_screened_needs_attention",
        curator_priority: "high",
        model: "reviewer-model-1",
        provider: "reviewer-provider",
        is_latest: true,
        findings: [finding({ curator_task_id: 41, severity: "critical" }), finding({ category: "units" })],
        ...over,
    }
}

export function summary(over: Json = {}): Json {
    return {
        status: "machine_screened_needs_attention",
        curator_priority: "high",
        findings_count: 2,
        highest_severity: "critical",
        model: "reviewer-model-1",
        provider: "reviewer-provider",
        reviewed_at: "2026-09-14T10:00:00Z",
        submission_id: SUBMISSION_ID,
        ...over,
    }
}

export function recordInspection(over: Json = {}): Json {
    return {
        record_type: "kinetics",
        record_public_ref: KINETICS_REF,
        record_id: KINETICS_RECORD_ID,
        container_type: "reaction_entry",
        container_ref: REACTION_ENTRY_REF,
        latest_summary: summary(),
        all_record_reviews_count: 2,
        all_record_reviews: [
            recordReview(),
            recordReview({
                audit_event_id: 7000,
                reviewed_at: "2026-09-10T09:30:00Z",
                status: "machine_screened_warning",
                curator_priority: "low",
                is_latest: false,
                findings: [finding({ message: "REVIEWER_MESSAGE earlier concern", category: "provenance" })],
            }),
        ],
        ...over,
    }
}

export function calculationInspection(over: Json = {}): Json {
    return recordInspection({
        record_type: "calculation",
        record_public_ref: CALC_REF,
        record_id: CALC_RECORD_ID,
        container_type: null,
        container_ref: null,
        latest_summary: summary({ status: "machine_screened_warning", highest_severity: "warning", findings_count: 1, curator_priority: "medium" }),
        all_record_reviews_count: 1,
        all_record_reviews: [
            recordReview({
                status: "machine_screened_warning",
                curator_priority: "medium",
                findings: [finding({ category: "calculation_parameters", message: "REVIEWER_MESSAGE basis set not stated" })],
            }),
        ],
        ...over,
    })
}

export function correctionInspection(over: Json = {}): Json {
    return recordInspection({
        record_type: "applied_energy_correction",
        record_public_ref: null,
        record_id: AEC_RECORD_ID,
        container_type: "species_entry",
        container_ref: SPECIES_ENTRY_REF,
        latest_summary: summary({ status: "machine_screened_pass", highest_severity: "info", findings_count: 1, curator_priority: "low" }),
        all_record_reviews_count: 1,
        all_record_reviews: [
            recordReview({
                status: "machine_screened_pass",
                curator_priority: "low",
                findings: [finding({ severity: "info", category: "thermo", message: "REVIEWER_MESSAGE correction looks standard" })],
            }),
        ],
        ...over,
    })
}

export function unmappedFinding(over: Json = {}): Json {
    return {
        audit_event_id: 7001,
        reason: "submission_scoped",
        cited_record_type: null,
        cited_record_ref: null,
        severity: "info",
        category: "schema_gap",
        message: "REVIEWER_MESSAGE the submission has no stated purpose",
        evidence_keys: [],
        recommended_action: null,
        ...over,
    }
}

export function run(over: Json = {}): Json {
    return {
        audit_event_id: 7001,
        reviewed_at: "2026-09-14T10:00:00Z",
        status: "machine_screened_needs_attention",
        curator_priority: "high",
        model: "reviewer-model-1",
        provider: "reviewer-provider",
        findings_count: 5,
        mapped_findings_count: 3,
        unmapped_findings_count: 2,
        highest_severity: "critical",
        summary: "REVIEWER_SUMMARY overall the kinetics look unreliable",
        parsed: true,
        parse_warnings: [],
        ...over,
    }
}

export function inspection(over: Json = {}): Json {
    return {
        submission_id: SUBMISSION_ID,
        record_summaries: [recordInspection(), calculationInspection(), correctionInspection()],
        unmapped_findings_count: 2,
        unmapped_findings: [
            unmappedFinding(),
            unmappedFinding({
                reason: "unknown_record_type",
                cited_record_type: "CITED_TYPE_VERBATIM",
                cited_record_ref: "CITED_REF_VERBATIM",
                severity: "warning",
                category: "consistency",
                message: "REVIEWER_MESSAGE cites something this archive does not hold",
                evidence_keys: ["x.y"],
                recommended_action: "REVIEWER_ACTION ask the depositor",
            }),
        ],
        runs: [
            run(),
            run({
                audit_event_id: 7000,
                reviewed_at: "2026-09-10T09:30:00Z",
                parsed: false,
                status: null,
                summary: null,
                curator_priority: null,
                highest_severity: null,
                model: null,
                provider: null,
                findings_count: 0,
                mapped_findings_count: 0,
                unmapped_findings_count: 0,
                parse_warnings: ["RUN_PARSE_WARNING the reply was not JSON"],
            }),
        ],
        mapping_warnings: ["MAPPING_WARNING one finding named two records"],
        parse_warnings: ["PARSE_WARNING one finding had no severity"],
        source_audit_event_ids: [7001, 7000],
        ...over,
    }
}

export function submission(over: Json = {}): Json {
    return {
        id: SUBMISSION_ID,
        created_at: "2026-09-01T08:00:00Z",
        public_ref: SUBMISSION_REF,
        created_by: 1,
        submission_kind: "computed_reaction",
        source_kind: "api",
        status: "approved",
        submitted_at: "2026-09-01T08:15:00Z",
        is_public: true,
        title: "DEPOSITOR_TITLE",
        summary: "DEPOSITOR_SUMMARY",
        approved_at: null,
        approved_by: null,
        rejected_at: null,
        rejected_by: null,
        rejection_reason: null,
        correction_due_at: null,
        llm_precheck_at: null,
        llm_precheck_label: null,
        llm_precheck_model: null,
        llm_precheck_summary: null,
        supersedes_submission_id: null,
        upload_job_id: null,
        ...over,
    }
}

export function auditEvent(over: Json = {}): Json {
    return {
        id: 5001,
        submission_id: SUBMISSION_ID,
        created_at: "2026-09-02T12:30:00Z",
        actor_kind: "admin",
        actor_user_id: 1,
        event_kind: "curator_approved",
        from_status: "pending",
        to_status: "approved",
        reason: "AUDIT_REASON",
        summary: "AUDIT_SUMMARY",
        details_json: { note: "AUDIT_DETAILS" },
        related_submission_id: null,
        ...over,
    }
}

export function recordLink(over: Json = {}): Json {
    return {
        id: 1,
        created_at: "2026-09-01T08:15:00Z",
        submission_id: SUBMISSION_ID,
        record_type: "kinetics",
        record_id: KINETICS_RECORD_ID,
        role: null,
        ...over,
    }
}

export function overviewRow(over: Json = {}): Json {
    return {
        id: SUBMISSION_ID,
        public_ref: SUBMISSION_REF,
        status: "approved",
        submission_kind: "computed_reaction",
        created_by: 1,
        created_by_username: "ada",
        submitted_at: "2026-09-01T08:15:00Z",
        title: "DEPOSITOR_TITLE",
        machine_review: {
            run_count: 2,
            open_job: null,
            open_task_count: 2,
            task_count: 5,
            latest_run: {
                audit_event_id: 7001,
                parsed: true,
                model: "reviewer-model-1",
                provider: "reviewer-provider",
                reviewed_at: "2026-09-14T10:00:00Z",
                status: "machine_screened_needs_attention",
                highest_severity: "critical",
                findings_count: 5,
            },
        },
        ...over,
    }
}

export function overviewPage(rows: Json[] = [overviewRow()], over: Json = {}): Json {
    return { items: rows, total: rows.length, skip: 0, limit: 50, ...over }
}

export function curatorTask(over: Json = {}): Json {
    return {
        id: 41,
        submission_id: SUBMISSION_ID,
        record_type: "kinetics",
        record_public_ref: KINETICS_REF,
        record_id: KINETICS_RECORD_ID,
        container_type: "reaction_entry",
        container_ref: REACTION_ENTRY_REF,
        finding_fingerprint: "abc123",
        workflow_state: "needs_curator_review",
        machine_review_status: "machine_screened_needs_attention",
        highest_severity: "critical",
        findings_count: 2,
        source_audit_event_id: 7001,
        assigned_to: null,
        assigned_to_username: null,
        created_at: "2026-09-14T10:00:00Z",
        updated_at: "2026-09-15T11:45:00Z",
        resolved_at: null,
        resolved_by: null,
        resolved_by_username: null,
        resolution_note: null,
        made_under: null,
        finding: finding({ curator_task_id: 41, severity: "critical" }),
        finding_match: "source_audit_event",
        finding_audit_event_ids: [7001],
        ...over,
    }
}

export function machineFindings(over: Json = {}): Json {
    return {
        status: "machine_screened_needs_attention",
        highest_severity: "critical",
        findings_count: 2,
        reviewed_at: "2026-09-14T10:00:00Z",
        open_task_count: 2,
        open_task_ids: [41, 42],
        ...over,
    }
}

export function reviewQueueRecord(over: Json = {}): Json {
    return {
        id: 301,
        created_at: "2026-09-12T08:00:00Z",
        created_by: null,
        reviewed_by: null,
        reviewed_at: null,
        first_approved_at: null,
        record_type: "kinetics",
        record_id: KINETICS_RECORD_ID,
        record_public_ref: KINETICS_REF,
        container_type: "reaction_entry",
        container_ref: REACTION_ENTRY_REF,
        submission_id: SUBMISSION_ID,
        status: "not_reviewed",
        note: null,
        actor_may_approve: true,
        machine_findings: machineFindings(),
        ...over,
    }
}

export const JOB_ID = 424242

export function job(over: Json = {}): Json {
    return {
        id: JOB_ID,
        submission_id: SUBMISSION_ID,
        requested_by: 3,
        requested_by_username: "cora",
        provider_key: "fake",
        executor: "in_process",
        status: "succeeded",
        cancel_requested: false,
        failure_code: null,
        step_count: 4,
        model: "fake_test/machine-review",
        prompt_name: "machine_review",
        prompt_version: 2,
        audit_event_id: 7001,
        created_at: "2026-11-24T10:00:00",
        started_at: "2026-11-24T10:00:01",
        finished_at: "2026-11-24T10:00:07",
        ...over,
    }
}

export function jobEvent(over: Json = {}): Json {
    return {
        seq: 1,
        kind: "claimed",
        tool_name: null,
        status_code: null,
        started_at: null,
        duration_ms: null,
        ...over,
    }
}

export const JOB_EVENT_KINDS = ["claimed", "context_built", "model_turn", "final_output", "finished"]

export function jobDetail(over: Json = {}): Json {
    return {
        ...job(),
        events: JOB_EVENT_KINDS.slice(0, 4).map((kind, i) => jobEvent({ seq: i + 1, kind })),
        ...over,
    }
}

export function openJob(over: Json = {}): Json {
    return {
        id: JOB_ID,
        status: "running",
        step_count: 2,
        cancel_requested: false,
        created_at: "2026-11-24T10:00:00",
        started_at: "2026-11-24T10:00:01",
        ...over,
    }
}

export function jobPage(items: Json[] = [job()]): Json {
    return { items, total: items.length, skip: 0, limit: 50 }
}

export function providers(over: Record<string, string> = {}): Json {
    const base: Record<string, string> = {
        fake: "ok",
        local: "provider_not_configured",
        cloud: "provider_not_configured",
        claude_code: "remote_runner_unavailable",
        codex: "remote_runner_unavailable",
        ...over,
    }
    return { providers: Object.entries(base).map(([provider_key, availability]) => ({ provider_key, availability })) }
}

export function settings(over: Json = {}): Json {
    return {
        machine_review_enabled: true,
        curator_requests_enabled: true,
        assistant_mode: "test",
        review_allowed: true,
        curator_allowed: true,
        ...over,
    }
}
