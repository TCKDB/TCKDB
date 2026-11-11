import { describe, expect, it } from "vitest"
import { modelKindWord, originWord, tunnellingWord } from "./kineticsWords"

describe("kinetics code wording", () => {
    it.each(["arrhenius", "modified_arrhenius", "multi_arrhenius", "lindemann", "troe", "sri", "plog", "chebyshev"])("words model kind %s", (code) => {
        expect(modelKindWord(code)).toMatch(/^[A-Z]/)
        expect(modelKindWord(code)).not.toContain("_")
    })
    it.each(["computed", "experimental", "estimated"])("words origin %s", (code) => {
        expect(originWord(code)).toBe(code)
    })
    it.each(["none", "wigner", "eckart", "sct", "other"])("words tunnelling model %s", (code) => {
        expect(tunnellingWord(code)).not.toBeNull()
    })
    it("returns null for a code it does not know, including inherited object keys", () => {
        expect(modelKindWord("new_kind")).toBeNull()
        expect(originWord("toString")).toBeNull()
        expect(tunnellingWord("")).toBeNull()
    })
})
