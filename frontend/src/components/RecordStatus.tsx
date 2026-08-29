import type { ScientificRecordState } from "../hooks/useScientificRecord"

/**
 * Shared non-ready rendering for every `/scientific/*` detail page built
 * on {@link useScientificRecord}. Each of the six non-ready states gets
 * its own title and message — a permanently-wrong reference (`invalid`,
 * HTTP 422) must never read the same as a possibly-transient outage
 * (`unavailable`), a schema mismatch on the archive's own response
 * (`malformed`) must never read the same as "not found" (`missing`), and
 * a *valid* reference the archive declined to fully serve
 * (`unprocessable`, e.g. `geometry_too_large`) must never read the same
 * as `invalid` — the reference is not the thing that's wrong there.
 *
 * `kind` is the record's plain-English name in sentence case, e.g.
 * "conformer observation" — used to build every title and message so
 * a caller supplies it once rather than five near-duplicate strings.
 */
export function RecordStatus({ state, ref, kind, loadingDetail }: {
    state: Exclude<ScientificRecordState<unknown>, { status: "ready" }>
    ref: string
    kind: string
    loadingDetail: string
}) {
    const Kind = kind.charAt(0).toUpperCase() + kind.slice(1)

    if (state.status === "loading") {
        return <Notice title={`Loading ${kind}…`} busy message={loadingDetail} />
    }
    if (state.status === "missing") {
        return (
            <Notice
                title={`${Kind} not found`}
                ref={ref}
                message={`No ${kind} with this stable reference is available in this archive projection.`}
            />
        )
    }
    if (state.status === "invalid") {
        return (
            <Notice
                title={`Not a ${kind} reference`}
                ref={ref}
                alert
                message={state.detail || `This reference does not identify a ${kind}. Retrying will not change that.`}
            />
        )
    }
    if (state.status === "unprocessable") {
        return (
            <Notice
                title={`${Kind} could not be displayed`}
                ref={ref}
                alert
                message={state.detail || `The archive recognised this reference but declined to serve the full ${kind}.`}
            />
        )
    }
    if (state.status === "malformed") {
        return (
            <Notice
                title={`${Kind} data could not be read`}
                ref={ref}
                alert
                message="The archive responded, but this page could not validate the scientific projection."
            />
        )
    }
    return (
        <Notice
            title={`${Kind} unavailable`}
            ref={ref}
            alert
            message="The archive service could not load this projection. Try again later."
        />
    )
}

function Notice({ title, ref, busy, alert, message }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
    message: string
}) {
    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{message}</p>
        </section>
    )
}
