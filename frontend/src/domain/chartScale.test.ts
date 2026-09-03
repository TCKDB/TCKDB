import { describe, expect, it } from "vitest"
import { domainWithPadding, evenTicks, formatTicks, linearScale } from "./chartScale"

describe("linearScale", () => {
    it("maps the domain endpoints onto the range endpoints", () => {
        const scale = linearScale([0, 100], [10, 210])
        expect(scale(0)).toBe(10)
        expect(scale(100)).toBe(210)
        expect(scale(50)).toBe(110)
    })

    it("never divides by zero on a degenerate (single-point) domain", () => {
        const scale = linearScale([5, 5], [0, 100])
        expect(scale(5)).toBe(50)
        expect(Number.isFinite(scale(5))).toBe(true)
    })
})

describe("domainWithPadding", () => {
    it("pads a real range on both sides", () => {
        const [min, max] = domainWithPadding([10, 20], 0.1)
        expect(min).toBeLessThan(10)
        expect(max).toBeGreaterThan(20)
    })

    it("returns [0, 1] for an empty input", () => {
        expect(domainWithPadding([])).toEqual([0, 1])
    })

    it("never returns a zero-span domain when every value is identical", () => {
        const [min, max] = domainWithPadding([7, 7, 7])
        expect(max).toBeGreaterThan(min)
    })
})

describe("evenTicks", () => {
    it("returns `count` evenly spaced values including both endpoints", () => {
        expect(evenTicks([0, 100], 5)).toEqual([0, 25, 50, 75, 100])
    })
})

describe("formatTicks", () => {
    it("uses one shared decimal precision for every tick on the axis, chosen from the axis's own step", () => {
        // Step 0.5 straddles the OLD per-value magnitude threshold at 10: a
        // prior version of this function chose precision per VALUE (2
        // decimals below 10, 1 decimal at/above 10), which rendered this
        // exact evenly spaced axis as "8.50, 9.00, 9.50, 10.0, 10.5" -- a
        // review finding ("one axis can read 8.50, 10.0, 100"). Every tick
        // here must carry the SAME one-decimal precision instead.
        expect(formatTicks([8.5, 9, 9.5, 10, 10.5])).toEqual(["8.5", "9.0", "9.5", "10.0", "10.5"])
    })

    it("needs no decimals when the step is a whole number", () => {
        expect(formatTicks([0, 25, 50, 75, 100])).toEqual(["0", "25", "50", "75", "100"])
        expect(formatTicks([0, 1, 2, 3])).toEqual(["0", "1", "2", "3"])
    })

    it("formats a zero-valued tick at the axis's own precision, not as a bare '0'", () => {
        // 0 is still just one tick among the others -- it must not fall
        // back to unpadded "0" while its neighbours carry decimals.
        expect(formatTicks([0, 0.25, 0.5])).toEqual(["0.0", "0.3", "0.5"])
    })

    it("falls back to whole numbers rather than NaN/Infinity on a degenerate (single-tick or zero-span) axis", () => {
        expect(formatTicks([5])).toEqual(["5"])
        expect(formatTicks([7, 7, 7])).toEqual(["7", "7", "7"])
    })

    it("returns no labels for no ticks", () => {
        expect(formatTicks([])).toEqual([])
    })

    it("keeps the negative sign on a negative tick", () => {
        expect(formatTicks([-3, 0, 3])).toEqual(["-3", "0", "3"])
    })
})
