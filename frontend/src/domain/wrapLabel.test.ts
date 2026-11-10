import { describe, expect, it } from "vitest"
import { LINE_CHARS, wrapLabel } from "./wrapLabel"

describe("wrapLabel", () => {
    it("wraps at word boundaries", () => {
        expect(wrapLabel("Reactions with this molecule")).toEqual(["Reactions with this", "molecule"])
    })

    it("hard-splits a long word with no spaces, preferring to break after a bracket or bond sign", () => {
        const smiles = "CC(C)(C)OC(=O)N1CCC(CC1)C(=O)O"
        const lines = wrapLabel(smiles)
        expect(lines.join("")).toBe(smiles)
        expect(lines.length).toBeGreaterThan(1)
        for (const line of lines) {
            expect(line.length).toBeLessThanOrEqual(LINE_CHARS)
            expect(line).not.toContain("…")
        }
        expect("()=,.-".includes(lines[0].slice(-1))).toBe(true)
    })

    it("splits a word with no break character at the line width", () => {
        const lines = wrapLabel("A".repeat(50))
        expect(lines.map((l) => l.length)).toEqual([21, 21, 8])
    })
})
