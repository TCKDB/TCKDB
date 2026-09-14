import { useCallback, useEffect, useState } from "react"
import type { FormEvent } from "react"
import { AuthApiError } from "../api/authApi"
import { clearStorageCapacity, getStorageCapacity } from "../api/adminApi"
import { formatBytes } from "../domain/storageFormat"
import type { StorageCapacityState } from "../types/admin"

type PanelState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; capacity: StorageCapacityState }

/**
 * Artifact-storage capacity, for the admin page.
 *
 * Reports the head of an append-only log, not a probe run just now, and
 * the wording keeps that distinction: when nothing is outstanding this
 * says "no refusal recorded", never "checked just now" or "healthy". The
 * endpoint has no timestamp to offer in that case, and inventing a
 * freshness claim the archive never made is exactly the failure this
 * whole surface is careful about elsewhere.
 *
 * Clearing is an ASSERTION, not a measurement -- it is the path of last
 * resort for a refusal no free-space report can see (a size never known,
 * a bucket-quota refusal). That is why the server requires a reason and
 * why this panel says, in the form, what happens if the operator is
 * wrong: the next refused upload appends a new refusal and `/status`
 * degrades again. Someone about to assert something should be able to
 * read the consequence of asserting it wrongly.
 */
export function StorageCapacityPanel() {
    const [state, setState] = useState<PanelState>({ status: "loading" })
    const [reason, setReason] = useState("")
    const [clearing, setClearing] = useState(false)
    const [clearError, setClearError] = useState<string | null>(null)

    const load = useCallback(async () => {
        setState({ status: "loading" })
        try {
            setState({ status: "ready", capacity: await getStorageCapacity() })
        } catch (caught) {
            setState({
                status: "error",
                message: caught instanceof AuthApiError ? caught.message : "Could not read storage capacity.",
            })
        }
    }, [])

    useEffect(() => { void load() }, [load])

    async function handleClear(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const trimmed = reason.trim()
        // The server enforces this too (min_length=1). Checking here as well
        // is not belt-and-braces for its own sake: a 422 for an empty box is
        // a worse way to learn the box is required than the box saying so.
        if (trimmed === "") {
            setClearError("Give a reason -- it is what the log records.")
            return
        }
        setClearError(null)
        setClearing(true)
        try {
            setState({ status: "ready", capacity: await clearStorageCapacity(trimmed) })
            setReason("")
        } catch (caught) {
            setClearError(caught instanceof AuthApiError ? caught.message : "Could not record that.")
        } finally {
            setClearing(false)
        }
    }

    return (
        <section className="admin-storage" aria-labelledby="storage-heading">
            <h2 id="storage-heading">Artifact storage</h2>

            {state.status === "loading" && <p>Reading storage capacity&hellip;</p>}
            {state.status === "error" && <p className="auth-error" role="alert">{state.message}</p>}

            {state.status === "ready" && !state.capacity.storage_full && (
                <p className="admin-storage-ok" role="status">
                    No storage-full refusal recorded. This is the archive&rsquo;s log of
                    refusals, not a disk check &mdash; it says nothing was refused for
                    want of room, not that there is room.
                </p>
            )}

            {state.status === "ready" && state.capacity.storage_full && (
                <>
                    <p className="auth-error" role="alert">
                        The object store refused a write for want of room. Uploads that
                        carry artifacts will keep failing until there is space.
                    </p>
                    {/* Each pair is wrapped in a <div>, which is the usage
                        contract `.kv-list` relies on and does not state: it is a
                        grid of `auto-fit` columns, so a bare <dt>/<dd> are two
                        SEPARATE grid items and flow independently. Without the
                        wrapper this rendered "Recorded" above `QuotaExceeded` and
                        "Store said" above "5.0 GiB" -- every label beside the
                        wrong value. Every other caller in the app wraps; see
                        `CalculationDetailPage.tsx:830`. */}
                    <dl className="kv-list admin-storage-facts">
                        {state.capacity.storage_full_observed_at && (
                            <div>
                                <dt>Recorded</dt>
                                <dd>{state.capacity.storage_full_observed_at.replace("T", " ").slice(0, 19)}</dd>
                            </div>
                        )}
                        {state.capacity.s3_code && (
                            <div>
                                <dt>Store said</dt>
                                <dd><code>{state.capacity.s3_code}</code></dd>
                            </div>
                        )}
                        {state.capacity.refused_bytes !== null && (
                            <div>
                                <dt>Refused upload</dt>
                                <dd>{formatBytes(state.capacity.refused_bytes)}</dd>
                            </div>
                        )}
                    </dl>

                    <form className="admin-storage-clear" onSubmit={handleClear}>
                        <p className="admin-storage-note">
                            Once there is room again, record that here. This appends to the
                            log; it does not edit the refusal. If the store is still full,
                            the next refused upload records a new refusal and the status
                            goes back to degraded.
                        </p>
                        <div className="auth-field">
                            <label htmlFor="storage-clear-reason">What changed?</label>
                            <input
                                id="storage-clear-reason"
                                value={reason}
                                onChange={(event) => setReason(event.target.value)}
                                placeholder="e.g. pruned 40 GiB of superseded release bundles"
                            />
                            <p className="auth-field-hint">
                                Required. This rests on your word rather than a measurement,
                                so the log records who said what.
                            </p>
                        </div>
                        {clearError && <p className="auth-error" role="alert">{clearError}</p>}
                        <button type="submit" className="auth-submit" disabled={clearing}>
                            Record as resolved
                        </button>
                    </form>
                </>
            )}
        </section>
    )
}
