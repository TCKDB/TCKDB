import { z } from "zod"

/**
 * Runtime + static types for the admin curator-task queue.
 *
 * Source of truth: `backend/app/api/routes/admin.py`
 * (`AdminCuratorTaskResponse`, `PaginatedResponse`).
 *
 * A curator task is one machine-review finding that a person should look
 * at. It is **advisory**: it carries no trust, approves nothing, and its
 * workflow state is entirely separate from `record_review` (whether a
 * human has endorsed the science) and from the deterministic evidence
 * layer. Resolving a task says "somebody dealt with this finding", never
 * "this record is good".
 */

export const CuratorTaskStateSchema = z.enum([
    "untriaged",
    "needs_curator_review",
    "in_curator_review",
    "resolved_no_action",
    "resolved_human_reviewed",
    "dismissed_machine_finding",
])
export type CuratorTaskState = z.infer<typeof CuratorTaskStateSchema>

/** The three states a task is still waiting on somebody for. */
export const OPEN_STATES: readonly CuratorTaskState[] = [
    "untriaged",
    "needs_curator_review",
    "in_curator_review",
]

/**
 * The terminal states, mirroring `MachineReviewCuratorTaskState.terminal_states()`.
 * Each says something different about *why* the task is closed, and the UI
 * must not collapse them: "dismissed" means the machine was wrong, while
 * "resolved_human_reviewed" means it was right and a person acted.
 */
export const TERMINAL_STATES: readonly CuratorTaskState[] = [
    "resolved_no_action",
    "resolved_human_reviewed",
    "dismissed_machine_finding",
]

export const CuratorTaskSeveritySchema = z.enum(["info", "warning", "critical"])
export type CuratorTaskSeverity = z.infer<typeof CuratorTaskSeveritySchema>

export const MachineReviewStatusSchema = z.enum([
    "not_run",
    "machine_screened_pass",
    "machine_screened_warning",
    "machine_screened_needs_attention",
    "machine_review_failed",
])
export type MachineReviewStatus = z.infer<typeof MachineReviewStatusSchema>

/**
 * One curator task.
 *
 * `record_public_ref` is the handle the archive is addressed by, resolved
 * at read time by the backend. It is `null` when the record cannot be
 * named -- `applied_energy_correction` has no `public_ref` column, and a
 * record may have been deleted since the task was raised. `record_id` is
 * an internal row id, carried because this surface is admin-only, and is
 * never the thing a curator is asked to act on.
 */
export const CuratorTaskSchema = z.object({
    id: z.number().int(),
    submission_id: z.number().int(),
    record_type: z.string(),
    record_public_ref: z.string().nullable().default(null),
    record_id: z.number().int(),
    finding_fingerprint: z.string(),
    workflow_state: CuratorTaskStateSchema,
    machine_review_status: MachineReviewStatusSchema,
    highest_severity: CuratorTaskSeveritySchema,
    findings_count: z.number().int(),
    source_audit_event_id: z.number().int().nullable().default(null),
    assigned_to: z.number().int().nullable().default(null),
    created_at: z.string(),
    updated_at: z.string(),
    resolved_at: z.string().nullable().default(null),
    resolved_by: z.number().int().nullable().default(null),
    resolution_note: z.string().nullable().default(null),
})
export type CuratorTask = z.infer<typeof CuratorTaskSchema>

export const CuratorTaskPageSchema = z.object({
    items: z.array(CuratorTaskSchema),
    total: z.number().int(),
    skip: z.number().int(),
    limit: z.number().int(),
})
export type CuratorTaskPage = z.infer<typeof CuratorTaskPageSchema>

/** Human wording for a workflow state. */
export function stateLabel(state: CuratorTaskState): string {
    switch (state) {
        case "untriaged":
            return "untriaged"
        case "needs_curator_review":
            return "needs review"
        case "in_curator_review":
            return "in review"
        case "resolved_no_action":
            return "closed: no action"
        case "resolved_human_reviewed":
            return "closed: reviewed"
        case "dismissed_machine_finding":
            return "dismissed"
    }
}

/**
 * What a terminal state asserts, shown where a curator picks one.
 *
 * Spelled out because the three are easy to use interchangeably and mean
 * different things to whoever reads the queue afterwards. In particular
 * `resolved_human_reviewed` records that a human review happened
 * *elsewhere* -- it does not itself write any review state.
 */
export function resolutionMeaning(state: CuratorTaskState): string {
    switch (state) {
        case "resolved_no_action":
            return "The finding was fair, and nothing needs changing."
        case "resolved_human_reviewed":
            return "A person reviewed the record itself. This does not record that review; it notes that it happened."
        case "dismissed_machine_finding":
            return "The machine was wrong. The record is unaffected."
        default:
            return ""
    }
}

export function isOpen(state: CuratorTaskState): boolean {
    return (OPEN_STATES as readonly string[]).includes(state)
}
