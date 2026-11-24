import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { listProviders, listSubmissionJobs, requestMachineReview, submissionJobsKey } from "../api/machineReviewJobsApi"
import { blockingAvailability, okProviders } from "../domain/machineReviewProviders"
import { OUTSIDE_PROVIDERS, wordLabel, wordsFor } from "../domain/machineReviewWords"
import { isOpenStatus } from "../types/machineReviewJob"
import { MachineReviewJobProgress } from "./MachineReviewJobProgress"

/**
 * Ask for a machine review of one submission and watch it.
 *
 * The reviewers on offer come from the server, each with whether this caller may use it now. The reviewer is chosen in
 * a list only when more than one is available. With none available the button stays focusable and says why, so the
 * reason can be read. A request while a review is already running attaches to that review. Opening the page while one
 * is queued or running shows its progress straight away.
 */
export function RequestMachineReview({ submissionId }: { submissionId: number }) {
    const providers = useQuery({ queryKey: ["machine-review-providers"], queryFn: listProviders })
    const jobs = useQuery({ queryKey: submissionJobsKey(submissionId), queryFn: () => listSubmissionJobs(submissionId) })
    const [watching, setWatching] = useState<number | null>(null)
    const [chosen, setChosen] = useState<string | null>(null)
    const [attachedNote, setAttachedNote] = useState(false)
    const [watchedOpen, setWatchedOpen] = useState(false)

    const openJob = jobs.data?.find((j) => isOpenStatus(j.status))
    // A review found already open is watched from then on, so its ending stays on the page after the list refetches.
    if (watching === null && openJob !== undefined) {
        setWatching(openJob.id)
        setWatchedOpen(true)
    }
    const jobId = watching ?? openJob?.id ?? null

    const list = providers.data ?? []
    const ok = okProviders(list)
    const provider = ok.find((p) => p.provider_key === chosen) ?? ok[0]
    const blocked = providers.data === undefined ? null : blockingAvailability(list)

    const request = useMutation({
        mutationFn: (key: string) => requestMachineReview(submissionId, key),
        onSuccess: (outcome) => {
            setWatching(outcome.job.id)
            setWatchedOpen(true)
            setAttachedNote(outcome.kind === "attached")
            void jobs.refetch()
        },
    })

    const running = watching !== null ? watchedOpen : openJob !== undefined
    let reason: string | null = null
    if (providers.isError) reason = "The reviewers on offer could not be read."
    else if (providers.data === undefined) reason = "Checking which reviewers are available…"
    else if (blocked !== null) reason = wordsFor("availability", blocked).label
    const disabled = reason !== null || request.isPending
    const reasonId = `request-reason-${submissionId}`

    return (
        <div className="machine-review-request">
            {list.length > 1 && ok.length > 1 && (
                <p>
                    <label htmlFor={`reviewer-${submissionId}`}>Reviewer</label>{" "}
                    <select
                        id={`reviewer-${submissionId}`}
                        className="admin-role-select"
                        value={provider?.provider_key ?? ""}
                        onChange={(e) => setChosen(e.target.value)}
                    >
                        {list.map((p) => {
                            const words = wordsFor("availability", p.availability)
                            const name = wordLabel("providerKey", p.provider_key)
                            return (
                                <option key={p.provider_key} value={p.provider_key} disabled={p.availability !== "ok"}>
                                    {p.availability === "ok" ? name : `${name} (${words.label})`}
                                </option>
                            )
                        })}
                    </select>
                </p>
            )}
            {provider !== undefined && ok.length === 1 && (
                <p className="admin-hint">Reviewer: {wordLabel("providerKey", provider.provider_key)}</p>
            )}
            {provider !== undefined && OUTSIDE_PROVIDERS.has(provider.provider_key) && (
                <p className="admin-hint">This review sends the submission to an outside service.</p>
            )}

            <button
                type="button"
                aria-disabled={disabled || running ? "true" : undefined}
                aria-describedby={reason !== null || running ? reasonId : undefined}
                onClick={() => {
                    if (disabled || running || provider === undefined) return
                    request.mutate(provider.provider_key)
                }}
            >
                {request.isPending ? "Requesting the review…" : "Request machine review"}
            </button>
            {(reason !== null || running) && (
                <p id={reasonId} className="admin-hint">
                    {running ? "A review of this submission is already queued or running." : reason}
                </p>
            )}
            {request.isError && (
                <p className="auth-error" role="alert">
                    The review could not be requested{request.error instanceof Error ? `: ${request.error.message}` : "."}
                </p>
            )}
            {attachedNote && <p className="admin-hint">A review was already running, so this page is showing that one.</p>}
            {jobId !== null && <MachineReviewJobProgress key={jobId} jobId={jobId} submissionId={submissionId} onOpenChange={setWatchedOpen} />}
        </div>
    )
}
