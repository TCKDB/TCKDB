import { SectionHeading } from "./PageSections"
import { Disclosure } from "./Disclosure"
import { MadeUnderLine } from "./MadeUnderLine"
import type { TrustFragment } from "../api/trustSchema"
import { checkAsks, meaningOf, tokenWords } from "../domain/apiVocabulary"
import { liveMadeUnder } from "../domain/madeUnder"
import { reviewPillClass } from "../domain/reviewPillFormat"
import "../trust-assessment.css"

// The trust verdict of a record, worded from the API vocabulary guide (`domain/apiVocabulary`): the meaning of each token and
// the rubric's own sentence for each check. A token the guide does not define shows as words with no meaning, never an
// invented one. The trust badge is completeness, not quality, and nothing here ranks a record. `evidence.record_id` and
// `is_certified` are never rendered.

type Evidence = TrustFragment["evidence"]

function outcomePill(outcome: string) {
    if (outcome === "passed") return <span className="value-pill">passed</span>
    if (outcome === "warning") return <span className="value-pill">warning, needs investigation</span>
    return <span className="value-pill value-pill--muted">{tokenWords(outcome)}</span>
}

/** The first thing a hard-failed record shows: the discrete failure that set the badge. */
export function HardFailNote({ reason }: { reason: string | null | undefined }) {
    if (!reason) return null
    const meaning = meaningOf("hardFailReason", reason)
    return (
        <div className="trust-hard-fail" role="note" data-component="trust-hard-fail">
            <p className="trust-hard-fail-title">Hard failure: {tokenWords(reason)}</p>
            {meaning && <p className="note">{meaning}</p>}
        </div>
    )
}

/** One row per check the server reported, in served order: name, outcome, and what the rubric asks. */
export function TrustChecks({ evidence }: { evidence: Evidence }) {
    const checks = Object.entries(evidence.checks ?? {})
    if (checks.length === 0) return null
    const attention = Boolean(evidence.hard_fail_reason) || checks.some(([, outcome]) => outcome === "missing")
    return (
        <div className="trust-checks">
            <Disclosure summary="Every check" count={checks.length} defaultOpen={attention}>
                <div className="table-scroll table-scroll--compact">
                    <table className="data-table" aria-label="Every trust check">
                        <thead>
                            <tr><th scope="col">Check</th><th scope="col">Outcome</th><th scope="col">What it asks</th></tr>
                        </thead>
                        <tbody>
                            {checks.map(([name, outcome]) => (
                                <tr key={name}>
                                    <td data-label="Check">{tokenWords(name)}</td>
                                    <td data-label="Outcome">{outcomePill(outcome)}</td>
                                    <td className="trust-check-ask" data-label="What it asks">{checkAsks(evidence.rubric, name) ?? ""}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </Disclosure>
        </div>
    )
}

/**
 * Trust of one record: hard-fail reason first when there is one, then the badge with its meaning and the weighted check
 * count, the review status, the rubric it was judged under, and every check. `variant="block"` renders an `<h4>` for use
 * inside a record card; the default is a registered page section.
 */
export function TrustAssessment({ trust, headingId, variant = "section", className }: {
    trust: TrustFragment
    headingId: string
    variant?: "section" | "block"
    /** Added beside the component's own class, for the page's section spacing. */
    className?: string
}) {
    const { evidence } = trust
    const heading = variant === "block"
        ? <h4 className="model-block-heading" id={headingId}>Trust</h4>
        : <SectionHeading id={headingId}>Trust</SectionHeading>
    const statusMeaning = meaningOf("trustStatus", trust.trust_status)
    const reviewMeaning = meaningOf("reviewStatus", trust.review_status)
    return (
        <section aria-labelledby={headingId} className={className ? `trust-assessment ${className}` : "trust-assessment"} data-component="trust-assessment">
            {heading}
            <HardFailNote reason={evidence.hard_fail_reason} />
            <p className="trust-assessment-line">
                <span className="value-pill">{tokenWords(trust.trust_status)}</span>
                {statusMeaning && <span className="note">{statusMeaning}</span>}
            </p>
            <p className="note">{evidence.passed_count} of {evidence.possible_count} weighted checks passed</p>
            {trust.review_status && (
                <p className="trust-assessment-line">
                    <span className="note">Review status</span>
                    <span className={reviewPillClass(trust.review_status)}>{tokenWords(trust.review_status)}</span>
                    {reviewMeaning && <span className="note">{reviewMeaning}</span>}
                </p>
            )}
            {evidence.rubric != null && evidence.rubric_version != null && (
                <MadeUnderLine madeUnder={liveMadeUnder(evidence.rubric, evidence.rubric_version)} />
            )}
            <TrustChecks evidence={evidence} />
        </section>
    )
}
