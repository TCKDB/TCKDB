import { describe, expect, it } from "vitest"
import { softwareLabel, toolReleaseLabel, words } from "./provenanceFormat"

describe("softwareLabel", () => {
    it("prepends the name when the version does not already start with it", () => {
        expect(softwareLabel({ software: "Gaussian", version: "16" })).toBe("Gaussian 16")
    })

    it("does NOT concatenate unconditionally when the version already opens with the name", () => {
        // This is the exact live defect the brief names: unconditional
        // concatenation produces "Gaussian Gaussian 16".
        expect(softwareLabel({ software: "Gaussian", version: "Gaussian 16" })).toBe("Gaussian 16")
    })

    it("returns just the version when there is no name", () => {
        expect(softwareLabel({ software: null, version: "16" })).toBe("16")
    })

    it("returns just the name when there is no version", () => {
        expect(softwareLabel({ software: "Gaussian", version: null })).toBe("Gaussian")
    })

    it("returns null for a missing release", () => {
        expect(softwareLabel(null)).toBeNull()
        expect(softwareLabel(undefined)).toBeNull()
    })

    it("returns null when neither name nor version is present", () => {
        expect(softwareLabel({ software: null, version: null })).toBeNull()
    })
})

describe("toolReleaseLabel", () => {
    it("borrows softwareLabel's rule via the workflow_tool/version pair", () => {
        expect(toolReleaseLabel({ workflow_tool: "Arkane", version: "Arkane 2024" })).toBe("Arkane 2024")
        expect(toolReleaseLabel({ workflow_tool: "Arkane", version: "1.0" })).toBe("Arkane 1.0")
    })

    it("returns null for a missing release", () => {
        expect(toolReleaseLabel(null)).toBeNull()
    })
})

describe("words", () => {
    it("transcribes underscores to spaces", () => {
        expect(words("asymmetric_top")).toBe("asymmetric top")
    })

    it("does not invent an expansion beyond the transcription", () => {
        expect(words("cm3_mol_s")).toBe("cm3 mol s")
    })

    it("returns null for a missing or empty token", () => {
        expect(words(null)).toBeNull()
        expect(words(undefined)).toBeNull()
        expect(words("")).toBeNull()
    })
})
