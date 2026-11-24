import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { cancelJob, getJob, jobQueryKey, submissionJobsKey } from "../api/machineReviewJobsApi"
import { fetchMachineReviewInspection, inspectionQueryKey } from "../api/machineReviewInspection"
import {
    elapsedSeconds,
    formatElapsed,
    progressAnnouncement,
    stepDuration,
    stepWords,
} from "../domain/machineReviewJob"
import { recommendationFor, wordLabel, wordsFor } from "../domain/machineReviewWords"
import { useAuth } from "../hooks/useAuth"
import { isOpenStatus, type JobDetail, type JobEvent, type JobStatus } from "../types/machineReviewJob"

const POLL_MS = 2000

/** Shown beside the status word, so the state is never carried by colour alone. */
const STATUS_ICON: Record<JobStatus, string> = {
    queued: "…",
    running: "▶",
    succeeded: "✓",
    failed: "✕",
    cancelled: "–",
}

function useNow(active: boolean): Date {
    const [now, setNow] = useState(() => new Date())
    useEffect(() => {
        if (!active) return
        const timer = window.setInterval(() => setNow(new Date()), 1000)
        return () => window.clearInterval(timer)
    }, [active])
    return now
}

function terminalHeading(status: JobStatus): string {
    if (status === "succeeded") return "Machine review finished"
    if (status === "failed") return "Machine review did not finish"
    return "Machine review cancelled"
}

function terminalText(job: JobDetail): string {
    if (job.status === "succeeded") return "The findings are shown on this page."
    if (job.status === "failed") {
        return job.failure_code === null
            ? "Machine review did not finish."
            : `Machine review did not finish: ${wordLabel("jobFailure", job.failure_code)}.`
    }
    const why = job.failure_code === "machine_review_disabled" ? ` Reason: ${wordLabel("jobFailure", job.failure_code)}.` : ""
    return `No review was recorded.${why}`
}

/**
 * The progress of one machine-review job: its steps, its elapsed time, a way to stop it, and how it ended.
 *
 * Polls every two seconds while the job is queued or running and not at all once it has ended. Each poll asks only for
 * the steps after the last one seen, and they are merged by their number, so a poll can neither repeat nor lose a step.
 * One live region announces the job; its sentence depends on the status and the step only, so it speaks when a step
 * lands and not on every poll. When the job ends the cached inspection and overview are refetched and focus moves to
 * the result heading.
 */
