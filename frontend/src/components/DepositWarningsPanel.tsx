import { SectionHeading } from "./PageSections"
import { DepositorNote } from "./DepositorNote"
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
    return (
        <li className="deposit-warning">
            <p className="deposit-warning-message">{item.message}</p>
            <p className="note"><span className="data">{item.field}</span></p>
            <p className="note">
                {item.register_entry != null && <>Check register entry {item.register_entry} · </>}
                <span className="data">{item.code}</span>
            </p>
            {item.retired_at != null && (
                <p className="deposit-warning-retired">
                    <span className="value-pill value-pill--muted">Retired</span>
                    <span className="note">
                        {isoDate(item.retired_at)}
                        {item.retired_reason ? `, ${retirementReason(item.retired_reason)}` : ""}
                    </span>
                </p>
            )}
            {asDeposited && <DepositorNote label="As deposited">{item.depositor_message}</DepositorNote>}
            {asDeposited && <DepositorNote label="As deposited, field">{item.depositor_field}</DepositorNote>}
            {supplied && <DepositorNote label="As deposited, value">{supplied}</DepositorNote>}
        </li>
    )
}

/**
 * The deposit warnings of a record page or of one record's block. Renders nothing when no block carries an item.
 * `recordRef` is the page's own record: its block is listed first and labelled "This record"; any other record's block is
 * labelled through `labelForRef` (or its ref). `variant="block"` renders an `<h4>` for use inside a record card, `"subsection"` an `<h3>` registered under the section above.
 */
export function DepositWarningsPanel({ warnings, recordRef, labelForRef, headingId = "deposit-warnings", variant = "section", className }: {
    warnings: DepositWarnings
    recordRef?: string
    labelForRef?: (ref: string) => string
    headingId?: string
    variant?: "section" | "subsection" | "block"
    /** Added beside the panel's own class, for the page's section spacing. */
    className?: string
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
        <section aria-labelledby={headingId} className={className ? `deposit-warnings ${className}` : "deposit-warnings"} data-component="deposit-warnings">
            {heading}
            {[...own, ...others].map((block, index) => (
                <div className="deposit-warnings-block" key={`${block.record_ref}-${index}`}>
                    {recordRef !== undefined && (
                        <p className="deposit-warnings-owner">
                            {block.record_ref === recordRef ? "This record" : (labelForRef?.(block.record_ref) ?? block.record_ref)}
                        </p>
                    )}
                    {block.status === "not_recorded" && (
                        <p className="note">Warnings from the original deposit were not recorded; the items below were added later.</p>
                    )}
                    <ul className="deposit-warnings-list">
                        {block.items.map((item, itemIndex) => <WarningItem key={`${item.code}-${item.field}-${itemIndex}`} item={item} />)}
                    </ul>
                </div>
            ))}
            {deposit.map((block, index) => (
                <div className="deposit-warnings-block" key={`${block.submission_ref ?? "deposit"}-${index}`}>
                    <p className="deposit-warnings-owner">About the deposit as a whole</p>
                    {block.submission_ref && <p className="note">Submission <span className="data">{block.submission_ref}</span></p>}
                    <ul className="deposit-warnings-list">
                        {block.items.map((item, itemIndex) => <WarningItem key={`${item.code}-${item.field}-${itemIndex}`} item={item} />)}
                    </ul>
                </div>
            ))}
        </section>
    )
}
