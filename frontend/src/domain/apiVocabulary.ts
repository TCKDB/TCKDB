import vocabulary from "./apiVocabulary.json"

// The published meaning of each token a record page words from the trust and reproducibility blocks, copied verbatim from
// the API vocabulary guide (docs/guides/api_vocabulary.md) into `apiVocabulary.json`. `apiVocabulary.parity.test.ts`
// fails when the copy drifts. A token not in the tables has no meaning here and the page shows it as words only.

export type VocabularyKind = "reviewStatus" | "trustStatus" | "checkOutcome" | "hardFailReason" | "reproducibilityGrade"

const TABLES: Record<VocabularyKind, Record<string, string>> = vocabulary

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

/** The rubric's own sentence for a check ("What it asks"), or null when the rubric does not carry that check. */
export function checkAsks(rubric: string | null | undefined, check: string): string | null {
    if (!rubric) return null
    const key = `${rubric}:${check}`
    return Object.hasOwn(vocabulary.checkAsks, key) ? (vocabulary.checkAsks as Record<string, string>)[key] : null
}