export function MachineReviewJobProgress({
    jobId,
    submissionId,
    onOpenChange,
}: {
    jobId: number
    submissionId: number
    onOpenChange?: (open: boolean) => void
}) {
    const { state } = useAuth()
    const queryClient = useQueryClient()
    const seen = useRef(new Map<number, JobEvent>())

    const query = useQuery({
        queryKey: jobQueryKey(jobId),
        queryFn: async (): Promise<JobDetail> => {
            const after = seen.current.size === 0 ? 0 : Math.max(...seen.current.keys())
            const detail = await getJob(jobId, after)
            for (const event of detail.events) seen.current.set(event.seq, event)
            return { ...detail, events: [...seen.current.values()].sort((a, b) => a.seq - b.seq) }
        },
        refetchInterval: (q) => (q.state.data !== undefined && !isOpenStatus(q.state.data.status) ? false : POLL_MS),
        gcTime: 0,
    })
    const job = query.data
    const open = job !== undefined && isOpenStatus(job.status)
    const now = useNow(open)
    useEffect(() => {
        if (job !== undefined) onOpenChange?.(isOpenStatus(job.status))
    }, [job, onOpenChange])

    const cancel = useMutation({
        mutationFn: () => cancelJob(jobId),
        onSuccess: () => void query.refetch(),
    })

    // The end of this job, seen while the page was watching it: refresh what the result changes and look at it.
    const wasOpen = useRef(false)
    const headingRef = useRef<HTMLHeadingElement>(null)
    const ended = job !== undefined && !isOpenStatus(job.status)
    useEffect(() => {
        if (job === undefined) return
        if (isOpenStatus(job.status)) {
            wasOpen.current = true
            return
        }
        if (!wasOpen.current) return
        wasOpen.current = false
        void queryClient.invalidateQueries({ queryKey: inspectionQueryKey(submissionId) })
        void queryClient.invalidateQueries({ queryKey: ["machine-review-overview"] })
        void queryClient.invalidateQueries({ queryKey: submissionJobsKey(submissionId) })
        headingRef.current?.focus()
    }, [job, queryClient, submissionId])

    // The advice the finished review leaves, read from the refreshed inspection and only once it holds this review.
    const succeeded = job !== undefined && job.status === "succeeded"
    const inspection = useQuery({
        queryKey: inspectionQueryKey(submissionId),
        queryFn: () => fetchMachineReviewInspection(submissionId),
        enabled: succeeded,
    })
    const finishedRun =
        succeeded && !inspection.isFetching && job.audit_event_id !== null
            ? inspection.data?.runs.find((r) => r.audit_event_id === job.audit_event_id)
            : undefined
    const advice = finishedRun?.parsed && finishedRun.status ? recommendationFor(finishedRun.status) : null

    const last = job === undefined || job.events.length === 0 ? null : job.events[job.events.length - 1]
    const current = job === undefined ? "" : progressAnnouncement(job, last)
    // The live region is on the page empty and given its text a moment later, so the first sentence is announced too.
    const [announcement, setAnnouncement] = useState("")
    useEffect(() => {
        const timer = setTimeout(() => setAnnouncement(current), 0)
        return () => clearTimeout(timer)
    }, [current])
    const isRequester = state.status === "signed-in" && job !== undefined && state.user.id === job.requested_by
    const isAdmin = state.status === "signed-in" && state.user.role === "admin"
    const mayCancel = isRequester || isAdmin
    const stoppingId = `cancel-stopping-${jobId}`
    const cancelDescribedBy =
        [!mayCancel ? "cancel-reason" : null, job?.cancel_requested ? stoppingId : null].filter(Boolean).join(" ") || undefined
    const seconds = job === undefined ? null : elapsedSeconds(job, now)

    return (
        <div className="machine-review-progress">
            <div role="status" aria-live="polite" aria-label="machine review progress" className="admin-visually-hidden">
                {announcement}
            </div>

            {query.isError && job === undefined && (
                <p className="auth-error" role="alert">The progress of the review could not be read.</p>
            )}
            {query.isLoading && <p>Reading the progress of the review…</p>}

            {job !== undefined && (
                <>
                    <p>
                        <span aria-hidden="true">{STATUS_ICON[job.status]} </span>
                        <strong>{wordLabel("jobStatus", job.status)}</strong>
                        {wordsFor("providerKey", job.provider_key).known && <> · {wordLabel("providerKey", job.provider_key)}</>}
                        {seconds !== null && <> · {open ? "elapsed" : "took"} {formatElapsed(seconds)}</>}
                    </p>

                    {job.events.length > 0 && (
                        <ol className="machine-review-steps" aria-label="steps of the review">
                            {job.events.map((event) => {
                                const duration = stepDuration(event)
                                return (
                                    <li key={event.seq}>
                                        Step {event.seq}: {stepWords(event)}
                                        {duration !== null && <> ({duration})</>}
                                    </li>
                                )
                            })}
                        </ol>
                    )}

                    {open && (
                        <>
                            {job.cancel_requested && <p id={stoppingId}>Stopping after this step.</p>}
                            <button
                                type="button"
                                aria-disabled={!mayCancel || job.cancel_requested || cancel.isPending ? "true" : undefined}
                                aria-describedby={cancelDescribedBy}
                                onClick={() => {
                                    if (mayCancel && !job.cancel_requested && !cancel.isPending) cancel.mutate()
                                }}
                            >
                                Cancel this review
                            </button>
                            {!mayCancel && (
                                <p id="cancel-reason" className="admin-hint">
                                    Only the person who requested this review, or an administrator, can cancel it.
                                </p>
                            )}
                            {cancel.isError && (
                                <p className="auth-error" role="alert">The review could not be cancelled.</p>
                            )}
                        </>
                    )}

                    {ended && (
                        <div className="machine-review-result">
                            <h3 ref={headingRef} tabIndex={-1}>{terminalHeading(job.status)}</h3>
                            {advice !== null && <p><strong>{advice}</strong></p>}
                            <p>{terminalText(job)}</p>
                        </div>
                    )}
                </>
            )}
        </div>
    )
}
