import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"
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
                className="ledger-section"
            />
        </ReportContext.Provider>
    )
}

function LoadedWarnings({ ref_, load, idBase, recordLabel }: { ref_: string; load: WarningsLoader; idBase: string; recordLabel?: string }) {
    const warnings = useRecordDepositWarnings(ref_, load)
    const report = useContext(ReportContext)
    useEffect(() => {
        if (warnings && warnings !== "unavailable") report?.(warnings.deposit_warnings)
    }, [warnings, report])
    if (warnings === null) return <p className="note" role="status">Loading deposit warnings.</p>
    if (warnings === "unavailable") return <p className="note">Deposit warnings could not be loaded for this record.</p>
    const own = { record_warnings: warnings.record_warnings, deposit_warnings: [] }
    return (
        <>
            <DepositWarningsPanel warnings={own} variant="block" headingId={`deposit-warnings-${idBase}`} recordLabel={recordLabel} />
            {own.record_warnings.every((block) => block.items.length === 0) && <p className="note">No deposit warnings are recorded for this record.</p>}
        </>
    )
}

/**
 * A record's deposit warnings, read from its own route only when the reader asks. The list a card comes from carries none,
 * and a read per card on mount would spend the anonymous read budget on records nobody has opened.
 */
export function RecordWarningsOnRequest({ ref_, load, idBase, recordLabel }: { ref_: string; load: WarningsLoader; idBase: string; recordLabel?: string }) {
    const [requested, setRequested] = useState(false)
    if (requested) return <LoadedWarnings ref_={ref_} load={load} idBase={idBase} recordLabel={recordLabel} />
    return (
        <p className="record-warnings-request">
            <button type="button" onClick={() => setRequested(true)} aria-label={recordLabel ? `Show deposit warnings of ${recordLabel}` : undefined}>
                Show deposit warnings
            </button>
        </p>
    )
}
