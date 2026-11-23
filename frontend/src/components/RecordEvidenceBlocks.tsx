import { DepositWarningsPanel } from "./DepositWarningsPanel"
import { RecordWarningsOnRequest, type WarningsLoader } from "./RecordWarningsOnRequest"
import { ReproducibilityAssessment } from "./ReproducibilityAssessment"
import { TrustAssessment } from "./TrustAssessment"
import type { DepositWarnings } from "../api/depositWarningsSchema"
import type { Assessments, TrustFragment } from "../api/trustSchema"
import { numberedWords } from "../domain/recordNumbers"
import { useRecordNumbers } from "../hooks/useRecordNumbers"

/**
 * What a list-shaped record card says about how far to rely on it: the trust verdict, the reproducibility assessment and
 * the deposit warnings for that one record, as blocks under the card's own heading. Each block is left out when the server
 * did not serve it. Warnings come either with the list (`warnings`) or, where the list carries none, from the record's own
 * read, made only when the reader asks (`loadWarnings`). `idBase` keeps every heading id unique on the page; `noun` and
 * `recordRef` put the record into each block's accessible name ("Trust of statmech record 2").
 */
export function RecordEvidenceBlocks({ trust, assessments, warnings, loadWarnings, idBase, noun, recordRef, showReviewStatus = true }: {
    trust?: TrustFragment | null
    assessments?: Assessments | null
    warnings?: DepositWarnings | "unavailable" | null
    loadWarnings?: { ref: string; load: WarningsLoader }
    idBase: string
    noun?: string
    recordRef?: string
    /** `false` when the card around it already shows the review status. */
    showReviewStatus?: boolean
}) {
    const numbers = useRecordNumbers()
    const recordLabel = noun && recordRef ? numberedWords(noun, recordRef, numbers) : undefined
    return (
        <>
            {trust && <TrustAssessment trust={trust} headingId={`trust-${idBase}`} variant="block" recordLabel={recordLabel} showReviewStatus={showReviewStatus} />}
            <ReproducibilityAssessment reproducibility={assessments?.reproducibility} idBase={idBase} recordLabel={recordLabel} />
            {warnings === "unavailable" && <p className="note">Deposit warnings could not be loaded for this record.</p>}
            {warnings && warnings !== "unavailable" && (
                <DepositWarningsPanel warnings={warnings} variant="block" headingId={`deposit-warnings-${idBase}`} recordLabel={recordLabel} />
            )}
            {loadWarnings && (
                <RecordWarningsOnRequest ref_={loadWarnings.ref} load={loadWarnings.load} idBase={idBase} recordLabel={recordLabel} />
            )}
        </>
    )
}
