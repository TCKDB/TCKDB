import { describe, expect, it } from "vitest"
import { buildCalculationsByRef, buildDependencyEdgesByChildRef, deriveKineticsLevelsFallback } from "./reactionKineticsLevels"
import type { ReactionFullCalculationEvidence, ReactionKineticsRecord, ReactionTransitionStateInFull } from "../api/reactionEntryApi"

const b3lyp = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }
const ccsdt = { method: "ccsd(t)", basis: "cc-pvtz", display: "ccsd(t)/cc-pvtz" }

function calc(ref: string, type: string, level: unknown = b3lyp): ReactionFullCalculationEvidence {
    return { calculation_ref: ref, calculation_type: type, level_of_theory: level } as ReactionFullCalculationEvidence
}

function ts(dependencies: { parent_calculation_ref: string; child_calculation_ref: string; role: string }[]): Pick<ReactionTransitionStateInFull, "dependencies"> {
    return { dependencies } as Pick<ReactionTransitionStateInFull, "dependencies">
}

function provenance(overrides: Partial<ReactionKineticsRecord["provenance"]> = {}): ReactionKineticsRecord["provenance"] {
    return {
        transition_state_entry_ref: null,
        ts_opt_calculation_ref: null,
        ts_freq_calculation_ref: null,
        ts_sp_calculation_ref: null,
        primary_level_of_theory: null,
        primary_software: null,
        software_release: null,
        workflow_tool_release: null,
        network_kinetics_ref: null,
        ...overrides,
    } as ReactionKineticsRecord["provenance"]
}

describe("buildDependencyEdgesByChildRef", () => {
    it("indexes freq_on/single_point_on edges by child ref across every TS entry", () => {
        const map = buildDependencyEdgesByChildRef([
            ts([
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" },
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_sp1", role: "single_point_on" },
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_irc1", role: "irc_start" },
            ]),
        ])
        expect(map.get("calc_freq1")).toEqual({ role: "freq_on", parentCalculationRef: "calc_opt1" })
        expect(map.get("calc_sp1")).toEqual({ role: "single_point_on", parentCalculationRef: "calc_opt1" })
        // irc_start is not indexed -- plays no part in level resolution.
        expect(map.has("calc_irc1")).toBe(false)
    })

    it("merges edges from multiple TS entries into one map", () => {
        const map = buildDependencyEdgesByChildRef([
            ts([{ parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" }]),
            ts([{ parent_calculation_ref: "calc_opt2", child_calculation_ref: "calc_freq2", role: "freq_on" }]),
        ])
        expect(map.get("calc_freq1")?.parentCalculationRef).toBe("calc_opt1")
        expect(map.get("calc_freq2")?.parentCalculationRef).toBe("calc_opt2")
    })
})

describe("deriveKineticsLevelsFallback -- geometry (mirrors the backend's _resolve_ts_opt_via_dependency)", () => {
    const calculationsByRef = buildCalculationsByRef([
        calc("calc_opt1", "opt", b3lyp),
        calc("calc_freq1", "freq", b3lyp),
        calc("calc_sp1", "sp", ccsdt),
    ])
    const edges = buildDependencyEdgesByChildRef([
        ts([
            { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" },
            { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_sp1", role: "single_point_on" },
        ]),
    ])

    it("resolves geometry via the freq_on parent-opt edge when ts_opt_calculation_ref is null (the structural-always-null case)", () => {
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_opt_calculation_ref: null, ts_freq_calculation_ref: "calc_freq1" }),
            calculationsByRef,
            edges,
        )
        expect(levels.geometry).toEqual(b3lyp)
    })

    it("falls back to the single_point_on parent-opt edge when only sp is cited (no freq)", () => {
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_opt_calculation_ref: null, ts_sp_calculation_ref: "calc_sp1" }),
            calculationsByRef,
            edges,
        )
        expect(levels.geometry).toEqual(b3lyp)
    })

    it("prefers the freq-derived opt when both freq and sp are cited", () => {
        const twoOpts = buildCalculationsByRef([
            calc("calc_opt_via_freq", "opt", b3lyp),
            calc("calc_opt_via_sp", "opt", ccsdt),
            calc("calc_freq1", "freq", b3lyp),
            calc("calc_sp1", "sp", ccsdt),
        ])
        const twoEdges = buildDependencyEdgesByChildRef([
            ts([
                { parent_calculation_ref: "calc_opt_via_freq", child_calculation_ref: "calc_freq1", role: "freq_on" },
                { parent_calculation_ref: "calc_opt_via_sp", child_calculation_ref: "calc_sp1", role: "single_point_on" },
            ]),
        ])
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_freq_calculation_ref: "calc_freq1", ts_sp_calculation_ref: "calc_sp1" }),
            twoOpts,
            twoEdges,
        )
        expect(levels.geometry).toEqual(b3lyp) // the freq-derived opt's level, not the sp-derived one
    })

    it("leaves geometry null when the cited freq has no recorded freq_on parent edge at all", () => {
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_freq_calculation_ref: "calc_freq_unlinked" }),
            calculationsByRef,
            edges,
        )
        expect(levels.geometry).toBeNull()
    })

    it("requires the edge's parent to actually be opt-typed -- a mistyped parent resolves to null, never a wrong level", () => {
        const badParentCalcs = buildCalculationsByRef([
            calc("calc_not_opt", "sp", b3lyp), // wrong type
            calc("calc_freq1", "freq", b3lyp),
        ])
        const badEdges = buildDependencyEdgesByChildRef([
            ts([{ parent_calculation_ref: "calc_not_opt", child_calculation_ref: "calc_freq1", role: "freq_on" }]),
        ])
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_freq_calculation_ref: "calc_freq1" }),
            badParentCalcs,
            badEdges,
        )
        expect(levels.geometry).toBeNull()
    })

    it("honours a direct ts_opt_calculation_ref citation when one IS present, without consulting the dependency edge", () => {
        const directCalcs = buildCalculationsByRef([calc("calc_opt_direct", "opt", ccsdt), calc("calc_freq1", "freq", b3lyp)])
        const { levels } = deriveKineticsLevelsFallback(
            provenance({ ts_opt_calculation_ref: "calc_opt_direct", ts_freq_calculation_ref: "calc_freq1" }),
            directCalcs,
            edges, // would resolve calc_freq1 -> calc_opt1/b3lyp if consulted -- must NOT be used
        )
        expect(levels.geometry).toEqual(ccsdt)
    })
})

