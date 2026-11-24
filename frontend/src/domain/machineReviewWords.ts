import { recordTypeGroupLabel } from "./recordRoute"

/**
 * Plain wording for the machine-review vocabulary, built from the codes the API sends.
 * An unfamiliar token comes back as itself (`known: false`) so no page shows another value's words for it.
 */

export type WordKind =
    | "status"
    | "severity"
    | "category"
    | "unmappedReason"
    | "priority"
    | "findingMatch"
    | "submissionStatus"
    | "submissionKind"
    | "auditEventKind"
    | "actorKind"
    | "recordType"
    | "taskState"
    | "jobStatus"
    | "jobFailure"
    | "availability"
    | "jobEventKind"
    | "providerKey"

export type Words = { label: string; meaning: string; known: boolean }

type Table = Record<string, [label: string, meaning: string]>

const TABLES: Record<Exclude<WordKind, "recordType">, Table> = {
    status: {
        not_run: ["not run", "No mapped machine review for this record."],
        machine_screened_pass: ["screened: pass", "The machine found no obvious issue. A person has not approved it."],
        machine_screened_warning: ["screened: warning", "An advisory warning. Nothing was rejected."],
        machine_screened_needs_attention: ["screened: needs attention", "Needs a person's attention. The record is not hidden."],
        machine_review_failed: ["review failed", "The reviewer failed. This says nothing against the record."],
    },
    severity: {
        info: ["info", "For information only."],
        warning: ["warning", "Worth a look."],
        critical: ["critical", "Likely to matter if it is right."],
    },
    category: {
        provenance: ["provenance", "How the data was produced."],
        units: ["units", "Units or conversions."],
        geometry: ["geometry", "Molecular geometry."],
        kinetics: ["kinetics", "Rate coefficients."],
        thermo: ["thermochemistry", "Thermochemical values."],
        statmech: ["statistical mechanics", "Statistical mechanics inputs."],
        transport: ["transport", "Transport properties."],
        transition_state_validation: ["transition-state validation", "Whether a transition state checks out."],
        calculation_parameters: ["calculation parameters", "Settings the calculation ran with."],
        consistency: ["consistency", "Values that disagree with each other."],
        schema_gap: ["schema gap", "Something the archive has no place to record."],
    },
    unmappedReason: {
        submission_scoped: ["about the whole submission", "The finding concerns the submission, not one record."],
        missing_record_ref: ["no record cited", "The reviewer named no record."],
        ambiguous_record_type: ["record type ambiguous", "The cited reference fits more than one record type."],
        unknown_record_type: ["record type not recognised", "The cited record type is not one this archive holds."],
        unlinked_record: ["record not in this submission", "The cited record is not linked to this submission."],
    },
    priority: {
        low: ["low", ""],
        medium: ["medium", ""],
        high: ["high", ""],
    },
    findingMatch: {
        source_audit_event: ["found in the review that raised the task", "The finding was found in the stored review the task came from."],
        submission_audit_events: ["found in another review of the same submission", "The task's own review no longer holds it, but another review of this submission does."],
        not_found: ["not found", "No stored review of this submission still contains this finding."],
    },
    submissionStatus: {
        pending: ["pending", ""],
        precheck_passed: ["precheck passed", ""],
        auto_flagged: ["flagged by the precheck", ""],
        approved: ["approved", ""],
        rejected: ["rejected", ""],
        superseded: ["superseded", ""],
        failed: ["failed", ""],
    },
    submissionKind: {
        computed_reaction: ["computed reaction", ""],
        computed_species: ["computed species", ""],
        conformer: ["conformer", ""],
        reaction: ["reaction", ""],
        kinetics: ["kinetics", ""],
        network: ["network", ""],
        network_pdep: ["pressure-dependent network", ""],
        statmech: ["statistical mechanics", ""],
        thermo: ["thermochemistry", ""],
        transition_state: ["transition state", ""],
        transport: ["transport", ""],
        other: ["other", ""],
    },
    auditEventKind: {
        submission_created: ["submission created", ""],
        ingestion_succeeded: ["ingestion succeeded", ""],
        ingestion_failed: ["ingestion failed", ""],
        llm_precheck_passed: ["precheck passed", ""],
        llm_precheck_flagged: ["precheck flagged", ""],
        llm_precheck_recorded: ["precheck recorded", ""],
        curator_approved: ["approved by a curator", ""],
        curator_rejected: ["rejected by a curator", ""],
        correction_window_opened: ["correction window opened", ""],
        correction_uploaded: ["correction uploaded", ""],
        submission_superseded: ["superseded", ""],
        status_changed: ["status changed", ""],
        public_visibility_changed: ["visibility changed", ""],
        observation_identity_attached: ["observation identity attached", ""],
    },
    actorKind: {
        user: ["depositor", ""],
        curator: ["curator", ""],
        admin: ["administrator", ""],
        llm: ["automated reviewer", ""],
        system: ["system", ""],
    },
    taskState: {
        untriaged: ["untriaged", "Nobody has looked at it yet."],
        needs_curator_review: ["needs review", "Waiting for a person."],
        in_curator_review: ["in review", "A person is looking at it."],
        resolved_no_action: ["closed: no action", "The finding was fair and nothing needs changing."],
        resolved_human_reviewed: ["closed: reviewed elsewhere", "A person reviewed the record elsewhere. This queue did not review it."],
        dismissed_machine_finding: ["dismissed", "A false positive, or not actionable."],
    },
    jobStatus: {
        queued: ["queued", "Waiting for a reviewer to pick it up."],
        running: ["running", "The reviewer is working on it."],
        succeeded: ["finished", "The reviewer finished and the result is recorded."],
        failed: ["did not finish", "The reviewer stopped before giving a result."],
        cancelled: ["cancelled", "Stopped before a result was recorded."],
    },
    jobFailure: {
        provider_unavailable: ["the reviewer could not be reached", "The model server did not answer."],
        provider_timeout: ["the reviewer took too long", "The model server did not answer in time."],
        budget_exhausted: ["the review ran out of steps or time", "The review used up its allowance before it could answer."],
        invalid_output: ["the reviewer's answer could not be used", "The answer did not match the format the archive accepts."],
        quota_exhausted: ["the reviewer's quota is used up", "The reviewing service refused for lack of quota."],
        auth_failed: ["the reviewer refused our credentials", "The reviewing service did not accept the archive's access."],
        worker_lost: ["the review was interrupted", "The process running the review stopped."],
        config_error: ["the reviewer is not set up correctly", "The deployment does not match the reviewer that was asked for."],
        internal_error: ["something went wrong in the archive", "The review failed for a reason on the archive's side."],
        machine_review_disabled: ["machine review was switched off", "An administrator turned machine review off before this one started."],
    },
    availability: {
        ok: ["available", "You may request this reviewer now."],
        mode_off: ["machine review is off in this deployment", "No reviewer is configured here."],
        machine_review_disabled: ["machine review is switched off", "An administrator has turned machine review off."],
        curator_requests_disabled: ["curator requests are switched off", "An administrator has switched off review requests from curators."],
        not_allowed_for_user: ["not available to your account", "This reviewer is not open to your role."],
        provider_not_configured: ["not set up in this deployment", "This reviewer is not the one this deployment is configured for."],
        remote_runner_unavailable: ["no remote runner is connected", "This reviewer runs on a separate machine that is not connected."],
    },
    jobEventKind: {
        claimed: ["picked up by a reviewer", ""],
        context_built: ["reading the submission", ""],
        model_turn: ["asking the model", ""],
        tool_call: ["looking something up", ""],
        tool_result: ["reading what the lookup returned", ""],
        retry: ["trying again", ""],
        final_output: ["writing the answer", ""],
        parse_failed: ["the answer could not be read", ""],
        finished: ["finished", ""],
    },
    providerKey: {
        fake: ["test reviewer", "A scripted reviewer that makes no model call."],
        local: ["local model", "A model server run by this deployment."],
        cloud: ["cloud model", "A hosted model service."],
        claude_code: ["Claude Code", "Claude, run on a separate machine."],
        codex: ["Codex", "Codex, run on a separate machine."],
    },
}

