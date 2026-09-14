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

describe("classifyReactionQuery: every recognized arrow means the same thing -- only splits sides", () => {
    // Owner correction: an earlier version of this grammar mapped `<>`/
    // `<=>`/`<->` to "either direction" and `=>`/`->` to "forward only" --
    // a hidden mode a reader typing `<>` had no way to discover. Every
    // arrow now produces the identical `{reactants, products}` shape; the
    // "either direction" search and the per-result "matched in reverse"
    // label live in `IdentifierSearch.tsx`, not here.
    it.each(["<=>", "<->", "<>", "=>", "->"])("splits reactants/products the same way for arrow %s", (arrow) => {
        const result = classifyReactionQuery(`NN,[H] ${arrow} N,[NH2]`)
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind).toBe("equation")
        if (result.kind !== "equation") return
        expect(result).not.toHaveProperty("direction")
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

    // Owner: "what if they only know the products but not the reactants
    // they are searching for? maybe they can do just `<>[NH2]`". A one-
    // sided equation now PARSES -- it used to be rejected here, back when
    // this test asserted the opposite. See `classifyReactionQuery`'s own
    // comment on why the unwritten side is not a narrowing convenience
    // (every reaction in this archive is reversible and every search here
    // already runs `direction=either`): the side given is sent, the other
    // is simply not constrained, never defaulted to "empty" or rejected.
    it("accepts an equation missing its product side -- products unconstrained, only reactants sent", () => {
        const result = classifyReactionQuery("NN,[H] <> ")
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind).toBe("equation")
        if (result.kind !== "equation") return
        expect(result.reactants).toEqual(["NN", "[H]"])
        expect(result.products).toEqual([])
    })

    it("accepts an equation missing its reactant side -- reactants unconstrained, only products sent", () => {
        const result = classifyReactionQuery(" <> N,[NH2]")
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.kind).toBe("equation")
        if (result.kind !== "equation") return
        expect(result.reactants).toEqual([])
        expect(result.products).toEqual(["N", "[NH2]"])
    })

    // The one equation shape still rejected: an arrow with NOTHING on
    // EITHER side. Distinct from a one-sided equation (both tests above)
    // and from a genuine empty-result search -- there is no structure here
    // at all to search for, on either side.
    it("rejects a bare arrow with nothing on either side, as a parse failure", () => {
        const result = classifyReactionQuery("<>")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/at least one side/i)
    })

    it("rejects a bare arrow surrounded only by whitespace, as a parse failure", () => {
        const result = classifyReactionQuery("  <>  ")
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toMatch(/at least one side/i)
    })

    it("a valid equation and a parse failure never share the same classification kind", () => {
        const valid = classifyReactionQuery("NN <> N")
        const invalid = classifyReactionQuery("NN => N => H")
        expect(valid.valid).toBe(true)
        expect(invalid.valid).toBe(false)
    })
})
