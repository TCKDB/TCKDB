import { describe, expect, it } from "vitest"
import { classifyIdentifier, resultPath } from "./recordModel"

describe("classifyIdentifier", () => {
    it.each([
        ["H2O", "formula"], ["Cl2", "formula"], ["Ca", "formula"], ["Cr", "formula"],
        ["spc_5bxnghp44yj0hf2vp9k1a6tk20", "species-ref"], ["spe_01J9X8K3Y2RM4F0X8K3Y2RM4F0", "species-entry-ref"],
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
    })

    it("requires a deterministic prefix for formula/SMILES ambiguity", () => {
        expect(classifyIdentifier("CCO").valid).toBe(false)
        expect(classifyIdentifier("Cl").valid).toBe(false)
        expect(classifyIdentifier("formula:Cl").valid).toBe(true)
        expect(classifyIdentifier("smiles:CCO").valid).toBe(true)
    })

    it("routes a match to its stable species-entry record", () => {
        expect(resultPath({ speciesRef: "spc_5bxnghp44yj0hf2vp9k1a6tk20", entryRef: "spe_01J9X8K3Y2RM4F0X8K3Y2RM4F0" }))
            .toBe("/species-entries/spe_01J9X8K3Y2RM4F0X8K3Y2RM4F0")
        expect(resultPath({ speciesRef: "spc_5bxnghp44yj0hf2vp9k1a6tk20" })).toBe("/species/spc_5bxnghp44yj0hf2vp9k1a6tk20")
    })
})
