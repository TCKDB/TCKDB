import { describe, expect, it } from "vitest"
import type { CalculationDependency } from "../api/calculationApi"
import {
    OPTIMISATION_STAGE_UNKNOWN_KICKER_SUFFIX,
    OPTIMISATION_STAGE_WORDS,
    optimisationStage,
} from "./optimisationStage"

function edge(overrides: Partial<CalculationDependency> = {}): CalculationDependency {
    return {
        role: "optimized_from",
        direction: "parent",
        parent_calculation_ref: "calc_parent",
        child_calculation_ref: "calc_child",
        ...overrides,
    }
}

describe("OPTIMISATION_STAGE_WORDS", () => {
    // Literal-pinned: the exact wording the owner asked for
    // ("Fine optimisation: <LINK>", "OPTIMISATION CALCULATION · COARSE
    // PASS") must never silently drift — a future edit that changes these
    // strings has to change this test too.
    it("pins the exact coarse/fine wording", () => {
        expect(OPTIMISATION_STAGE_WORDS).toEqual({
            coarse: { label: "Coarse optimisation", kickerSuffix: "coarse pass" },
            fine: { label: "Fine optimisation", kickerSuffix: "fine pass" },
        })
    })

    it("pins the unknown-stage kicker suffix to the pre-existing 'deposited evidence' wording", () => {
        expect(OPTIMISATION_STAGE_UNKNOWN_KICKER_SUFFIX).toBe("deposited evidence")
    })
})

describe("optimisationStage", () => {
    it("reads a parent-side optimized_from edge as the coarse pass, linking to the fine (child) calculation", () => {
        const stage = optimisationStage([
            edge({ direction: "parent", role: "optimized_from", parent_calculation_ref: "calc_coarse", child_calculation_ref: "calc_fine" }),
        ])
        expect(stage).toEqual({ kind: "coarse", otherRef: "calc_fine" })
    })

    it("reads a child-side optimized_from edge as the fine pass, linking to the coarse (parent) calculation", () => {
        const stage = optimisationStage([
            edge({ direction: "child", role: "optimized_from", parent_calculation_ref: "calc_coarse", child_calculation_ref: "calc_fine" }),
        ])
        expect(stage).toEqual({ kind: "fine", otherRef: "calc_coarse" })
    })

    it("returns unknown -- never a guessed single-pass verdict -- when there is no optimized_from edge", () => {
        expect(optimisationStage([])).toEqual({ kind: "unknown" })
        expect(optimisationStage([edge({ role: "freq_on", direction: "child" })])).toEqual({ kind: "unknown" })
    })

    it("ignores a non-optimized_from edge even when direction matches", () => {
        const stage = optimisationStage([
            edge({ direction: "parent", role: "single_point_on" }),
        ])
        expect(stage.kind).toBe("unknown")
    })
})
