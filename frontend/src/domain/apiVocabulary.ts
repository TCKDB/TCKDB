import vocabulary from "./apiVocabulary.json"
import { readerCheckAsks } from "./readerChecks"
import { READER_HARD_FAIL_REASON, READER_REVIEW_STATUS, READER_TRUST_STATUS } from "./readerWording"

// The meaning of each token a record page words from the trust and reproducibility blocks. The token set is copied from
// the API vocabulary guide (docs/guides/api_vocabulary.md) into `apiVocabulary.json`, and `apiVocabulary.parity.test.ts`
// fails when the copy drifts. Review status, trust status and hard-fail reasons are shown in the site's own reader wording
// (`readerWording.ts`); the other tokens use the guide's sentence. A token not in the tables has no meaning here and the
// page shows it as words only.

export type VocabularyKind = "reviewStatus" | "trustStatus" | "checkOutcome" | "hardFailReason" | "reproducibilityGrade"

const TABLES: Record<VocabularyKind, Record<string, string>> = {
    ...vocabulary,
    reviewStatus: READER_REVIEW_STATUS,
    trustStatus: READER_TRUST_STATUS,
    hardFailReason: READER_HARD_FAIL_REASON,
}

/** A snake_case token as words: `hard_failed` reads "hard failed". */
export function tokenWords(token: string): string {
    return token.replaceAll("_", " ")
}

/** The guide's worked example follows the definition; a line beside a badge carries the definition only. */
const EXAMPLE_MARKER = " For example: "

/** The one-line meaning of a token, or null when the guide does not define it. */
export function meaningOf(kind: VocabularyKind, token: string | null | undefined): string | null {
    if (!token) return null
    const text = Object.hasOwn(TABLES[kind], token) ? TABLES[kind][token] : null
    if (text === null) return null
    const cut = text.indexOf(EXAMPLE_MARKER)
    return cut < 0 ? text : text.slice(0, cut)
}

/** The site's own sentence for a check ("What it asks"), or null when none is written: the guide's developer wording is never shown. */
export function checkAsks(rubric: string | null | undefined, check: string): string | null {
    return readerCheckAsks(rubric, check)
}
