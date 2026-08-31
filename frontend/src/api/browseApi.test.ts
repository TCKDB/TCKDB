import { describe, expect, it } from "vitest"
import { EMPTY_BROWSE_FILTERS, buildSpeciesBrowseQuery, hasActiveFilters } from "./browseApi"
import type { BrowseFilters } from "./browseApi"

/**
 * Direct unit coverage for the pure query-building/filter-state functions
 * behind `BrowsePage` -- fast, precise regression tests for the two fixes
 * that are otherwise only reachable indirectly through the async UI suite
 * in `pages/BrowsePage.test.tsx`.
 */

describe("hasActiveFilters: widening toggles never count as a narrowing filter", () => {
    it("includeRejected/includeDeprecated alone do NOT count as active -- they widen, they never narrow", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, includeRejected: true, includeDeprecated: true }
        expect(hasActiveFilters("species", filters)).toBe(false)
        expect(hasActiveFilters("vdw", filters)).toBe(false)
        expect(hasActiveFilters("transition_state", filters)).toBe(false)
    })

    it("a genuine narrowing filter (charge) still counts as active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, charge: "0" }
        expect(hasActiveFilters("species", filters)).toBe(true)
    })
})

describe("buildSpeciesBrowseQuery: an incomplete integer never reaches the wire", () => {
    it("a lone '-' (the first keystroke of any anion charge) is not sent", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, charge: "-" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.has("charge")).toBe(false)
    })

    it("a complete negative integer IS sent", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, charge: "-1" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("charge")).toBe("-1")
    })

    it("a non-numeric multiplicity/min-heavy-atoms/max-heavy-atoms value is not sent", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, multiplicity: "2x", minHeavyAtoms: "-", maxHeavyAtoms: "3.5" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.has("multiplicity")).toBe(false)
        expect(query.has("min_heavy_atoms")).toBe(false)
        expect(query.has("max_heavy_atoms")).toBe(false)
    })

    it("complete integers for multiplicity/min/max-heavy-atoms ARE sent", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, multiplicity: "2", minHeavyAtoms: "1", maxHeavyAtoms: "10" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("multiplicity")).toBe("2")
        expect(query.get("min_heavy_atoms")).toBe("1")
        expect(query.get("max_heavy_atoms")).toBe("10")
    })

    it("sends collapse=all explicitly rather than relying on the server default", () => {
        const query = buildSpeciesBrowseQuery("species", EMPTY_BROWSE_FILTERS, 0, 20)
        expect(query.get("collapse")).toBe("all")
    })
})
