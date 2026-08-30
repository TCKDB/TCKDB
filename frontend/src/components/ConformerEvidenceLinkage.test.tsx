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
// counts (3 / 7 / 2) are ALSO mutually distinct, so a bare-number query is
// unambiguous about which step it read -- but see `step()` below: every
// assertion in this file binds a count to its unit inside the SAME step,
// because two independent `getByText` calls do NOT prove the two belong
// together (a review round caught exactly that gap: swapping the
// observations and geometries counts left every prior assertion green).
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

// The stable test hook (`data-linkage-step`, `ConformerEvidenceLinkage.tsx`)
// scopes a query to ONE step's own DOM subtree, so a count and its unit (or
// its detail text) can be asserted as belonging to the SAME step, not just
// present somewhere on the page.
function step(kind: "observations" | "calculations" | "geometries"): HTMLElement {
    const el = document.querySelector(`[data-linkage-step="${kind}"]`)
    if (!el) throw new Error(`no rendered step for "${kind}"`)
    return el as HTMLElement
}

describe("ConformerEvidenceLinkage", () => {
    it("labels the heading with the conformer's own display label (auto-numbered basin rendered as 'Conformer Group N')", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByRole("heading", { name: "Evidence for Conformer Group 1" })).toBeVisible()
    })

    it("binds the observation count to its OWN step and unit -- swapping it with the geometry count is caught here", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(within(step("observations")).getByText("3")).toBeVisible()
        expect(within(step("observations")).getByText("deposited observations")).toBeVisible()
        expect(within(step("observations")).getByText("each a separate sighting of this basin")).toBeVisible()
        // Not present in this step under a swap: the OTHER two steps' own counts.
        expect(within(step("observations")).queryByText("7")).not.toBeInTheDocument()
        expect(within(step("observations")).queryByText("2")).not.toBeInTheDocument()
    })

    it("binds the calculation-row total, its own opt/freq/sp breakdown, and the (different) chain count to the SAME step", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(within(step("calculations")).getByText("7")).toBeVisible()
        expect(within(step("calculations")).getByText("calculation rows")).toBeVisible()
        // Breakdown by TYPE (3 opt/2 freq/2 sp) is the raw row count --
        // different from the 2 optimization CHAINS reported alongside it.
        expect(within(step("calculations")).getByText(
            "3 opt · 2 freq · 2 sp, in 2 optimization chains (a staged coarse-then-fine reoptimization counts as one chain)",
        )).toBeVisible()
    })

    it("binds the distinct-geometry count to its OWN step, and shows how many calculation outputs converge on EACH one", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        const geometries = step("geometries")
        expect(within(geometries).getByText("2")).toBeVisible()
        expect(within(geometries).getByText("distinct stored geometries")).toBeVisible()
        // Not present in this step under a swap: the observation count.
        expect(within(geometries).queryByText("3")).not.toBeInTheDocument()
        expect(within(geometries).getByText("geom_a")).toBeVisible()
        expect(within(geometries).getByText(/4 calculation outputs/)).toBeVisible()
        expect(within(geometries).getByText("geom_b")).toBeVisible()
        expect(within(geometries).getByText(/3 calculation outputs/)).toBeVisible()
    })

    it("prints the archive's PUBLISHED calculation_count even when the row breakdown hasn't loaded -- never recomputed from a missing list", () => {
        // `calculation_count: 7` is still published; `calculations` (the
        // list the breakdown is derived from) is null, e.g. not yet
        // fetched. A component that recomputes the total from the
        // breakdown instead of trusting the published field would print 0
        // here, silently disagreeing with the archive's own count.
        const noBreakdown = conformer({ calculations: null } as Partial<ConformerProjection>)
        render(<ConformerEvidenceLinkage conformer={noBreakdown} />)
        expect(within(step("calculations")).getByText("7")).toBeVisible()
        expect(within(step("calculations")).getByText("breakdown not loaded")).toBeVisible()
        expect(within(step("calculations")).queryByText(/opt/)).not.toBeInTheDocument()
        expect(within(step("calculations")).queryByText("no calculation rows recorded")).not.toBeInTheDocument()
    })

    it("says 'no calculation rows recorded' only when the archive's OWN published count is genuinely zero", () => {
        const zero = conformer({
            evidence_summary: {
                calculation_count: 0, optimization_chain_count: 0, geometry_count: 0,
                evidence_coverage: { opt: 0, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            calculations: [],
        })
        render(<ConformerEvidenceLinkage conformer={zero} />)
        expect(within(step("calculations")).getByText("no calculation rows recorded")).toBeVisible()
        expect(within(step("calculations")).queryByText("breakdown not loaded")).not.toBeInTheDocument()
    })

    it("prints the archive's PUBLISHED geometry_count even when the geometry links haven't loaded -- never recomputed from a missing list", () => {
        // `geometry_count: 2` is still published; `geometries` (the link
        // list the convergence breakdown is derived from) is null. A
        // component that derives the count from the links instead of
        // trusting the published field would print 0 here.
        const noLinks = conformer({ geometries: null } as Partial<ConformerProjection>)
        render(<ConformerEvidenceLinkage conformer={noLinks} />)
        expect(within(step("geometries")).getByText("2")).toBeVisible()
        expect(within(step("geometries")).getByText("breakdown not loaded")).toBeVisible()
        expect(within(step("geometries")).queryByText("geom_a")).not.toBeInTheDocument()
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
        expect(within(step("observations")).getByText("deposited observation")).toBeVisible()
        expect(within(step("calculations")).getByText("calculation row")).toBeVisible()
        expect(within(step("geometries")).getByText("distinct stored geometry")).toBeVisible()
        expect(within(step("calculations")).getByText(/1 optimization chain\b/)).toBeVisible()
        expect(within(step("geometries")).getByText(/1 calculation output\b/)).toBeVisible()
    })

    it("renders a depositor-chosen label verbatim, never coerced into 'Conformer Group N'", () => {
        const named = conformer({ conformer_group: { conformer_group_ref: "cg_x", label: "anti-periplanar" } })
        render(<ConformerEvidenceLinkage conformer={named} />)
        expect(screen.getByRole("heading", { name: "Evidence for anti-periplanar" })).toBeVisible()
    })

    it("falls back to the group's own ref for a blank/whitespace-only label, never an empty heading", () => {
        const blank = conformer({ conformer_group: { conformer_group_ref: "cg_blank", label: "   " } })
        render(<ConformerEvidenceLinkage conformer={blank} />)
        expect(screen.getByRole("heading", { name: "Evidence for cg_blank" })).toBeVisible()
    })
})
