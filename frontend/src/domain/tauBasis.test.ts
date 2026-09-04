import { describe, expect, it } from "vitest"
import { hessianMethodLabel, isAssumedTauBasis, tauBasisNote } from "./tauBasis"

const RECORDED_CASES: Array<[string, string, string]> = [
    ["analytic_tight", "analytic", "analytic, tight convergence"],
    ["analytic_default", "analytic", "analytic, default convergence"],
    ["finite_difference_gradient", "numerical (from gradients)", "numerical, from gradients"],
    ["finite_difference_energy", "numerical (from energies)", "numerical, from energies"],
]

const ASSUMED_CASES: Array<[string, string, string]> = [
    ["assumed_analytic_default", "analytic", "analytic, default convergence"],
    ["assumed_finite_difference_gradient", "numerical (from gradients)", "numerical, from gradients"],
    ["assumed_finite_difference_energy", "numerical (from energies)", "numerical, from energies"],
]

describe("hessianMethodLabel", () => {
    it.each(RECORDED_CASES)("maps the recorded basis %s to its plain-language method", (basis, method) => {
        expect(hessianMethodLabel(basis)).toBe(method)
    })

    it.each(ASSUMED_CASES)("maps the assumed basis %s to its method PLUS a visible assumed suffix", (basis, method) => {
        expect(hessianMethodLabel(basis)).toBe(`${method} (assumed: the program's default for this method)`)
    })

    it('maps "protocol_not_recorded" to "not recorded"', () => {
        expect(hessianMethodLabel("protocol_not_recorded")).toBe("not recorded")
    })

    it('maps a null or undefined basis to "not recorded"', () => {
        expect(hessianMethodLabel(null)).toBe("not recorded")
        expect(hessianMethodLabel(undefined)).toBe("not recorded")
    })

    it("shows the raw token for a basis this build does not recognise, rather than hiding it", () => {
        expect(hessianMethodLabel("semi_numerical_v2")).toBe("semi_numerical_v2")
    })
})

describe("tauBasisNote", () => {
    it.each(RECORDED_CASES)("gives the recorded basis %s a plain note with no assumed language", (basis, _method, note) => {
        expect(tauBasisNote(basis)).toBe(note)
    })

    it.each(ASSUMED_CASES)("gives the assumed basis %s a note naming BOTH the assumption and the borrowed recorded note", (basis, _method, note) => {
        expect(tauBasisNote(basis)).toBe(`assumed from the program's default (${note})`)
    })

    it('maps "protocol_not_recorded" to "not recorded"', () => {
        expect(tauBasisNote("protocol_not_recorded")).toBe("not recorded")
    })

    it('maps a null or undefined basis to "not recorded"', () => {
        expect(tauBasisNote(null)).toBe("not recorded")
        expect(tauBasisNote(undefined)).toBe("not recorded")
    })

    it("shows the raw token for a basis this build does not recognise, rather than hiding it", () => {
        expect(tauBasisNote("semi_numerical_v2")).toBe("semi_numerical_v2")
    })
})

describe("isAssumedTauBasis", () => {
    it.each(ASSUMED_CASES)("is true for the assumed basis %s", (basis) => {
        expect(isAssumedTauBasis(basis)).toBe(true)
    })

    it.each(RECORDED_CASES)("is false for the recorded basis %s", (basis) => {
        expect(isAssumedTauBasis(basis)).toBe(false)
    })

    it("is false for protocol_not_recorded, null, undefined, and an unrecognised token", () => {
        expect(isAssumedTauBasis("protocol_not_recorded")).toBe(false)
        expect(isAssumedTauBasis(null)).toBe(false)
        expect(isAssumedTauBasis(undefined)).toBe(false)
        expect(isAssumedTauBasis("semi_numerical_v2")).toBe(false)
    })
})

// Copy-rule invariant (brief item 4): "assumed" must appear in rendered
// text whenever an assumed_* basis is rendered, and NEVER otherwise.
// Checked both directions, across every basis this module knows about.
describe("the word \"assumed\"", () => {
    const ALL_NON_ASSUMED = [
        "analytic_tight",
        "analytic_default",
        "finite_difference_gradient",
        "finite_difference_energy",
        "protocol_not_recorded",
        null,
        undefined,
        "semi_numerical_v2",
    ] as const

    it("appears in hessianMethodLabel and tauBasisNote for every assumed_* basis", () => {
        for (const [basis] of ASSUMED_CASES) {
            expect(hessianMethodLabel(basis)).toContain("assumed")
            expect(tauBasisNote(basis)).toContain("assumed")
        }
    })

    it("never appears in hessianMethodLabel or tauBasisNote for a non-assumed basis", () => {
        for (const basis of ALL_NON_ASSUMED) {
            expect(hessianMethodLabel(basis)).not.toContain("assumed")
            expect(tauBasisNote(basis)).not.toContain("assumed")
        }
    })
})

describe("basis tokens that name inherited Object properties", () => {
    // A bare `TABLE[basis]` lookup finds inherited keys, so "constructor"
    // resolved to a function and both label functions threw -- while their
    // docstrings promised they never would. Own-property lookups only.
    const inherited = ["constructor", "toString", "__proto__", "valueOf", "hasOwnProperty"]

    it("shows an inherited-property token raw instead of throwing", () => {
        for (const basis of inherited) {
            expect(() => hessianMethodLabel(basis)).not.toThrow()
            expect(() => tauBasisNote(basis)).not.toThrow()
            expect(hessianMethodLabel(basis)).toBe(basis)
            expect(tauBasisNote(basis)).toBe(basis)
        }
    })

    it("does not call an inherited-property token assumed", () => {
        for (const basis of inherited) {
            expect(isAssumedTauBasis(basis)).toBe(false)
            expect(hessianMethodLabel(basis)).not.toContain("assumed")
        }
    })
})
