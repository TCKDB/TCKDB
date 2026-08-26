import { describe, expect, it } from "vitest"
import { classifyIdentifier, resultPath } from "./recordModel"

describe("classifyIdentifier", () => {
    it.each([
        ["H2O", "formula"], ["Cl2", "formula"], ["Ca", "formula"], ["Cr", "formula"],
        ["spc_abcde234567abcde234567abcd", "species-ref"], ["spe_bcdef234567bcdef234567abcd", "species-entry-ref"],
        ["InChI=1S/H2O/h1H2", "inchi"], ["XLYOFNOQVPJJNP-UHFFFAOYSA-N", "inchi-key"],
    ])("classifies %s", (value, kind) => {
        const result = classifyIdentifier(value)
        expect(result.valid && result.identifier.kind).toBe(kind)
    })

    it("explains empty and unsupported whitespace input", () => {
        expect(classifyIdentifier("").valid).toBe(false)
        expect(classifyIdentifier("ethyl alcohol").valid).toBe(false)
    })

    it("rejects malformed element tokens and malformed public references", () => {
        expect(classifyIdentifier("Xx2").valid).toBe(false)
        expect(classifyIdentifier("H02").valid).toBe(false)
        expect(classifyIdentifier("spc_not-a-ref").valid).toBe(false)
        expect(classifyIdentifier("spc_ABCde234567abcde234567abcd").valid).toBe(false)
        expect(classifyIdentifier("spe_abcde034567abcde234567abcd").valid).toBe(false)
    })

    it("requires a deterministic prefix for formula/SMILES ambiguity", () => {
        expect(classifyIdentifier("CCO").valid).toBe(false)
        expect(classifyIdentifier("Cl").valid).toBe(false)
        expect(classifyIdentifier("formula:Cl").valid).toBe(true)
        expect(classifyIdentifier("smiles:CCO").valid).toBe(true)
    })

    it("routes a match to its stable species-entry record", () => {
        expect(resultPath({ speciesRef: "spc_abcde234567abcde234567abcd", entryRef: "spe_bcdef234567bcdef234567abcd" }))
            .toBe("/species-entries/spe_bcdef234567bcdef234567abcd")
        expect(resultPath({ speciesRef: "spc_abcde234567abcde234567abcd" })).toBe("/species/spc_abcde234567abcde234567abcd")
    })
})
