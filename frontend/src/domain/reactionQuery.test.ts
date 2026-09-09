import { describe, expect, it } from "vitest"
import { classifyReactionQuery, isRecognizedArrow } from "./reactionQuery"

describe("classifyReactionQuery: bare structure or comma-list -> participation, either side", () => {
    it("classifies a single bare SMILES as participation", () => {
        const result = classifyReactionQuery("NN")
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind).toBe("participation")
        expect(result.kind === "participation" && result.smiles).toEqual(["NN"])
    })

    // Mutation-table item (f): commas -- never `+` -- split species on one
    // side. `[NH4+]` carries a real chemical `+` that must survive intact.
    it("splits a comma-list into several participants, leaving a `+`-bearing SMILES intact", () => {
        const result = classifyReactionQuery("NN,[NH4+]")
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind === "participation" && result.smiles).toEqual(["NN", "[NH4+]"])
    })

    it("trims whitespace and drops stray blank tokens", () => {
        const result = classifyReactionQuery(" NN , [H] , ")
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind === "participation" && result.smiles).toEqual(["NN", "[H]"])
    })

    it("rejects an empty query", () => {
        const result = classifyReactionQuery("   ")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/enter a structure/i)
    })
})

describe("classifyReactionQuery: the arrow carries the direction", () => {
    it.each([
        ["<=>", "either"], ["<->", "either"], ["<>", "either"],
        ["=>", "forward"], ["->", "forward"],
    ] as const)("maps %s to direction=%s", (arrow, direction) => {
        const result = classifyReactionQuery(`NN,[H] ${arrow} N,[NH2]`)
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind).toBe("equation")
        if (result.kind !== "equation") return
        expect(result.direction).toBe(direction)
        expect(result.reactants).toEqual(["NN", "[H]"])
        expect(result.products).toEqual(["N", "[NH2]"])
    })

    it("isRecognizedArrow agrees with the classifier's own arrow set", () => {
        expect(isRecognizedArrow("<=>")).toBe(true)
        expect(isRecognizedArrow("<->")).toBe(true)
        expect(isRecognizedArrow("<>")).toBe(true)
        expect(isRecognizedArrow("=>")).toBe(true)
        expect(isRecognizedArrow("->")).toBe(true)
        expect(isRecognizedArrow(">")).toBe(false)
        expect(isRecognizedArrow("=")).toBe(false)
    })
})

describe("classifyReactionQuery: parse failures are distinct from an empty result -- never collapsed together", () => {
    // Mutation-table item (g): a parse failure must never be reported the
    // same way an honest zero-match search is.
    it("rejects an equation with two arrows as a parse failure, not silently picking one", () => {
        const result = classifyReactionQuery("NN => N => [H]")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/more than one reaction arrow/i)
    })

    it("rejects an equation missing its product side", () => {
        const result = classifyReactionQuery("NN,[H] <> ")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/both sides/i)
    })

    it("rejects an equation missing its reactant side", () => {
        const result = classifyReactionQuery(" <> N,[NH2]")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/both sides/i)
    })

    it("a valid equation and a parse failure never share the same classification kind", () => {
        const valid = classifyReactionQuery("NN <> N")
        const invalid = classifyReactionQuery("NN => N => H")
        expect(valid.valid).toBe(true)
        expect(invalid.valid).toBe(false)
    })
})
