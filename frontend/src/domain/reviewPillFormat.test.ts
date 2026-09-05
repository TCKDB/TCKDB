import { describe, expect, it } from "vitest"
import { reviewPillClass } from "./reviewPillFormat"

describe("reviewPillClass", () => {
    it("returns the muted pill pair for 'not_reviewed'", () => {
        expect(reviewPillClass("not_reviewed")).toBe("value-pill value-pill--muted")
    })

    it("returns the plain accent pill for every other status", () => {
        for (const status of ["reviewed", "approved", "rejected", "well_supported"]) {
            expect(reviewPillClass(status)).toBe("value-pill")
        }
    })

    // Mutation check: always including "value-pill" (never bare
    // "value-pill--muted") matters -- PR D moved `.value-pill--muted`'s
    // selector to the compound `.value-pill.value-pill--muted`, so a
    // caller carrying only the muted class renders unstyled.
    it("always includes the base 'value-pill' class, even for the muted case", () => {
        expect(reviewPillClass("not_reviewed").split(" ")).toContain("value-pill")
    })
})
