import { parseApiInstant, wordLabel } from "./machineReviewWords"
import { isOpenStatus, type Job, type JobEvent } from "../types/machineReviewJob"

/**
 * Wording and timing for a machine-review job's progress.
 */

/** Whole seconds from the job's start (its request while queued) to `now`, never negative. */
export function elapsedSeconds(job: Pick<Job, "created_at" | "started_at" | "finished_at">, now: Date): number | null {
    const from = parseApiInstant(job.started_at) ?? parseApiInstant(job.created_at)
    if (from === null) return null
    const to = parseApiInstant(job.finished_at) ?? now
    return Math.max(0, Math.floor((to.getTime() - from.getTime()) / 1000))
}

export function formatElapsed(seconds: number): string {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return m === 0 ? `${s} s` : `${m} min ${s} s`
}

/** What one progress entry says, in words. A lookup names its tool. */
export function stepWords(event: JobEvent): string {
    const base = wordLabel("jobEventKind", event.kind)
    const tool = event.tool_name !== null && (event.kind === "tool_call" || event.kind === "tool_result")
    return tool ? `${base} (${event.tool_name})` : base
}

/** The duration shown beside a step, only when the server recorded one. */
export function stepDuration(event: JobEvent): string | null {
    if (event.duration_ms === null) return null
    return event.duration_ms < 1000 ? `${event.duration_ms} ms` : `${(event.duration_ms / 1000).toFixed(1)} s`
}

/**
 * The sentence the progress region announces. It is a function of the status and the last step seen only,
 * so it changes when a step lands and not on every poll.
 */
export function progressAnnouncement(
    job: Pick<Job, "status" | "step_count" | "cancel_requested"> & { failure_code?: string | null },
    last: JobEvent | null,
): string {
    if (job.status === "queued") return "Machine review is queued."
    if (job.status === "running") {
        const stopping = job.cancel_requested ? " Stopping after this step." : ""
        if (last === null) return `Machine review is running.${stopping}`
        return `Machine review is running. Step ${last.seq}: ${stepWords(last)}.${stopping}`
    }
    const base = `Machine review ${wordLabel("jobStatus", job.status)}.`
    return job.status === "failed" && job.failure_code ? `${base} ${capitalise(wordLabel("jobFailure", job.failure_code))}.` : base
}

function capitalise(text: string): string {
    return text.charAt(0).toUpperCase() + text.slice(1)
}

export { isOpenStatus, parseApiInstant }