/** Providers that send the submission to a service outside this deployment. */
export const OUTSIDE_PROVIDERS: ReadonlySet<string> = new Set(["claude_code", "codex"])

/**
 * What the machine reviewer's status means for the curator, as advice and never as approval.
 * `not_run` has no entry: nothing was reviewed, so nothing is recommended.
 */
const RECOMMENDATION: Record<string, string> = {
    machine_screened_pass: "Machine reviewer recommends approval (advisory)",
    machine_screened_warning: "Machine reviewer recommends approval after reading its notes (advisory)",
    machine_screened_needs_attention: "Machine reviewer does not recommend approval yet (advisory)",
}

export const RECOMMENDATION_STATUSES: readonly string[] = Object.keys(RECOMMENDATION)

/**
 * The advice line for a stored status. A failed review reads "did not finish" with the words for `failureCode`;
 * `not_run` and an unfamiliar status give `null`, so no line is shown that this build cannot vouch for.
 */
export function recommendationFor(status: string, failureCode: string | null = null): string | null {
    if (status === "machine_review_failed") {
        return failureCode === null
            ? "Machine review did not finish"
            : `Machine review did not finish: ${wordLabel("jobFailure", failureCode)}`
    }
    return RECOMMENDATION[status] ?? null
}

export function wordsFor(kind: WordKind, token: string): Words {
    if (kind === "recordType") {
        return { label: recordTypeGroupLabel(token, 1), meaning: "", known: true }
    }
    const hit = TABLES[kind][token]
    if (hit === undefined) return { label: token, meaning: "", known: false }
    return { label: hit[0], meaning: hit[1], known: true }
}

