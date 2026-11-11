import { describe, expect, it } from "vitest"
import { MIN_SPAN_KJ_MOL, profileScale } from "./energyProfile"

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
