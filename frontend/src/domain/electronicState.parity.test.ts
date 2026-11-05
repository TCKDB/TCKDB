import { describe, expect, it } from "vitest"
import grammar from "./electronicStateGrammar.json"
import { termSymbolSpoken, validStateLabel, validTermSymbol } from "./electronicState"

// The shipped grammar (tckdb-schemas `electronic_state_examples.json`). `import.meta.glob` instead of `node:fs`
// (no Node types under src/); an empty match fails the test rather than passing it with nothing to compare.
const SHIPPED_FILES = import.meta.glob(
    "../../../schemas/python/tckdb-schemas/tckdb_schemas/electronic_state_examples.json",
    { import: "default", eager: true },
) as Record<string, ShippedExamples>

interface FieldExamples {
    max_length: number
    source: string
    accepted: string[]
    rejected: string[]
    canonical: Record<string, string>
}
interface ShippedExamples {
    trim: string
    electronic_state_label: FieldExamples
    term_symbol: FieldExamples
}

const shipped = Object.values(SHIPPED_FILES)[0]

describe("electronic-state grammar parity with tckdb-schemas", () => {
    it("found the shipped examples file", () => {
        expect(Object.keys(SHIPPED_FILES)).toHaveLength(1)
        expect(shipped.electronic_state_label.accepted.length).toBeGreaterThan(20)
        expect(shipped.term_symbol.accepted.length).toBeGreaterThan(40)
    })

    it("holds a verbatim copy of the shipped sources and length caps", () => {
        for (const field of ["electronic_state_label", "term_symbol"] as const) {
            expect(grammar[field].source, `${field} source drifted from the shipped file`).toBe(shipped[field].source)
            expect(grammar[field].max_length).toBe(shipped[field].max_length)
        }
    })

    for (const [field, check] of [["electronic_state_label", validStateLabel], ["term_symbol", validTermSymbol]] as const) {
        it(`accepts every shipped ${field} example`, () => {
            for (const value of shipped[field].accepted) expect(check(value), `${field} ${JSON.stringify(value)}`).not.toBeNull()
        })
        it(`refuses every shipped ${field} counter-example`, () => {
            for (const value of shipped[field].rejected) expect(check(value), `${field} ${JSON.stringify(value)}`).toBeNull()
        })
        it(`accepts every canonical (stored) ${field} spelling and shows it unchanged`, () => {
            for (const canonical of new Set(Object.values(shipped[field].canonical))) expect(check(canonical)).toBe(canonical)
        })
    }
})

describe("termSymbolSpoken", () => {
    it("reads a term symbol as words for a screen reader", () => {
        expect(termSymbolSpoken("²Π")).toBe("doublet pi")
        expect(termSymbolSpoken("X ²Π")).toBe("X doublet pi")
        expect(termSymbolSpoken("³Σg⁻")).toBe("triplet sigma g minus")
        expect(termSymbolSpoken("¹A₁")).toBe("singlet A 1")
        expect(termSymbolSpoken("²P₃⁄₂")).toBe("doublet P 3 over 2")
    })
    it("has no reading for text outside the grammar", () => {
        expect(termSymbolSpoken("DEPOSITOR_SENTINEL")).toBeNull()
        expect(termSymbolSpoken(null)).toBeNull()
    })
})

describe("trimming parity with Python", () => {
    it("trims exactly the shipped set: ASCII whitespace yes; U+FEFF, U+0085, U+001C, NBSP no", () => {
        expect(grammar.trim).toBe(shipped.trim)
        expect(validStateLabel(" \tX\n")).toBe("X")
        expect(validTermSymbol("\f\u00b2\u03a0\v")).toBe("\u00b2\u03a0")
        for (const stray of ["\ufeff", "\u0085", "\u001c", "\u00a0"]) {
            expect(validStateLabel(`${stray}X`), JSON.stringify(stray)).toBeNull()
            expect(validStateLabel(`X${stray}`), JSON.stringify(stray)).toBeNull()
            expect(validTermSymbol(`${stray}2Pi`), JSON.stringify(stray)).toBeNull()
        }
    })
})