export function wordLabel(kind: WordKind, token: string): string {
    return wordsFor(kind, token).label
}

/** The status tokens the legend lists, in severity order. */
export const STATUS_TOKENS = Object.keys(TABLES.status)

/** The class a machine-review status takes. `null` for a pass, which is not styled as a problem. */
export function statusClassName(token: string): string | null {
    switch (token) {
        case "machine_screened_warning":
            return "severity-warning"
        case "machine_screened_needs_attention":
            return "severity-critical"
        case "machine_review_failed":
        case "not_run":
            return "admin-absent"
        default:
            return null
    }
}

const pad = (n: number) => String(n).padStart(2, "0")

/**
 * An API timestamp as a Date. The backend sends naive UTC (no offset); `new Date` would read that as local time and
 * be off by the viewer's UTC offset, so a string with no offset is read as UTC.
 */
export function parseApiInstant(iso: string | null | undefined): Date | null {
    if (!iso) return null
    const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso)
    const date = new Date(hasOffset ? iso : `${iso}Z`)
    return Number.isNaN(date.getTime()) ? null : date
}

const parse = parseApiInstant

/** `YYYY-MM-DD` in UTC, or an empty string for a missing or unreadable value. */
export function adminDate(iso: string | null | undefined): string {
    const d = parse(iso)
    return d === null ? "" : `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

/** `YYYY-MM-DD HH:MM UTC`, or an empty string for a missing or unreadable value. */
export function adminDateTime(iso: string | null | undefined): string {
    const d = parse(iso)
    return d === null ? "" : `${adminDate(iso)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
}

const RECORD_TYPES = [
    "species", "species_entry", "conformer_group", "conformer_observation", "reaction", "reaction_entry",
    "transition_state", "transition_state_entry", "calculation", "statmech", "thermo", "kinetics", "transport",
    "network", "network_solve", "applied_energy_correction", "artifact", "molecular_property_observation",
]

export const RECORD_TYPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = RECORD_TYPES.map((value) => ({
    value,
    label: recordTypeGroupLabel(value, 1),
}))
