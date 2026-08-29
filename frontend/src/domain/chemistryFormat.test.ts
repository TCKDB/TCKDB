import { describe, expect, it } from "vitest"
import { ABSENT, chargeDisplay, chargeText, entryCountDisplay, formulaTokens, spinDisplay, spinWord } from "./chemistryFormat"

// These rules were pinned only by `backend/tests/api/test_landing_page.py`,
// which is being deleted along with `landing.py`. This file is now the only
// place that would fail if the regexes/tables in `chemistryFormat.ts` drift
// from the rules they were ported from.

describe("formulaTokens", () => {
    it("splits a simple formula into element/count pairs", () => {
        expect(formulaTokens("H2O")).toEqual([
            { element: "H", count: "2" },
            { element: "O", count: "" },
        ])
    })

    it("emits no count for a single-atom element (subscript omitted, not '1')", () => {
        expect(formulaTokens("HCl")).toEqual([
            { element: "H", count: "" },
            { element: "Cl", count: "" },
        ])
    })

    it("handles multi-digit counts", () => {
        expect(formulaTokens("C12H26")).toEqual([
            { element: "C", count: "12" },
            { element: "H", count: "26" },
        ])
    })

    it("handles two-letter element symbols without splitting the letters apart", () => {
        // The regression this guards: a naive per-character scan could read
        // "Cl2" as "C", "l", "2" -- one correct element and one bogus
        // single-letter "l" token, which `Formula.tsx` would then render as
        // a stray "C l₂" instead of "Cl₂".
        expect(formulaTokens("Cl2")).toEqual([{ element: "Cl", count: "2" }])
    })

    it("round-trips: rejects a match set that does not reconstruct the input", () => {
        // "H2O!" -- the regex matches "H2O" and silently drops the "!".
        // Without the round-trip guard this would return tokens for "H2O"
        // and quietly discard the trailing garbage.
        expect(formulaTokens("H2O!")).toBeNull()
    })

    it("rejects a lowercase-leading string that cannot be an element symbol", () => {
        expect(formulaTokens("h2o")).toBeNull()
    })

    it("rejects an empty string", () => {
        expect(formulaTokens("")).toBeNull()
    })
})

describe("chargeText", () => {
    it("renders a positive charge with an explicit '+' sign", () => {
        expect(chargeText(1)).toBe("+1")
        expect(chargeText(2)).toBe("+2")
    })

    it("renders a negative charge with U+2212 (real minus), not ASCII hyphen-minus", () => {
        const result = chargeText(-1)
        expect(result).toBe("−1")
        expect(result).not.toBe("-1")
        // Byte-level check that the ASCII hyphen (U+002D) never appears.
        expect(result?.includes("-")).toBe(false)
    })

    it("renders a larger negative charge correctly", () => {
        expect(chargeText(-3)).toBe("−3")
    })

    it("renders neutral charge as a bare '0', not '+0'", () => {
        expect(chargeText(0)).toBe("0")
    })

    it("returns null for a genuinely absent charge, distinct from zero", () => {
        expect(chargeText(null)).toBeNull()
        expect(chargeText(undefined)).toBeNull()
    })
})

describe("spinWord — the full 1-8 table", () => {
    const table: [number, string][] = [
        [1, "singlet"],
        [2, "doublet"],
        [3, "triplet"],
        [4, "quartet"],
        [5, "quintet"],
        [6, "sextet"],
        [7, "septet"],
        [8, "octet"],
    ]

    it.each(table)("maps multiplicity %i to '%s'", (multiplicity, word) => {
        expect(spinWord(multiplicity)).toBe(word)
    })

    it("returns null for an out-of-range multiplicity (9) rather than guessing a word", () => {
        expect(spinWord(9)).toBeNull()
    })

    it("returns null for a zero or negative multiplicity", () => {
        expect(spinWord(0)).toBeNull()
        expect(spinWord(-1)).toBeNull()
    })

    it("returns null when multiplicity itself is absent", () => {
        expect(spinWord(null)).toBeNull()
        expect(spinWord(undefined)).toBeNull()
    })
})

describe("spinDisplay", () => {
    it("pairs the word with the number for a mapped multiplicity", () => {
        expect(spinDisplay(2)).toBe("doublet (2)")
    })

    it("falls back to the bare number for an unmapped multiplicity, not ABSENT", () => {
        // This is the divergence the audit found against `landing.py`'s
        // `stateCell`, which would style this the same as a missing value.
        // Resolved here: presence of *a* multiplicity is real information
        // and must not collapse into the same rendering as no multiplicity
        // at all. See the module-level decision note near `spinDisplay`.
        expect(spinDisplay(9)).toBe("9")
    })

    it("renders ABSENT only when multiplicity itself is missing", () => {
        expect(spinDisplay(null)).toBe(ABSENT)
        expect(spinDisplay(undefined)).toBe(ABSENT)
    })
})

describe("chargeDisplay", () => {
    it("renders ABSENT for a missing charge", () => {
        expect(chargeDisplay(null)).toBe(ABSENT)
    })

    it("renders a signed charge otherwise", () => {
        expect(chargeDisplay(-2)).toBe("−2")
    })
})

describe("entryCountDisplay", () => {
    it("pluralizes zero and plural counts as 'entries'", () => {
        expect(entryCountDisplay(0)).toBe("0 entries")
        expect(entryCountDisplay(4)).toBe("4 entries")
    })

    it("keeps the singular for exactly one", () => {
        expect(entryCountDisplay(1)).toBe("1 entry")
    })
})

describe("ABSENT", () => {
    it("is the lowercase, mid-sentence-safe spelling", () => {
        // See the project-wide decision recorded in quantityFormat.ts's
        // module docstring: this is the one spelling used everywhere a
        // value is missing, chosen specifically because it reads correctly
        // both standalone ("Sigma: not recorded") and inline
        // ("charge not recorded") without a capitalization special case.
        expect(ABSENT).toBe("not recorded")
    })
})
