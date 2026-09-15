import { z } from "zod"
import {
    MACHINE_REVIEW_STATUSES,
    type MachineReviewStatus,
} from "./machineReviewInspection"

/**
 * Runtime + static types for ONE explicitly triggered machine-review run.
 *
 * Source of truth: `POST /api/v1/admin/machine-review/run-for-submission/
 * {submission_id}` in `backend/app/api/routes/admin.py`.
 *
 * A machine review is **advisory**. It endorses nothing, and it is neither
 * of the other two axes it is easy to confuse it with: `record_review` is
 * whether a person has endorsed the science, and `submission.status` is
 * moderation of the deposit. A run writes machine-review findings and an
 * audit event, and moves neither of the other two.
 *
 * Nothing starts a review on upload (the machine-review stack "is not wired
 * into uploads or any public read"), which is why this route exists at all:
 * before it, a deployment could hold any number of submissions and zero
 * machine reviews, and the inspection page below had nothing to project.
 *
 * ## Why `status` is a plain string, and NOT a `z.enum`
 *
 * `machineReviewInspection.ts` declares the same tokens as a strict
 * `z.enum`, so copying that here would look like the consistent choice. It
 * is the wrong one, for a reason specific to how this value is used.
 *
 * Strictness decides what happens the day the backend grows a status this
 * build has never heard of. Here the reply is a SINGLE object, not a row in
 * a list: a strict enum that rejects an unfamiliar token throws the whole
 * result away, and the page then has to say "the review ran but I could not
 * read the reply" about a reply whose other seven fields were perfectly
 * readable -- `findings_count`, `summary`, `model`, `failure_reason` and the
 * rest, every one of which the admin came here for.
 *
 * What makes that cost unnecessary is the shape of the one branch the page
 * takes on this field. It is a POSITIVE test for one known token, `failed`
 * versus everything else, not a partition of a closed set. An unfamiliar
 * token is honestly "not `machine_review_failed`", so the branch stays
 * correct without the enum; the token is then displayed as it arrived.
 *
 * Contrast `curatorTask.ts`, which keeps `workflow_state` strict on the
 * opposite reasoning: there the branch is open-versus-terminal, a partition
 * of a closed set, and an unfamiliar member belongs to neither side, so the
 * page genuinely cannot classify it. This one it can.
 *
 * Display strictness is kept where it belongs, at the point of display:
 * `knownRunStatus` returns the enum member or `null`, so a familiar token
 * gets the badge and its disclaimer and an unfamiliar one is shown verbatim
 * rather than silently painted as one of the five this build knows. That is
 * the same shape as `severityClass` in `curatorTask.ts`.
 *
 * ## No row id is declared, because the contract carries none
 *
 * `audit_event_recorded` is a BOOLEAN: the contract says *whether* a run
 * was written to the audit log, never *which* row it became. Nothing here
 * may grow an id field (DR-0028 Req 2), and zod strips undeclared keys, so
 * an id the server starts sending cannot reach the page by accident.
 */

/** The two status tokens the page branches on. */
export const MACHINE_REVIEW_RUN_FAILED = "machine_review_failed"
export const MACHINE_REVIEW_NOT_RUN = "not_run"

export const MachineReviewRunResultSchema = z.object({
    submission_id: z.number().int(),
    status: z.string(),
    findings_count: z.number().int(),
    summary: z.string().nullable(),
    model: z.string().nullable(),
    provider: z.string().nullable(),
    audit_event_recorded: z.boolean(),
    /** Non-null only when `status` is `machine_review_failed`. */
    failure_reason: z.string().nullable(),
})
export type MachineReviewRunResult = z.infer<typeof MachineReviewRunResultSchema>

/**
 * Did the reviewer fail?
 *
 * This is a statement about the REVIEWER, not about the submission and not
 * about the request. The POST succeeded; a review ran; its outcome was a
 * failure, which the archive records like any other outcome.
 */
export function reviewFailed(result: MachineReviewRunResult): boolean {
    return result.status === MACHINE_REVIEW_RUN_FAILED
}

/**
 * Did no review happen at all?
 *
 * `not_run` is the one outcome with nothing behind it. The route's service
 * says so in as many words: in off mode the disabled provider "returns
 * `not_run` without calling anything", and no audit event is written,
 * because the absence of an event already means exactly that.
 *
 * It needs its own branch because the alternative is the page reporting
 * "the review ran and recorded 0 findings" about a deployment where no
 * provider was asked anything. Zero findings from a reviewer that looked
 * and zero findings from a reviewer that is switched off are the same
 * number and opposite news, and an admin acts differently on each.
 */
export function reviewDidNotStart(result: MachineReviewRunResult): boolean {
    return result.status === MACHINE_REVIEW_NOT_RUN
}

/**
 * The status as an enum member, or `null` when this build does not know it.
 *
 * Returning `null` rather than guessing keeps an unfamiliar token visible
 * and unstyled instead of wearing another status's badge and, worse, that
 * badge's disclaimer.
 */
export function knownRunStatus(status: string): MachineReviewStatus | null {
    return (MACHINE_REVIEW_STATUSES as readonly string[]).includes(status)
        ? (status as MachineReviewStatus)
        : null
}
