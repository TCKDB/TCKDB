import "../record-blocks.css"
import { Term } from "./Term"

const KNOWN: Readonly<Record<string, string>> = {
    not_reviewed: "not reviewed",
    under_review: "under review",
    approved: "approved",
    rejected: "rejected",
    deprecated: "deprecated",
}

/** The status as a plain word; a status this build does not know is still shown, spelled out. */
function reviewWord(status: string): string {
    return KNOWN[status] ?? status.replaceAll("_", " ")
}

/**
 * Review status as a word beside a mark: filled for approved, outline for every other status.
 * The word always carries the meaning; the mark is decoration.
 */
export function ReviewChip({ status, explain = true }: { status: string; explain?: boolean }) {
    const tone = status === "approved" ? "approved" : status === "under_review" ? "pending" : "plain"
    return (
        <span className="review-chip-wrap">
            <span className={`review-chip review-chip--${tone}`} data-status={status}>
                <svg className="review-chip-mark" viewBox="0 0 10 10" width="10" height="10" aria-hidden="true">
                    <circle cx="5" cy="5" r="3.6" fill={status === "approved" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" />
                </svg>
                {reviewWord(status)}
            </span>
            {explain && <Term term="review-status">What review status means</Term>}
        </span>
    )
}
