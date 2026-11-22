import { DepositWarningsPanel } from "./DepositWarningsPanel"
import { ReproducibilityAssessment } from "./ReproducibilityAssessment"
import { TrustAssessment } from "./TrustAssessment"
import type { DepositWarnings } from "../api/depositWarningsSchema"
import type { Assessments, TrustFragment } from "../api/trustSchema"

/**
 * What a list-shaped record card says about how far to rely on it: the trust verdict, the reproducibility assessment and
 * the deposit warnings for that one record, as blocks under the card's own heading. Each block is left out when the server
 * did not serve it. `warnings` is `"unavailable"` when the warnings could not be read, which is said in one line and does
 * not hide the rest. `idBase` keeps every heading id unique on the page.
 */
export function RecordEvidenceBlocks({ trust, assessments, warnings, idBase }: {
    trust?: TrustFragment | null
    assessments?: Assessments | null
    warnings?: DepositWarnings | "unavailable" | null
    idBase: string
}) {
    return (
        <>
            {trust && <TrustAssessment trust={trust} headingId={`trust-${idBase}`} variant="block" />}
            <ReproducibilityAssessment reproducibility={assessments?.reproducibility} idBase={idBase} />
            {warnings === "unavailable" && <p className="note">Deposit warnings could not be loaded for this record.</p>}
            {warnings && warnings !== "unavailable" && (
                <DepositWarningsPanel warnings={warnings} variant="block" headingId={`deposit-warnings-${idBase}`} />
            )}
        </>
    )
}