describe("deriveKineticsLevelsFallback -- frequency (direct citation only, never falls back to the opt's level)", () => {
    it("is the freq calc's own level when cited", () => {
        const calculationsByRef = buildCalculationsByRef([calc("calc_opt1", "opt", ccsdt), calc("calc_freq1", "freq", b3lyp)])
        const edges = buildDependencyEdgesByChildRef([ts([{ parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" }])])
        const { levels } = deriveKineticsLevelsFallback(provenance({ ts_freq_calculation_ref: "calc_freq1" }), calculationsByRef, edges)
        expect(levels.frequency).toEqual(b3lyp)
    })

    it("stays null when no freq is cited, even though the resolved opt itself has a level (no carries_frequencies fallback)", () => {
        const calculationsByRef = buildCalculationsByRef([calc("calc_opt1", "opt", b3lyp), calc("calc_sp1", "sp", ccsdt)])
        const edges = buildDependencyEdgesByChildRef([ts([{ parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_sp1", role: "single_point_on" }])])
        const { levels } = deriveKineticsLevelsFallback(provenance({ ts_sp_calculation_ref: "calc_sp1" }), calculationsByRef, edges)
        expect(levels.geometry).toEqual(b3lyp) // resolved via the edge
        expect(levels.frequency).toBeNull() // but frequency is NOT backfilled from it
    })
})

describe("deriveKineticsLevelsFallback -- energy and the resolver-disagreement fallback note", () => {
    it("resolves energy directly from a cited sp that IS among calculationsByRef, energy_source sp, no fallback note", () => {
        const calculationsByRef = buildCalculationsByRef([calc("calc_sp1", "sp", ccsdt)])
        const { levels, energyFallbackNote } = deriveKineticsLevelsFallback(
            provenance({ ts_sp_calculation_ref: "calc_sp1", primary_level_of_theory: b3lyp }),
            calculationsByRef,
            new Map(),
        )
        expect(levels.energy).toEqual(ccsdt)
        expect(levels.energy_source).toBe("sp")
        expect(energyFallbackNote).toBeNull()
    })

    // The live "resolver disagreement" case (plan §7): a kinetics record's
    // own cited sp calculation is not among this page's known calculations
    // at all (verified live on kin_spkzatwjlvmmnja3i5im4fl7hq pre-deploy).
    it("substitutes primary_level_of_theory and RETURNS a fallback note when the cited sp is not among calculationsByRef", () => {
        const calculationsByRef = buildCalculationsByRef([]) // sp ref not known here
        const { levels, energyFallbackNote } = deriveKineticsLevelsFallback(
            provenance({ ts_sp_calculation_ref: "calc_unknown_sp", primary_level_of_theory: b3lyp }),
            calculationsByRef,
            new Map(),
        )
        expect(levels.energy).toEqual(b3lyp)
        expect(levels.energy_source).toBe("sp")
        expect(energyFallbackNote).not.toBeNull()
        expect(energyFallbackNote).toContain("calc_unknown_sp")
    })

    it("falls back to the resolved opt's level (energy_source opt) when no sp is cited at all", () => {
        const calculationsByRef = buildCalculationsByRef([calc("calc_opt1", "opt", b3lyp), calc("calc_freq1", "freq", b3lyp)])
        const edges = buildDependencyEdgesByChildRef([ts([{ parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" }])])
        const { levels, energyFallbackNote } = deriveKineticsLevelsFallback(provenance({ ts_freq_calculation_ref: "calc_freq1" }), calculationsByRef, edges)
        expect(levels.energy).toEqual(b3lyp)
        expect(levels.energy_source).toBe("opt")
        expect(energyFallbackNote).toBeNull()
    })

    it("returns an all-null summary for a non-TS-backed record (no chain at all)", () => {
        const { levels, energyFallbackNote } = deriveKineticsLevelsFallback(provenance(), new Map(), new Map())
        expect(levels).toEqual({ geometry: null, frequency: null, energy: null, energy_source: null })
        expect(energyFallbackNote).toBeNull()
    })
})
