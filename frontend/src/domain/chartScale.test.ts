import { describe, expect, it } from "vitest"
import { domainWithPadding, evenTicks, formatTick, linearScale } from "./chartScale"

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

describe("formatTick", () => {
    it("scales precision by magnitude", () => {
        expect(formatTick(0)).toBe("0")
        expect(formatTick(1234)).toBe("1234")
        expect(formatTick(42.567)).toBe("42.6")
        expect(formatTick(4.5678)).toBe("4.57")
        expect(formatTick(-3.14159)).toBe("-3.14")
    })
})
