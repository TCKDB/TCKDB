import { describe, expect, it } from "vitest"
import type { EnergyProfileRecord } from "../api/reactionEnergyProfileApi"
import { MIN_SPAN_KJ_MOL, mixesSpin, profileLabels, profileScale, spinGroups } from "./energyProfile"

describe("profileScale", () => {
    it("is at least the minimum span tall however small the spread, and keeps zero on the axis", () => {
        const scale = profileScale([0, 3, -2])
        expect(scale.max - scale.min).toBeGreaterThanOrEqual(MIN_SPAN_KJ_MOL)
        expect(scale.ticks).toContain(0)
    })

    it("pads to round tick values and keeps every value inside the axis", () => {
        const values = [0, 84.3, -262.5]
        const scale = profileScale(values)
        expect(scale.min).toBeLessThanOrEqual(-262.5)
        expect(scale.max).toBeGreaterThanOrEqual(84.3)
        expect(scale.ticks.length).toBeGreaterThanOrEqual(3)
        expect(scale.ticks.length).toBeLessThanOrEqual(6)
        const step = scale.ticks[1] - scale.ticks[0]
        expect(scale.ticks.every((tick, index) => Math.abs(tick - (scale.min + index * step)) < 1e-6)).toBe(true)
    })

    it("is linear: equal energy gaps are equal tick gaps", () => {
        const scale = profileScale([0, 40, -40])
        const gaps = scale.ticks.slice(1).map((tick, index) => tick - scale.ticks[index])
        expect(new Set(gaps.map((gap) => gap.toFixed(6))).size).toBe(1)
    })
})

describe("spinGroups", () => {
    const at = (spin: string) => ({ level_of_theory: { spin_treatment: spin } })
    const profile = (spins: (string | null)[]) =>
        ({
            spin_treatments: spins,
            reactants: [{ species_entry_ref: "a", ...at("restricted") }, { species_entry_ref: "b", ...at("unrestricted") }],
            transition_state: at("unrestricted"),
            products: [{ species_entry_ref: "c", ...at("unrestricted") }],
        }) as unknown as EnergyProfileRecord

    it("groups points by the spin treatment they ran with, in the profile's order", () => {
        const groups = spinGroups(profile(["restricted", "unrestricted"]), (ref) => ref.toUpperCase(), "TS")
        expect(groups).toEqual([
            { spin: "restricted", points: ["A"] },
            { spin: "unrestricted", points: ["B", "TS", "C"] },
        ])
    })

    it("is empty for a profile with one spin treatment", () => {
        expect(mixesSpin(profile(["unrestricted"]))).toBe(false)
        expect(spinGroups(profile(["unrestricted"]), (ref) => ref, "TS")).toEqual([])
    })
})

describe("profileLabels", () => {
    const level = { level_of_theory_ref: "lot_x", method: "wb97xd", basis: "def2tzvp", spin_treatment: null }
    const point = (ref: string, spin: string) => ({ species_entry_ref: ref, level_of_theory: { ...level, level_of_theory_ref: `lot_${spin}`, spin_treatment: spin } })
    const profile = (radicalSpin: string, extra: Partial<EnergyProfileRecord> = {}) =>
        ({
            transition_state_entry_ref: "tse_one",
            level_of_theory: level,
            program: "orca",
            reactants: [point("closed", "restricted"), point("radical", radicalSpin)],
            transition_state: { level_of_theory: level },
            products: [point("radical_p", radicalSpin)],
            ...extra,
        }) as unknown as EnergyProfileRecord

    it("names the radicals' spin treatment when profiles share transition state, level and program", () => {
        const labels = profileLabels([profile("restricted_open"), profile("unrestricted")])
        expect(labels).toEqual([
            "wb97xd/def2tzvp, orca, restricted open radicals",
            "wb97xd/def2tzvp, orca, unrestricted radicals",
        ])
    })

    it("leaves a profile that is the only one at its level and program as it was", () => {
        const labels = profileLabels([profile("unrestricted"), profile("restricted_open", { program: "gaussian" })])
        expect(labels).toEqual(["wb97xd/def2tzvp, orca", "wb97xd/def2tzvp, gaussian"])
    })
})
