import { describe, expect, it } from "vitest"
import vocabulary from "./apiVocabulary.json"
import { READER_HARD_FAIL_REASON, READER_REVIEW_STATUS, READER_TRUST_STATUS } from "./readerWording"

// The published guide. `import.meta.glob` instead of `node:fs` (no Node types under src/); a match count other than one
// fails the test rather than passing it with nothing to compare.
const GUIDE_FILES = import.meta.glob("../../../docs/guides/api_vocabulary.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>

const HEADINGS = {
    reviewStatus: "Review status",
    trustStatus: "Trust status (the evidence badge)",
    checkOutcome: "Check outcomes",
    hardFailReason: "Hard-fail reasons",
    reproducibilityGrade: "Reproducibility grade",
    checkAsks: "Check names",
} as const

const plain = (text: string) => text.replaceAll("**", "").replaceAll("`", "").trim()

function parseTable(lines: string[], heading: string, keyed: boolean): Record<string, string> {
    const start = lines.indexOf(`### ${heading}`)
    expect(start, `section "${heading}" in the guide`).toBeGreaterThan(-1)
    const out: Record<string, string> = {}
    for (let i = start + 1; i < lines.length && !lines[i].startsWith("#"); i++) {
        if (!lines[i].startsWith("| `")) continue
        const cells = lines[i].slice(2, -2).split(" | ")
        if (keyed) out[`${plain(cells[1])}:${plain(cells[0])}`] = plain(cells[4])
        else out[plain(cells[0])] = plain(cells.slice(1).join(" | "))
    }
    return out
}

describe("apiVocabulary.json parity with docs/guides/api_vocabulary.md", () => {
    it("finds exactly one guide", () => {
        expect(Object.keys(GUIDE_FILES)).toHaveLength(1)
    })

    const lines = (Object.values(GUIDE_FILES)[0] ?? "").split("\n")

    for (const [key, heading] of Object.entries(HEADINGS)) {
        it(`copies the "${heading}" table exactly`, () => {
            const parsed = parseTable(lines, heading, key === "checkAsks")
            expect(Object.keys(parsed).length).toBeGreaterThan(3)
            expect(vocabulary[key as keyof typeof vocabulary]).toEqual(parsed)
        })
    }

    it("words every guide token for a reader, and nothing else", () => {
        expect(Object.keys(READER_REVIEW_STATUS).sort()).toEqual(Object.keys(vocabulary.reviewStatus).sort())
        expect(Object.keys(READER_TRUST_STATUS).sort()).toEqual(Object.keys(vocabulary.trustStatus).sort())
        expect(Object.keys(READER_HARD_FAIL_REASON).sort()).toEqual(Object.keys(vocabulary.hardFailReason).sort())
    })
})
