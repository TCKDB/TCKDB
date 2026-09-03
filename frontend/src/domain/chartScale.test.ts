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
    it("uses one shared decimal precision for every tick on the axis (the {1,2,5}×10^n step niceTicks actually produces)", () => {
        // Step 0.5 straddles the OLD per-value magnitude threshold at 10: a
        // prior version of this function chose precision per VALUE (2
        // decimals below 10, 1 decimal at/above 10), which rendered this
        // exact evenly spaced axis as "8.50, 9.00, 9.50, 10.0, 10.5" -- a
        // review finding ("one axis can read 8.50, 10.0, 100"). Every tick
        // here must carry the SAME one-decimal precision instead.
        expect(formatTicks([8.5, 9, 9.5, 10, 10.5])).toEqual(["8.5", "9.0", "9.5", "10.0", "10.5"])
    })

    it("needs no decimals when every tick is already a whole number", () => {
        expect(formatTicks([0, 25, 50, 75, 100])).toEqual(["0", "25", "50", "75", "100"])
        expect(formatTicks([0, 1, 2, 3])).toEqual(["0", "1", "2", "3"])
    })

    // A step-from-log10 approach (an earlier version of this function) is
    // only correct for a {1, 2, 5}×10^n step -- for the 0.25 step below it
    // computed `-floor(log10(0.25))` = 1 decimal, which does NOT round-trip
    // 0.25 or 0.75 (0.25.toFixed(1) is "0.3", a real precision loss, not a
    // display nicety) and printed "0.0, 0.3, 0.5, 0.8, 1.0" for this exact
    // tick set. Every tick here must round-trip through its own label.
    it("finds enough decimals for a NON-{1,2,5} step (0.25) so every tick round-trips exactly", () => {
        expect(formatTicks([0, 0.25, 0.5, 0.75, 1.0])).toEqual(["0.00", "0.25", "0.50", "0.75", "1.00"])
    })

    // Same defect, a different non-{1,2,5} step: `-floor(log10(2.5))` = 0
    // decimals, which rounded 2.5/7.5 to whole numbers -- "0, 3, 5, 8, 10"
    // for this exact tick set, printing ticks that were never plotted.
    it("finds enough decimals for a NON-{1,2,5} step (2.5) so every tick round-trips exactly", () => {
        expect(formatTicks([0, 2.5, 5, 7.5, 10])).toEqual(["0.0", "2.5", "5.0", "7.5", "10.0"])
    })

    // The same 2.5 step, entirely below zero: the step-from-log10 approach
    // rounded -7.5 to "-8" (a lossier, wrong-sign-adjacent label for a
    // value that was never -8). The round-trip check has no sign blind
    // spot -- it checks each tick, including negative ones, against itself.
    it("finds enough decimals for negative ticks so every tick round-trips exactly, never rounding one to the wrong integer", () => {
        expect(formatTicks([-7.5, -5, -2.5, 0, 2.5])).toEqual(["-7.5", "-5.0", "-2.5", "0.0", "2.5"])
    })

    it("formats a zero-valued tick at the axis's own precision, not as a bare '0'", () => {
        // 0 is still just one tick among the others -- it must not fall
        // back to unpadded "0" while its neighbours carry decimals.
        expect(formatTicks([0, 0.25, 0.5])).toEqual(["0.00", "0.25", "0.50"])
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

    it("never throws on a NaN or ±Infinity tick, and never lets one force excess precision onto the finite ticks sharing its axis", () => {
        expect(() => formatTicks([0, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 5])).not.toThrow()
        expect(formatTicks([0, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 5]))
            .toEqual(["0", "NaN", "Infinity", "-Infinity", "5"])
        // Every tick non-finite: nothing to derive precision FROM, so it
        // falls back to 0 decimals rather than searching forever or throwing.
        expect(() => formatTicks([Number.NaN, Number.POSITIVE_INFINITY])).not.toThrow()
        expect(formatTicks([Number.NaN, Number.POSITIVE_INFINITY])).toEqual(["NaN", "Infinity"])
    })
})
