import { MadeUnderLine } from "./MadeUnderLine"
import type { Reproducibility } from "../api/trustSchema"
import { meaningOf, tokenWords } from "../domain/apiVocabulary"
import "../trust-assessment.css"

// The reproducibility assessment of a record: the grade in words with the guide's one-line meaning, the date it was made,
// and the rubric it was made under. The grade says how far someone else could get with what was deposited; it is not a
// ranking and is never shown as one. A record nobody has assessed shows no grade.

const isoDate = (value: string) => value.slice(0, 10)

export function ReproducibilityAssessment({ reproducibility, idBase }: {
    reproducibility: Reproducibility | null | undefined
    idBase: string
}) {
    if (!reproducibility) return null
    const id = `reproducibility-${idBase}`
    const grade = reproducibility.state === "unassessed" ? null : reproducibility.grade
    const meaning = meaningOf("reproducibilityGrade", grade)
    const madeUnder = reproducibility.made_under ?? (reproducibility.state === "unassessed" ? { state: "unassessed" } : null)
    return (
        <section aria-labelledby={id} data-component="reproducibility-assessment">
            <h4 className="model-block-heading" id={id}>Reproducibility assessment</h4>
            {grade && (
                <p className="trust-assessment-line">
                    <span className="value-pill">{tokenWords(grade)}</span>
                    {meaning && <span className="note">{meaning}</span>}
                </p>
            )}
            {grade && reproducibility.assessed_at && <p className="note">Assessed {isoDate(reproducibility.assessed_at)}</p>}
            <MadeUnderLine madeUnder={madeUnder} />
        </section>
    )
}
