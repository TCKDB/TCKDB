import { describe, expect, it } from "vitest"
import { classifyIdentifier, resultPath } from "./recordModel"

describe("classifyIdentifier", () => {
    it.each([
        ["H2O", "formula"], ["spec_abc123", "public-ref"], ["CCO", "smiles"],
        ["InChI=1S/H2O/h1H2", "inchi"], ["XLYOFNOQVPJJNP-UHFFFAOYSA-N", "inchi-key"],
    ])("classifies %s", (value, kind) => {
        const result = classifyIdentifier(value)
        expect(result.valid && result.identifier.kind).toBe(kind)
    })

    it("explains empty and unsupported whitespace input", () => {
        expect(classifyIdentifier("").valid).toBe(false)
        expect(classifyIdentifier("ethyl alcohol").valid).toBe(false)
    })

    it("routes a match to its stable species-entry record", () => {
        expect(resultPath({ speciesRef: "spec_water", entryRef: "se_water" })).toBe("/species-entries/se_water")
    })
})
