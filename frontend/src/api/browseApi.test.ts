import { describe, expect, it } from "vitest"
import {
    EMPTY_BROWSE_FILTERS,
    buildReactionBrowseQuery,
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
        // Every flag set to "true" (never "false"): the seven tri-state
        // selects collapsed into checkboxes, which can only ever produce
        // "true" or "" (see `EvidenceFlagState`'s own doc comment) -- a
        // fixture using "false" no longer reflects anything the form can
        // actually produce.
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS,
            status: "optimized", hasOpt: "true", hasFreq: "true", hasSp: "true", hasIrc: "true",
            hasPathSearch: "true", hasGeometryValidation: "true", hasScfStability: "true",
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

// ---------------------------------------------------------------------------
// Structure filter (query_smiles / query_smarts / mode / similarity_threshold)
//
// "just make the struct and smiles search part of the browser-filters
// class" -- folded into BrowseFilterForm.tsx's composition fields, wired
// through here into the SAME /species/browse request every other filter
// on the page reaches. See species_browse.py / SpeciesBrowseRequest for
// the backend side of this contract.
// ---------------------------------------------------------------------------

describe("buildSpeciesBrowseQuery: structure filter params travel together, and ONLY together", () => {
    it("omits query_smiles/query_smarts/mode/similarity_threshold entirely when queryStructure is empty", () => {
        const query = buildSpeciesBrowseQuery("species", EMPTY_BROWSE_FILTERS, 0, 20)
        expect(query.has("query_smiles")).toBe(false)
        expect(query.has("query_smarts")).toBe(false)
        expect(query.has("mode")).toBe(false)
        expect(query.has("similarity_threshold")).toBe(false)
    })

    it("sends query_smiles (not query_smarts) when queryIsSmarts is false", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("query_smiles")).toBe("CCO")
        expect(query.has("query_smarts")).toBe(false)
        expect(query.get("mode")).toBe("substructure")
    })

    it("sends query_smarts (not query_smiles) when queryIsSmarts is true -- the SAME typed value, routed, never both", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "[#6]-[#8]", queryIsSmarts: true }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("query_smarts")).toBe("[#6]-[#8]")
        expect(query.has("query_smiles")).toBe(false)
    })

    it("sends the chosen mode explicitly", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO", structureMode: "exact" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("mode")).toBe("exact")
    })

    it("sends similarity_threshold only under mode=similarity, never under substructure/exact even if a value is set", () => {
        const base: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO", similarityThreshold: "0.8" }
        const substructure = buildSpeciesBrowseQuery("species", { ...base, structureMode: "substructure" }, 0, 20)
        const exact = buildSpeciesBrowseQuery("species", { ...base, structureMode: "exact" }, 0, 20)
        const similarity = buildSpeciesBrowseQuery("species", { ...base, structureMode: "similarity" }, 0, 20)
        expect(substructure.has("similarity_threshold")).toBe(false)
        expect(exact.has("similarity_threshold")).toBe(false)
        expect(similarity.get("similarity_threshold")).toBe("0.8")
    })

    it("a half-typed similarity_threshold (e.g. a trailing '.') is not sent", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO", structureMode: "similarity", similarityThreshold: "0.",
        }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.has("similarity_threshold")).toBe(false)
    })

    // The composition guarantee, at the query-string layer: an existing
    // filter (charge) and the new structure filter must BOTH reach the
    // request when both are set -- the assertion that catches a filter
    // silently dropped on the way to the wire.
    it("composes with an existing filter (charge): both reach the query string", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, charge: "1", queryStructure: "CCO" }
        const query = buildSpeciesBrowseQuery("species", filters, 0, 20)
        expect(query.get("charge")).toBe("1")
        expect(query.get("query_smiles")).toBe("CCO")
    })

    it("kind=vdw ALSO carries the structure filter -- the same builder as species", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO" }
        const query = buildSpeciesBrowseQuery("vdw", filters, 0, 20)
        expect(query.get("query_smiles")).toBe("CCO")
    })
})

describe("hasActiveFilters: a structure query counts as an active (narrowing) filter", () => {
    it("species with only queryStructure set is active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, queryStructure: "CCO" }
        expect(hasActiveFilters("species", filters)).toBe(true)
    })
})

describe("clearInapplicableFilters: the structure filter is species/vdw-only composition, cleared entering transition_state", () => {
    it("entering transition_state clears queryStructure/queryIsSmarts/structureMode/similarityThreshold", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS,
            queryStructure: "CCO", queryIsSmarts: true, structureMode: "similarity", similarityThreshold: "0.8",
        }
        const cleared = clearInapplicableFilters("transition_state", filters)
        expect(cleared.queryStructure).toBe("")
        expect(cleared.queryIsSmarts).toBe(false)
        expect(cleared.structureMode).toBe("substructure")
        expect(cleared.similarityThreshold).toBe("")
    })
})

