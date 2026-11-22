import { describe, expect, it } from "vitest"
import { checkAsks, meaningOf, tokenWords } from "./apiVocabulary"

describe("apiVocabulary", () => {
    it("words a token", () => expect(tokenWords("hard_failed")).toBe("hard failed"))

    it("gives the published definition without its worked example", () => {
        expect(meaningOf("reviewStatus", "under_review")).toMatch(/^A curator has this record open\./)
        expect(meaningOf("reviewStatus", "under_review")).not.toMatch(/For example/)
        expect(meaningOf("reproducibilityGrade", "described")).toMatch(/ceiling for every non-calculation record/)
    })

    it("returns null for a token the guide does not define", () => {
        expect(meaningOf("trustStatus", "brand_new_token")).toBeNull()
        expect(meaningOf("checkOutcome", "constructor")).toBeNull()
        expect(meaningOf("hardFailReason", null)).toBeNull()
    })

    it("looks a check up by rubric and name", () => {
        expect(checkAsks("computed_kinetics", "arrhenius_units_present")).toBe("Arrhenius A units should be populated for Arrhenius-family kinetics.")
        expect(checkAsks("computed_kinetics", "no_such_check")).toBeNull()
        expect(checkAsks(undefined, "arrhenius_units_present")).toBeNull()
    })
})
