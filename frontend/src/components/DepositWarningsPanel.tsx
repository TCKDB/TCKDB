import type { ReactNode } from "react"
import { SectionHeading } from "./PageSections"
import { DepositorNote } from "./DepositorNote"
import { RecordLink } from "./RecordLink"
import { usedByWords } from "../domain/recordWords"
import { hasNoWarningItems, type DepositWarningItem, type DepositWarnings } from "../api/depositWarningsSchema"
import { tokenWords } from "../domain/apiVocabulary"
import "../deposit-warnings.css"

// The API sends no severity on a warning, so nothing here is grouped or ordered by one: a record's items appear in the
// order served. `message` and `field` are TCKDB's words, built from the code and the values. Text the depositor typed
// (`depositor_message`, `depositor_field`, `context.depositor_supplied`) is shown only inside an "As deposited" note, and
// only when the server served `depositor_message` or `depositor_field`, which it does for the deposit's owner and for
// curators alone. For any other reader no typed string reaches the page.

const isoDate = (value: string) => value.slice(0, 10)

/** Why a warning was retired, in words, for the closed set the server issues. An unknown reason shows as words. */
const RETIREMENT_REASONS: Record<string, string> = {
    artifact_attached: "an artifact was attached after the deposit",
    single_point_deposited: "a single point calculation was deposited later",
    hessian_stored: "a Hessian was stored later",
    superseded_by_recheck: "a later re-check superseded it",
}

function retirementReason(reason: string): string {
    return Object.hasOwn(RETIREMENT_REASONS, reason) ? RETIREMENT_REASONS[reason] : tokenWords(reason)
}

function suppliedText(item: DepositWarningItem): string | null {
    const supplied = item.context?.depositor_supplied
    return typeof supplied === "string" && supplied !== "" ? supplied : null
}

function WarningItem({ item }: { item: DepositWarningItem }) {
    const asDeposited = item.depositor_message != null || item.depositor_field != null
    const supplied = asDeposited ? suppliedText(item) : null
    const retired = item.retired_at != null
    const deposited = [
        { label: "Warning", text: item.depositor_message },
        { label: "Field", text: item.depositor_field },
        { label: "Value", text: supplied },
    ].filter((line): line is { label: string; text: string } => Boolean(line.text))
    return (
        <li className={retired ? "deposit-warning deposit-warning--retired" : "deposit-warning"}>
            <p className="deposit-warning-message">{item.message}</p>
            <p className="note">Where in the deposit: <span className="data">{item.field}</span></p>
            <p className="note">
                Check code: <span className="data">{item.code}</span>
                {item.register_entry != null && ` · Register entry ${item.register_entry}`}
            </p>
            {retired && (
                <p className="deposit-warning-retired">
                    <span className="value-pill value-pill--muted">Retired</span>
                    <span className="note">
                        {isoDate(item.retired_at as string)}
                        {item.retired_reason ? `, ${retirementReason(item.retired_reason)}` : ""}
                    </span>
                </p>
            )}
            {deposited.length > 0 && (
                <DepositorNote
                    label="As deposited"
                    inline
                    lines={deposited}
                    origin="The warning text as stored when the record was deposited. It can quote what the depositor typed. TCKDB shows it as stored and has not checked it."
                />
            )}
        </li>
    )
}

/** The words for a block that belongs to another record: a link where the record has a page, never its ref. */
function defaultOwnerLabel(ref: string, index: number, count: number): ReactNode {
    if (ref.startsWith("calc_")) {
        return <RecordLink kind="calculation" to={`/calculations/${encodeURIComponent(ref)}`} label={usedByWords("Another calculation", index, count)} />
    }
    if (ref.startsWith("tse_")) {
        return <RecordLink kind="transition-state" to={`/transition-state-entries/${encodeURIComponent(ref)}`} label={usedByWords("Another transition state entry", index, count)} />
    }
    return usedByWords("Another record", index, count)
}

/** Active items first; a retired item follows them, in the order served. */
const activeFirst = (items: DepositWarningItem[]): DepositWarningItem[] => [
    ...items.filter((item) => item.retired_at == null),
    ...items.filter((item) => item.retired_at != null),
]

/**
 * The deposit warnings of a record page or of one record's block. Renders nothing when no block carries an item.
 * `recordRef` is the page's own record: its block is listed first and labelled "This record"; any other record's block is
 * labelled through `labelForRef` (or in words, never by its ref). `variant="block"` renders an `<h4>` for use inside a record card, `"subsection"` an `<h3>` registered under the section above.
 */
export function DepositWarningsPanel({ warnings, recordRef, labelForRef, headingId = "deposit-warnings", variant = "section", className, recordLabel }: {
    warnings: DepositWarnings
    recordRef?: string
    labelForRef?: (ref: string, index: number, count: number) => ReactNode
    headingId?: string
    variant?: "section" | "subsection" | "block"
    /** Added beside the panel's own class, for the page's section spacing. */
    className?: string
    /** The record a block inside a card belongs to, for its accessible name. */
    recordLabel?: string
}) {
    if (hasNoWarningItems(warnings)) return null
    const records = warnings.record_warnings.filter((block) => block.items.length > 0)
    const own = records.filter((block) => block.record_ref === recordRef)
    const others = records.filter((block) => block.record_ref !== recordRef)
    const deposit = warnings.deposit_warnings.filter((block) => block.items.length > 0)
    const heading = variant === "block"
        ? <h4 className="model-block-heading" id={headingId}>Deposit warnings</h4>
        : <SectionHeading id={headingId} subsection={variant === "subsection"}>Deposit warnings</SectionHeading>
    return (
        <section aria-labelledby={recordLabel ? undefined : headingId} aria-label={recordLabel ? `Deposit warnings of ${recordLabel}` : undefined} className={className ? `deposit-warnings ${className}` : "deposit-warnings"} data-component="deposit-warnings">
            {heading}
            {[...own, ...others].map((block, index) => (
                <div className="deposit-warnings-block" key={`${block.record_ref}-${index}`}>
                    {recordRef !== undefined && (
                        <p className="deposit-warnings-owner">
                            {block.record_ref === recordRef ? "This record" : (labelForRef ?? defaultOwnerLabel)(block.record_ref, others.indexOf(block), others.length)}
                        </p>
                    )}
                    {block.status === "not_recorded" && (
                        <p className="note">Warnings from the original deposit were not recorded; the items below were added later.</p>
                    )}
                    <ul className="deposit-warnings-list">
                        {activeFirst(block.items).map((item, itemIndex) => <WarningItem key={`${item.code}-${item.field}-${itemIndex}`} item={item} />)}
                    </ul>
                </div>
            ))}
            {deposit.map((block, index) => (
                <div className="deposit-warnings-block" key={`${block.submission_ref ?? "deposit"}-${index}`}>
                    <p className="deposit-warnings-owner">About the deposit as a whole</p>
                    {block.submission_ref && <p className="note">Submission <span className="data">{block.submission_ref}</span></p>}
                    <ul className="deposit-warnings-list">
                        {activeFirst(block.items).map((item, itemIndex) => <WarningItem key={`${item.code}-${item.field}-${itemIndex}`} item={item} />)}
                    </ul>
                </div>
            ))}
        </section>
    )
}
