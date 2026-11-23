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

function parse(iso: string | null | undefined): Date | null {
    if (!iso) return null
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? null : d
}

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
