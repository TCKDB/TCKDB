import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { DepositWarningsPanel } from "./DepositWarningsPanel"
import { useRecordDepositWarnings } from "../hooks/useRecordDepositWarnings"
import type { DepositWarnings, SubmissionWarnings } from "../api/depositWarningsSchema"

export type WarningsLoader = (ref: string, signal: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<DepositWarnings>

type Report = (blocks: SubmissionWarnings[]) => void

const ReportContext = createContext<Report | null>(null)

const blockKey = (block: SubmissionWarnings) =>
    `${block.submission_ref ?? ""}|${block.items.map((item) => `${item.code}:${item.field}:${item.retired_at ?? ""}`).join(",")}`

/**
 * One place for the whole-deposit warnings of a section whose records each read their own warnings. A record's read
 * reports its whole-deposit blocks here once it loads, and the section shows each distinct block once, after its records,
 * instead of repeating it inside every record.
 */
export function DepositWideWarnings({ children, headingId }: { children: ReactNode; headingId: string }) {
    const [blocks, setBlocks] = useState<Map<string, SubmissionWarnings>>(new Map())
    const report = useCallback<Report>((incoming) => {
        setBlocks((current) => {
            const next = new Map(current)
            let changed = false
            for (const block of incoming) {
                if (block.items.length === 0) continue
                const key = blockKey(block)
                if (!next.has(key)) { next.set(key, block); changed = true }
            }
            return changed ? next : current
        })
    }, [])
    return (
        <ReportContext.Provider value={report}>
            {children}
            <DepositWarningsPanel
                warnings={{ record_warnings: [], deposit_warnings: [...blocks.values()] }}
                headingId={headingId}
                variant="subsection"
                className="ledger-section"
            />
        </ReportContext.Provider>
    )
}

/** Announced to screen readers once the panel renders; hidden on screen because the panel heading says the same. */
const LOADED = "Deposit warnings loaded."

function LoadedWarnings({ ref_, load, idBase, recordLabel, onStatus }: {
    ref_: string; load: WarningsLoader; idBase: string; recordLabel?: string; onStatus: (text: string) => void
}) {
    const warnings = useRecordDepositWarnings(ref_, load)
    const report = useContext(ReportContext)
    const hasOwn = warnings !== null && warnings !== "unavailable" && warnings.record_warnings.some((block) => block.items.length > 0)
    useEffect(() => {
        if (warnings && warnings !== "unavailable") report?.(warnings.deposit_warnings)
    }, [warnings, report])
    useEffect(() => {
        if (warnings === null) onStatus("Loading deposit warnings.")
        else if (warnings === "unavailable") onStatus("Deposit warnings could not be loaded for this record.")
        else onStatus(hasOwn ? LOADED : "No deposit warnings are recorded for this record.")
    }, [warnings, hasOwn, onStatus])
    if (warnings === null || warnings === "unavailable") return null
    const own = { record_warnings: warnings.record_warnings, deposit_warnings: [] }
    return <DepositWarningsPanel warnings={own} variant="block" headingId={`deposit-warnings-${idBase}`} recordLabel={recordLabel} />
}

/**
 * A record's deposit warnings, read from its own route only when the reader asks. The list a card comes from carries none,
 * and a read per card on mount would spend the anonymous read budget on records nobody has opened. The status line is
 * mounted from the start so the outcome is announced, and takes focus when the button it replaces goes.
 */
export function RecordWarningsOnRequest({ ref_, load, idBase, recordLabel }: { ref_: string; load: WarningsLoader; idBase: string; recordLabel?: string }) {
    const [requested, setRequested] = useState(false)
    const [status, setStatus] = useState("")
    const statusRef = useRef<HTMLParagraphElement>(null)
    useEffect(() => {
        if (requested) statusRef.current?.focus()
    }, [requested])
    return (
        <div className="record-warnings-request">
            {!requested && (
                <button type="button" onClick={() => { setStatus("Loading deposit warnings."); setRequested(true) }} aria-label={recordLabel ? `Show deposit warnings of ${recordLabel}` : undefined}>
                    Show deposit warnings
                </button>
            )}
            <p className={status === LOADED ? "note record-warnings-status record-warnings-status--loaded" : "note record-warnings-status"} role="status" tabIndex={-1} ref={statusRef}>{status}</p>
            {requested && <LoadedWarnings ref_={ref_} load={load} idBase={idBase} recordLabel={recordLabel} onStatus={setStatus} />}
        </div>
    )
}
