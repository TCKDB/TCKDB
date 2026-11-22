import { describe, expect, it } from "vitest"
import { checkAsks, meaningOf, tokenWords } from "./apiVocabulary"

describe("apiVocabulary", () => {
    it("words a token", () => expect(tokenWords("hard_failed")).toBe("hard failed"))

    it("gives the guide's definition, without its worked example, for the tokens that use it", () => {
        expect(meaningOf("checkOutcome", "not_applicable")).not.toMatch(/For example/)
        expect(meaningOf("reproducibilityGrade", "described")).toMatch(/ceiling for every non-calculation record/)
    })

    it("words review status, trust status and hard-fail reasons for a reader, with no API vocabulary", () => {
        for (const [kind, token] of [["reviewStatus", "approved"], ["reviewStatus", "rejected"], ["reviewStatus", "under_review"], ["trustStatus", "hard_failed"], ["hardFailReason", "calculation_rejected"]] as const) {
            const text = meaningOf(kind, token) ?? ""
            expect(text).not.toBe("")
            expect(text).not.toMatch(/include_|trust\.evidence|[a-z]+_[a-z]+|curated read profile/)
        }
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
