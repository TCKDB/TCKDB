import { describe, expect, it } from "vitest"
import type { RelativeEnergies } from "../api/relativeEnergyApi"
import { cardEnergy, formatHartree, formatRelativeKj, type ProgramIndex } from "./conformerGallery"

// The backend's closed set of reasons a shape has no usable value (`REASONS` in relative_conformer_energy/models.py).
const BACKEND_REASONS = [
    "no_structure_determination", "no_determination_supplies_requested_kind", "no_value_at_requested_level", "recipe_not_established",
    "recipe_differs_from_request", "not_eligible", "certificate_not_supported", "certificate_not_on_energy_surface",
    "contested_certificate", "repeat_values_disagree", "multiple_recipes_at_level", "group_below_review_floor",
    "group_terminal_review_status", "determination_below_review_floor", "determination_terminal_review_status",
]

function answer(member: Record<string, unknown>, referenceRefs = ["calc_ref"]): RelativeEnergies {
    return {
        outcome: "relative_energies",
        reference: { conformer_ref: "cg_ref", status: "reference" },
        members: [
            { conformer_ref: "cg_ref", status: "reference", energy_calculation_refs: referenceRefs, alternates: [], detail: [] },
            { conformer_ref: "cg_m", energy_calculation_refs: [], alternates: [], detail: [], ...member },
        ],
    } as unknown as RelativeEnergies
}

const programs: ProgramIndex = new Map([["calc_ref", "Gaussian 16"], ["calc_g", "Gaussian 16"], ["calc_o", "ORCA 6.0"], ["calc_none", null]])

describe("cardEnergy", () => {
    it("words every reason the archive can give, and says no value only for the two codes that mean it", () => {
        for (const code of BACKEND_REASONS) {
            const energy = cardEnergy(answer({ status: "absent", absent_reason: code }), "cg_m", programs)
            expect(energy.kind === "note" || energy.kind === "disagree", code).toBe(true)
            if (energy.kind === "note") {
                expect(energy.text, code).not.toMatch(/reason this page does not word/)
                const saysNoValue = energy.text === "No value at this level."
                expect(saysNoValue, code).toBe(code === "no_structure_determination" || code === "no_value_at_requested_level")
            }
        }
    })

    it("shows a number only when the shape and the reference each have one known program and it is the same", () => {
        const same = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_g"] }), "cg_m", programs)
        expect(same).toMatchObject({ kind: "value", program: "Gaussian 16" })
        const other = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_o"] }), "cg_m", programs)
        expect(other).toEqual({
            kind: "note",
            text: "Computed with ORCA 6.0; the reference with Gaussian 16. Energies from different programs are not compared.",
        })
        const unknownText = {
            kind: "note",
            text: "The program of this value or of the reference is not known to this page, so no difference is shown. Energies from different programs are not compared.",
        }
        const unknown = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_none"] }), "cg_m", programs)
        expect(unknown).toEqual(unknownText)
        // One calculation with a known program and one without: no number.
        const partlyKnown = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_g", "calc_none"] }), "cg_m", programs)
        expect(partlyKnown).toEqual(unknownText)
        const mixed = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_g", "calc_o"] }), "cg_m", programs)
        expect(mixed.kind).toBe("note")
        const noReferenceProgram = cardEnergy(answer({ status: "present", relative_energy_kj_mol: 1.5, energy_calculation_refs: ["calc_g"] }, ["calc_none"]), "cg_m", programs)
        expect(noReferenceProgram.kind).toBe("note")
    })

    it("reports disagreement only for repeat_values_disagree", () => {
        const disagree = cardEnergy(answer({ status: "unresolved", absent_reason: "repeat_values_disagree", alternates: [{ determination_ref: "d", value_hartree: -1 }] }), "cg_m", programs)
        expect(disagree).toEqual({ kind: "disagree", values: [{ hartree: -1, determinationRef: "d" }] })
        const recipes = cardEnergy(answer({ status: "unresolved", absent_reason: "multiple_recipes_at_level" }), "cg_m", programs)
        expect(recipes).toEqual({ kind: "note", text: "More than one recipe at this level; not compared." })
    })

    it("words a present value with no difference by whether the reference had none", () => {
        const noReference = cardEnergy(answer({ status: "present", relative_energy_kj_mol: null, detail: ["reference_unavailable"] }), "cg_m", programs)
        expect(noReference).toEqual({ kind: "note", text: "Has a value at this level; the reference has none, so no difference is shown." })
    })
})

describe("number formats", () => {
    it("uses a true minus sign", () => {
        expect(formatHartree(-76.5)).toBe("−76.500000")
        expect(formatRelativeKj(-1.234)).toBe("−1.23")
        expect(formatRelativeKj(0.001)).toBe("0.00")
    })
})
