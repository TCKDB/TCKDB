import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { ConformerEvidenceLinkage } from "./ConformerEvidenceLinkage"

afterEach(cleanup)

// Deliberately DIFFERENT numbers at every level -- 3 observations, 7
// calculation rows (3 opt/2 freq/2 sp) in 2 optimization chains, 2 distinct
// geometries (one produced by 4 calculation outputs, one by 3) -- so a
// mutation that reads the wrong field (e.g. printing the chain count where
// the row count belongs, or the coverage count where the raw type count
// belongs) produces a value distinguishable from every other number in the
// fixture, not one that happens to coincide. The three top-level step
// counts (3 / 7 / 2) are ALSO mutually distinct, so `getByText` on a bare
// number is unambiguous about which step it read.
function conformer(overrides: Partial<ConformerProjection> = {}): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: 3 },
        evidence_summary: {
            calculation_count: 7,
            optimization_chain_count: 2,
            geometry_count: 2,
            evidence_coverage: { opt: 2, freq: 2, sp: 1 },
            levels_of_theory: {},
        },
        observations: [],
        calculations: [
            { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" }, { calculation_ref: "c3", type: "opt" },
            { calculation_ref: "c4", type: "freq" }, { calculation_ref: "c5", type: "freq" },
            { calculation_ref: "c6", type: "sp" }, { calculation_ref: "c7", type: "sp" },
        ],
        geometries: [
            { calculation_ref: "c1", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c2", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c3", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c4", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c5", geometry: { geometry_ref: "geom_b" } },
            { calculation_ref: "c6", geometry: { geometry_ref: "geom_b" } },
            { calculation_ref: "c7", geometry: { geometry_ref: "geom_b" } },
        ],
        ...overrides,
    } as ConformerProjection
}

describe("ConformerEvidenceLinkage", () => {
    it("labels the heading with the conformer's own display label (auto-numbered basin rendered as 'Conformer Group N')", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByRole("heading", { name: "Evidence for Conformer Group 1" })).toBeVisible()
    })

    it("prints the observation count as its OWN unit, distinct from calculation rows and geometries", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByText("3")).toBeVisible()
        expect(screen.getByText("deposited observations")).toBeVisible()
    })

    it("prints the calculation-row total with its own opt/freq/sp breakdown AND the (different) chain count -- never one for the other", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByText("7")).toBeVisible()
        expect(screen.getByText("calculation rows")).toBeVisible()
        // Breakdown by TYPE (3 opt/2 freq/2 sp) is the raw row count --
        // different from the 2 optimization CHAINS reported alongside it.
        expect(screen.getByText(
            "3 opt · 2 freq · 2 sp, in 2 optimization chains (a staged coarse-then-fine reoptimization counts as one chain)",
        )).toBeVisible()
    })

    it("prints the distinct-geometry count and shows how many calculation outputs converge on EACH one", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByText("2")).toBeVisible()
        expect(screen.getByText("distinct stored geometries")).toBeVisible()
        const list = screen.getByText("geom_a").closest("ul") as HTMLElement
        expect(within(list).getByText("geom_a")).toBeVisible()
        expect(within(list).getByText(/4 calculation outputs/)).toBeVisible()
        expect(within(list).getByText("geom_b")).toBeVisible()
        expect(within(list).getByText(/3 calculation outputs/)).toBeVisible()
    })

    it("labels stage coverage as a share of the 3 OBSERVATIONS, not of the 7 calculation rows", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        const coverage = screen.getByText(/Stage coverage/).closest("p") as HTMLElement
        expect(coverage).toHaveTextContent("opt 2/3 · freq 2/3 · sp 1/3")
        expect(coverage).toHaveTextContent(
            "This counts which observations have at least one calculation of that stage, not the number of calculation rows.",
        )
    })

    it("uses singular wording at exactly one, for every unit independently", () => {
        const single = conformer({
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 1,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            calculations: [{ calculation_ref: "c1", type: "opt" }] as ConformerProjection["calculations"],
            geometries: [{ calculation_ref: "c1", geometry: { geometry_ref: "geom_solo" } }] as ConformerProjection["geometries"],
        })
        render(<ConformerEvidenceLinkage conformer={single} />)
        expect(screen.getByText("deposited observation")).toBeVisible()
        expect(screen.getByText("calculation row")).toBeVisible()
        expect(screen.getByText("distinct stored geometry")).toBeVisible()
        expect(screen.getByText(/1 optimization chain\b/)).toBeVisible()
        expect(screen.getByText(/1 calculation output\b/)).toBeVisible()
    })

    it("renders a depositor-chosen label verbatim, never coerced into 'Conformer Group N'", () => {
        const named = conformer({ conformer_group: { conformer_group_ref: "cg_x", label: "anti-periplanar" } })
        render(<ConformerEvidenceLinkage conformer={named} />)
        expect(screen.getByRole("heading", { name: "Evidence for anti-periplanar" })).toBeVisible()
    })
})
