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
 *
 * ## How strict each field is, and why it differs
 *
 * Strictness here is not a style choice: it decides what happens the day
 * the backend grows an enum member this build has never heard of. A strict
 * `z.enum` inside `z.array(...)` fails the WHOLE page, so one unfamiliar
 * row takes every other row down with it -- and the queue's entire job is
 * to show a curator the work that is outstanding.
 *
 * - `workflow_state` stays a strict enum. The page branches on it (open
 *   versus terminal decides which actions a row offers), so a value it
 *   cannot classify is genuinely unusable, not merely unfamiliar.
 * - `highest_severity` and `machine_review_status` are plain strings. The
 *   first is displayed and the second is not displayed at all; neither
 *   drives a decision, so an unknown token can be shown as-is rather than
 *   discarding the row. `severityClass` handles the styling side.
 * - `record_type` was already a plain string, for the same reason.
 *
 * The array is then parsed row by row (see `curatorTasksApi.ts`) so a row
 * that fails even this is reported and skipped, not fatal to the page.
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

/** The severities the backend defines today (`MachineReviewSeverity`). */
export const KNOWN_SEVERITIES = ["info", "warning", "critical"] as const
export type KnownSeverity = (typeof KNOWN_SEVERITIES)[number]

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
    machine_review_status: z.string(),
    highest_severity: z.string(),
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

/**
 * The paginated envelope, with `items` left unparsed on purpose.
 *
 * Each row is validated separately by `listCuratorTasks` so that one row
 * the frontend cannot read costs that row and not the queue.
 */
export const CuratorTaskEnvelopeSchema = z.object({
    items: z.array(z.unknown()),
    total: z.number().int(),
    skip: z.number().int(),
    limit: z.number().int(),
})

/** A page of tasks, plus how many rows on it could not be read. */
export type CuratorTaskPage = {
    items: CuratorTask[]
    total: number
    skip: number
    limit: number
    unreadable: number
}

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
        // "elsewhere" is load-bearing. This state records that a human
        // review of the record happened somewhere else; it writes no
        // review of its own. Without that word the label reads, in a
        // table beside a record ref, as though this queue reviewed it.
        case "resolved_human_reviewed":
            return "closed: reviewed elsewhere"
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
            // Spec section 7 calls this "false positive or not actionable".
            // "The machine was wrong" alone loses the second half, which is
            // the commoner case: the finding was right and still not worth
            // acting on.
            return "The finding was a false positive, or not actionable. The record is unaffected."
        default:
            return ""
    }
}

export function isOpen(state: CuratorTaskState): boolean {
    return (OPEN_STATES as readonly string[]).includes(state)
}

/**
 * The CSS class for a severity, or null when the token is unfamiliar.
 *
 * Returning null rather than guessing a class keeps an unknown severity
 * visible and unstyled instead of silently painted as one of the three
 * this build happens to know.
 */
export function severityClass(severity: string): string | null {
    return (KNOWN_SEVERITIES as readonly string[]).includes(severity)
        ? `severity-${severity}`
        : null
}

/**
 * The result of an explicit build-for-submission run.
 *
 * Source of truth: `AdminCuratorTaskBuildResponse` in
 * `backend/app/api/routes/admin.py`, mirroring `CuratorTaskBuildResult` in
 * `backend/app/services/machine_review/curator_tasks.py`.
 *
 * ## `task_ids` is deliberately absent
 *
 * The response carries one and this schema drops it. zod strips keys it
 * does not declare, so the ids never reach the object the page renders
 * from: they cannot be printed by accident, and no later edit to the page
 * can print them without coming back here first. They are internal row
 * ids, which DR-0028 Req 2 keeps out of user-facing output. Nothing is
 * lost -- `created_count + reused_count` is the same number, and a task is
 * reached through the queue, never by typing its id.
 *
 * ## The counts are not all disjoint
 *
 * `refreshed_count` is a SUB-count of `reused_count`: an open task whose
 * snapshot was updated in place is counted in both. Listing the six as
 * siblings would total more findings than were considered, which is why
 * the page nests it rather than giving it a line of its own.
 */
export const CuratorTaskBuildResultSchema = z.object({
    created_count: z.number().int(),
    reused_count: z.number().int(),
    refreshed_count: z.number().int(),
    skipped_info_count: z.number().int(),
    skipped_unmapped_count: z.number().int(),
    skipped_terminal_count: z.number().int(),
    warnings: z.array(z.string()),
})
export type CuratorTaskBuildResult = z.infer<typeof CuratorTaskBuildResultSchema>

/**
 * The warning/critical findings the run actually weighed for a task.
 *
 * Disjoint by construction (see `CuratorTaskBuildResult` above): each such
 * finding lands in exactly one of created / reused / skipped_terminal.
 * Zero here means the submission had no warning or critical finding mapped
 * to a record at all -- which is a different sentence from "they all had
 * tasks already", and the page must not print one for the other.
 */
export function findingsConsidered(result: CuratorTaskBuildResult): number {
    return result.created_count + result.reused_count + result.skipped_terminal_count
}