// ---------------------------------------------------------------------------
// PR 4b: the "reaction" browse kind -- /scientific/reactions/browse.
// Verified live 2026-09-07: the endpoint accepts EXACTLY `family,
// reactant_smiles, product_smiles, has_kinetics, has_transition_state,
// min_review_status, include_rejected, include_deprecated, offset, limit`
// -- no charge, multiplicity, or provenance params, unlike the other three
// kinds. Every test below is written against that measured contract, not
// an assumed mirror of `buildSpeciesBrowseQuery`.
// ---------------------------------------------------------------------------

describe("buildReactionBrowseQuery: only the params the live endpoint accepts are ever sent", () => {
    it("an entirely empty filter set sends only offset and limit", () => {
        const query = buildReactionBrowseQuery(EMPTY_BROWSE_FILTERS, 0, 20)
        expect([...query.keys()].sort()).toEqual(["limit", "offset"])
    })

    it("family is sent when set", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, family: "H_Abstraction" }
        const query = buildReactionBrowseQuery(filters, 0, 20)
        expect(query.get("family")).toBe("H_Abstraction")
    })

    it("reactant_smiles and product_smiles are sent as TWO separate params, each only when set", () => {
        const reactantOnly = buildReactionBrowseQuery({ ...EMPTY_BROWSE_FILTERS, reactantSmiles: "CCO" }, 0, 20)
        expect(reactantOnly.get("reactant_smiles")).toBe("CCO")
        expect(reactantOnly.has("product_smiles")).toBe(false)

        const productOnly = buildReactionBrowseQuery({ ...EMPTY_BROWSE_FILTERS, productSmiles: "CC=O" }, 0, 20)
        expect(productOnly.get("product_smiles")).toBe("CC=O")
        expect(productOnly.has("reactant_smiles")).toBe(false)

        const both = buildReactionBrowseQuery({ ...EMPTY_BROWSE_FILTERS, reactantSmiles: "CCO", productSmiles: "CC=O" }, 0, 20)
        expect(both.get("reactant_smiles")).toBe("CCO")
        expect(both.get("product_smiles")).toBe("CC=O")
    })

    it("has_kinetics and has_transition_state are sent as \"true\" when checked, omitted when \"\"", () => {
        const query = buildReactionBrowseQuery({ ...EMPTY_BROWSE_FILTERS, hasKinetics: "true" }, 0, 20)
        expect(query.get("has_kinetics")).toBe("true")
        expect(query.has("has_transition_state")).toBe(false)
    })

    it("min_review_status, include_rejected, include_deprecated all reach the wire", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, minReviewStatus: "approved", includeRejected: true, includeDeprecated: true,
        }
        const query = buildReactionBrowseQuery(filters, 0, 20)
        expect(query.get("min_review_status")).toBe("approved")
        expect(query.get("include_rejected")).toBe("true")
        expect(query.get("include_deprecated")).toBe("true")
    })

    // The endpoint has no charge/multiplicity/provenance axis (measured
    // live) -- `buildReactionBrowseQuery` must never send them even if a
    // stale value is sitting in `filters` from a prior species/TS session
    // (the field is simply not read by this builder, unlike
    // `sharedQueryParams`'s callers).
    it("charge, multiplicity, and the six provenance fields are NEVER sent, even when set on the filters object", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS,
            charge: "0", multiplicity: "1", method: "b3lyp", basis: "def2tzvp",
            software: "Gaussian", softwareVersion: "16", workflowTool: "ARC", workflowToolVersion: "1.2.0",
        }
        const query = buildReactionBrowseQuery(filters, 0, 20)
        for (const param of ["charge", "multiplicity", "method", "basis", "software", "software_version", "workflow_tool", "workflow_tool_version"]) {
            expect(query.has(param)).toBe(false)
        }
    })

    it("offset and limit are always sent, even when 0", () => {
        const query = buildReactionBrowseQuery(EMPTY_BROWSE_FILTERS, 0, 20)
        expect(query.get("offset")).toBe("0")
        expect(query.get("limit")).toBe("20")
    })

    it("composes: family + reactant_smiles + has_kinetics all reach the same query string together", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, family: "H_Abstraction", reactantSmiles: "CCO", hasKinetics: "true",
        }
        const query = buildReactionBrowseQuery(filters, 40, 20)
        expect(query.get("family")).toBe("H_Abstraction")
        expect(query.get("reactant_smiles")).toBe("CCO")
        expect(query.get("has_kinetics")).toBe("true")
        expect(query.get("offset")).toBe("40")
    })
})

