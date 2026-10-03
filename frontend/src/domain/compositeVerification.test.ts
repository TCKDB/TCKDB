import { describe, expect, it } from "vitest"
import { isContradiction, legacyShapeText, reasonText, verificationView } from "./compositeVerification"

const v = (state: string, extra: Record<string, unknown> = {}) => ({ state, assembly: "assembled", ...extra })

describe("verificationView state mapping", () => {
    it.each([
        ["recomputed", "confirmed", "Recomputed from its inputs"],
        ["log_reconciled", "confirmed", "Confirmed against the output log"],
        ["program_reported", "neutral", "Reported by the program"],
        ["unverifiable", "neutral", "Cannot be checked"],
        ["recompute_mismatch", "contradiction", "Does not match its inputs"],
    ])("%s reads as %s with the headline %s", (state, tone, headline) => {
        const view = verificationView(v(state))
        expect(view.tone).toBe(tone)
        expect(view.headline).toBe(headline)
    })

    it("a log that disagrees is a contradiction, never a plain program_reported", () => {
        const view = verificationView(v("program_reported", { reason: "log_mismatch" }))
        expect(view.tone).toBe("contradiction")
        expect(view.headline).toBe("Output log disagrees")
        const method = verificationView(v("program_reported", { reason: "log_method_mismatch" }))
        expect(method.tone).toBe("contradiction")
    })

    it("an unconfirming log stays neutral and says why", () => {
        const view = verificationView(v("program_reported", { reason: "log_did_not_confirm" }))
        expect(view.tone).toBe("neutral")
        expect(view.detail).toContain("could not confirm")
    })

    it("a mismatch says which side the stated total is on and carries the figures", () => {
        const view = verificationView(v("recompute_mismatch", { difference_hartree: -0.001, tolerance_hartree: 4.5e-6 }))
        expect(view.detail).toContain("below")
        expect(view.difference).toBe("0.001000 hartree")
        expect(view.tolerance).toBe("4.50e-6 hartree")
    })

    it("absent figures stay absent, never zero", () => {
        const view = verificationView(v("recomputed"))
        expect(view.difference).toBeNull()
        expect(view.tolerance).toBeNull()
    })

    it("an unknown state degrades to a neutral transcription", () => {
        const view = verificationView(v("brand_new_state"))
        expect(view.tone).toBe("neutral")
        expect(view.headline).toBe("brand new state")
    })
})

describe("isContradiction", () => {
    it("is true only for a mismatch or a disagreeing log", () => {
        expect(isContradiction(v("recompute_mismatch"))).toBe(true)
        expect(isContradiction(v("program_reported", { reason: "log_mismatch" }))).toBe(true)
        expect(isContradiction(v("program_reported", { reason: "log_method_mismatch" }))).toBe(true)
        expect(isContradiction(v("program_reported"))).toBe(false)
        expect(isContradiction(v("unverifiable", { reason: "log_mismatch" }))).toBe(false)
        expect(isContradiction(null)).toBe(false)
    })
})

describe("reasons and legacy shapes", () => {
    it("words a known reason and transcribes an unknown one", () => {
        expect(reasonText("scheme_not_bound")).toContain("not tied to a recipe")
        expect(reasonText("some_new_reason")).toBe("some new reason")
        expect(reasonText(null)).toBeNull()
    })

    it("words both legacy shapes, and nothing for an absent one", () => {
        expect(legacyShapeText("composite_role_on_non_composite_calculation")).toContain("older shape")
        expect(legacyShapeText("named_method_level_on_non_composite_calculation")).toContain("named composite method")
        expect(legacyShapeText(null)).toBeNull()
    })
})
