import { describe, expect, it } from "vitest"
import {
    EMPTY_BROWSE_FILTERS,
    buildSpeciesBrowseQuery,
    buildTransitionStateBrowseQuery,
    clearInapplicableFilters,
    hasActiveFilters,
} from "./browseApi"
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

// The mirror-image bug the design brief calls out by name: `hasActiveFilters`
// used to check the six provenance fields ONLY when `kind === "transition_state"`,
// so a species query with just `method` set would report "no filters active"
// -- collapsing a genuine narrowing filter into the archive-empty branch
// ("nothing of this kind has been deposited") instead of the filtered-empty
// one ("filters excluded everything"). `/species/browse` answers `method`
// (see `buildSpeciesBrowseQuery` below), so this must be `true` on every kind.
describe("hasActiveFilters: the six provenance fields count as active on EVERY kind, not just transition_state", () => {
    it("species with only `method` set is active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, method: "b3lyp" }
        expect(hasActiveFilters("species", filters)).toBe(true)
    })

    it("vdw with only `software` set is active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, software: "Gaussian" }
        expect(hasActiveFilters("vdw", filters)).toBe(true)
    })

    it("transition_state with only `workflowTool` set is (still) active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, workflowTool: "ARC" }
        expect(hasActiveFilters("transition_state", filters)).toBe(true)
    })

    it("every one of the six provenance fields, checked individually, activates species", () => {
        const fields: (keyof BrowseFilters)[] = ["method", "basis", "software", "softwareVersion", "workflowTool", "workflowToolVersion"]
        for (const field of fields) {
            const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, [field]: "x" }
            expect(hasActiveFilters("species", filters), `field ${field} did not activate species`).toBe(true)
        }
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

// `/species/browse` (species_browse.py) accepts the same six provenance
// parameters as `/transition-states/browse` -- method/basis/
// software(+version)/workflow_tool(+version) -- but `buildSpeciesBrowseQuery`
// used to only ever emit the five composition/shared params, dropping all
// six on the floor regardless of what `BrowseFilterForm` collected. Checked
// on BOTH "species" and "vdw" (the same builder, see its own doc comment)
// since a fix scoped to only one of the two kind literals would still leave
// the other silently broken.
describe("buildSpeciesBrowseQuery: the six provenance params reach the wire", () => {
    const filledProvenance: Partial<BrowseFilters> = {
        method: "b3lyp", basis: "def2tzvp", software: "Gaussian", softwareVersion: "16",
        workflowTool: "ARC", workflowToolVersion: "1.2.0",
    }

    it.each(["species", "vdw"] as const)("kind=%s: all six provenance params are set", (kind) => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, ...filledProvenance }
        const query = buildSpeciesBrowseQuery(kind, filters, 0, 20)
        expect(query.get("method")).toBe("b3lyp")
        expect(query.get("basis")).toBe("def2tzvp")
        expect(query.get("software")).toBe("Gaussian")
        expect(query.get("software_version")).toBe("16")
        expect(query.get("workflow_tool")).toBe("ARC")
        expect(query.get("workflow_tool_version")).toBe("1.2.0")
    })

    it("none of the six provenance params are sent when unset", () => {
        const query = buildSpeciesBrowseQuery("species", EMPTY_BROWSE_FILTERS, 0, 20)
        for (const param of ["method", "basis", "software", "software_version", "workflow_tool", "workflow_tool_version"]) {
            expect(query.has(param)).toBe(false)
        }
    })
})

describe("clearInapplicableFilters: the six provenance fields apply to every kind, so they are never cleared by a kind switch", () => {
    const filledProvenance: Partial<BrowseFilters> = {
        method: "b3lyp", basis: "def2tzvp", software: "Gaussian", softwareVersion: "16",
        workflowTool: "ARC", workflowToolVersion: "1.2.0",
    }

    it("species -> transition_state: provenance survives", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, ...filledProvenance }
        const cleared = clearInapplicableFilters("transition_state", filters)
        expect(cleared.method).toBe("b3lyp")
        expect(cleared.basis).toBe("def2tzvp")
        expect(cleared.software).toBe("Gaussian")
        expect(cleared.softwareVersion).toBe("16")
        expect(cleared.workflowTool).toBe("ARC")
        expect(cleared.workflowToolVersion).toBe("1.2.0")
    })

    it("transition_state -> species: provenance ALSO survives (both directions)", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, ...filledProvenance }
        const cleared = clearInapplicableFilters("species", filters)
        expect(cleared.method).toBe("b3lyp")
        expect(cleared.basis).toBe("def2tzvp")
        expect(cleared.software).toBe("Gaussian")
        expect(cleared.softwareVersion).toBe("16")
        expect(cleared.workflowTool).toBe("ARC")
        expect(cleared.workflowToolVersion).toBe("1.2.0")
    })

    it("transition_state -> species ALSO survives via vdw", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, ...filledProvenance }
        const cleared = clearInapplicableFilters("vdw", filters)
        expect(cleared.method).toBe("b3lyp")
        expect(cleared.software).toBe("Gaussian")
    })

    it("leaving transition_state clears status and the seven has_* evidence flags", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS,
            status: "optimized", hasOpt: "true", hasFreq: "false", hasSp: "true", hasIrc: "true",
            hasPathSearch: "false", hasGeometryValidation: "true", hasScfStability: "false",
        }
        const cleared = clearInapplicableFilters("species", filters)
        expect(cleared.status).toBe("")
        expect(cleared.hasOpt).toBe("")
        expect(cleared.hasFreq).toBe("")
        expect(cleared.hasSp).toBe("")
        expect(cleared.hasIrc).toBe("")
        expect(cleared.hasPathSearch).toBe("")
        expect(cleared.hasGeometryValidation).toBe("")
        expect(cleared.hasScfStability).toBe("")
    })

    it("entering transition_state still clears the species-only composition fields", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, formula: "C6H6", elements: "C,H", minHeavyAtoms: "1" }
        const cleared = clearInapplicableFilters("transition_state", filters)
        expect(cleared.formula).toBe("")
        expect(cleared.elements).toBe("")
        expect(cleared.minHeavyAtoms).toBe("")
    })
})

// `buildTransitionStateBrowseQuery` must keep sending the same six
// provenance params after the refactor that shares `applyProvenanceParams`
// with `buildSpeciesBrowseQuery` -- this is the "did I break the thing
// that already worked" counterpart to the species-side test above.
describe("buildTransitionStateBrowseQuery: the six provenance params still reach the wire", () => {
    it("all six provenance params are set", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS,
            method: "b3lyp", basis: "def2tzvp", software: "Gaussian", softwareVersion: "16",
            workflowTool: "ARC", workflowToolVersion: "1.2.0",
        }
        const query = buildTransitionStateBrowseQuery(filters, 0, 20)
        expect(query.get("method")).toBe("b3lyp")
        expect(query.get("basis")).toBe("def2tzvp")
        expect(query.get("software")).toBe("Gaussian")
        expect(query.get("software_version")).toBe("16")
        expect(query.get("workflow_tool")).toBe("ARC")
        expect(query.get("workflow_tool_version")).toBe("1.2.0")
    })
})