describe("hasActiveFilters: \"reaction\" only counts the fields the endpoint actually reads", () => {
    it("empty filters: not active", () => {
        expect(hasActiveFilters("reaction", EMPTY_BROWSE_FILTERS)).toBe(false)
    })

    it("each of family/reactantSmiles/productSmiles/hasKinetics/hasTransitionState/minReviewStatus activates it individually", () => {
        const fields: [keyof BrowseFilters, string][] = [
            ["family", "H_Abstraction"], ["reactantSmiles", "CCO"], ["productSmiles", "CC=O"],
            ["hasKinetics", "true"], ["hasTransitionState", "true"], ["minReviewStatus", "approved"],
        ]
        for (const [field, value] of fields) {
            const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, [field]: value }
            expect(hasActiveFilters("reaction", filters), `field ${field} did not activate reaction`).toBe(true)
        }
    })

    // Mirrors the existing species/vdw/transition_state widening-toggle
    // rule: include_rejected/include_deprecated only ever WIDEN a result
    // set, so they must never be the reason an empty listing is reported
    // as "filters excluded everything".
    it("includeRejected/includeDeprecated alone do NOT count as active", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, includeRejected: true, includeDeprecated: true }
        expect(hasActiveFilters("reaction", filters)).toBe(false)
    })

    // A stale species/TS-scoped value (charge, method, ...) must not read
    // as an active reaction filter -- `/reactions/browse` never sees it,
    // so reporting it as active would make the empty-state copy lie about
    // why the listing came back empty.
    it("charge/multiplicity/the six provenance fields do NOT count as active for kind=reaction, even when set", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, charge: "0", multiplicity: "1", method: "b3lyp", software: "Gaussian",
        }
        expect(hasActiveFilters("reaction", filters)).toBe(false)
    })
})

describe("clearInapplicableFilters: the reaction-only fields and the shared family field", () => {
    it("leaving \"reaction\" clears reactantSmiles/productSmiles/hasKinetics/hasTransitionState", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, reactantSmiles: "CCO", productSmiles: "CC=O", hasKinetics: "true", hasTransitionState: "true",
        }
        const cleared = clearInapplicableFilters("species", filters)
        expect(cleared.reactantSmiles).toBe("")
        expect(cleared.productSmiles).toBe("")
        expect(cleared.hasKinetics).toBe("")
        expect(cleared.hasTransitionState).toBe("")
    })

    it("entering \"reaction\" clears the species-only composition fields", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, formula: "C6H6", elements: "C,H", minHeavyAtoms: "1" }
        const cleared = clearInapplicableFilters("reaction", filters)
        expect(cleared.formula).toBe("")
        expect(cleared.elements).toBe("")
        expect(cleared.minHeavyAtoms).toBe("")
    })

    it("entering \"reaction\" clears the transition-state-only evidence fields (status/has_*/participantSmiles)", () => {
        const filters: BrowseFilters = {
            ...EMPTY_BROWSE_FILTERS, status: "optimized", hasOpt: "true", participantSmiles: "CCO",
        }
        const cleared = clearInapplicableFilters("reaction", filters)
        expect(cleared.status).toBe("")
        expect(cleared.hasOpt).toBe("")
        expect(cleared.participantSmiles).toBe("")
    })

    it("leaving \"transition_state\" for \"reaction\" clears reactantSmiles/hasKinetics that never applied there in the first place (no-op, but must not error or leak stale values back in)", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, participantSmiles: "CCO" }
        const cleared = clearInapplicableFilters("reaction", filters)
        expect(cleared.reactantSmiles).toBe("")
        expect(cleared.hasKinetics).toBe("")
    })

    // `family` is the ONE field shared between "transition_state" and
    // "reaction" -- both narrow through the same `/meta/reaction-families`
    // vocabulary -- so it must survive a switch between those two, in
    // BOTH directions, while still being cleared on a switch to
    // "species"/"vdw" (neither of which has any family axis at all).
    it("family SURVIVES a transition_state <-> reaction switch, in both directions", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, family: "H_Abstraction" }
        expect(clearInapplicableFilters("reaction", filters).family).toBe("H_Abstraction")
        expect(clearInapplicableFilters("transition_state", filters).family).toBe("H_Abstraction")
    })

    it("family is CLEARED on a switch to species/vdw from either transition_state or reaction", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, family: "H_Abstraction" }
        expect(clearInapplicableFilters("species", filters).family).toBe("")
        expect(clearInapplicableFilters("vdw", filters).family).toBe("")
    })
})

// Review follow-up (round 2), NIT: the six provenance fields are invisible
// on "reaction" (see `BrowseFilterForm`'s `!isReaction` guards) but not
// cleared by a switch through it -- a decision, not an oversight, recorded
// in `clearInapplicableFilters`'s own comment: the module's established
// rule for these six fields ("only ever cleared explicitly by a reader
// picking 'Any'") already applies uniformly, and this test pins that
// choice so a future edit that quietly starts clearing them on "reaction"
// (or stops clearing them on species/vdw/transition_state) is a visible,
// deliberate diff here rather than a silent behavior change.
describe("clearInapplicableFilters: the six provenance fields round-trip THROUGH reaction (documented choice, not an oversight)", () => {
    it("a provenance value set on transition_state survives a switch to reaction (where it is hidden) and back to species (where it re-applies)", () => {
        const filters: BrowseFilters = { ...EMPTY_BROWSE_FILTERS, method: "b3lyp", software: "Gaussian" }
        const throughReaction = clearInapplicableFilters("reaction", filters)
        expect(throughReaction.method).toBe("b3lyp")
        expect(throughReaction.software).toBe("Gaussian")
        const backToSpecies = clearInapplicableFilters("species", throughReaction)
        expect(backToSpecies.method).toBe("b3lyp")
        expect(backToSpecies.software).toBe("Gaussian")
    })
})
