import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import { DependencyStrip } from "./CalculationLineage"
import { dependsOn, usedBy } from "../domain/calculationLineage"
import type { CalculationDependency } from "../api/calculationApi"

afterEach(cleanup)

function edge(role: string, direction: "parent" | "child", parent: string, child: string): CalculationDependency {
    return { role, direction, parent_calculation_ref: parent, child_calculation_ref: child }
}

describe("dependency strip", () => {
    it("is visited by Tab in the order the calculations ran, upstream first", async () => {
        const user = userEvent.setup()
        // Given out of order on purpose: the strip sorts by role, not by arrival.
        const steps = dependsOn([
            edge("single_point_on", "child", "calc_second", "calc_self"),
            edge("optimized_from", "child", "calc_first", "calc_self"),
            edge("freq_on", "child", "calc_third", "calc_self"),
        ])
        render(
            <MemoryRouter>
                <a href="#before">before</a>
                <DependencyStrip steps={steps} ownWords="Single-point energy" />
                <a href="#after">after</a>
            </MemoryRouter>,
        )
        await user.tab()
        expect(screen.getByRole("link", { name: "before" })).toHaveFocus()
        for (const ref of ["calc_first", "calc_third", "calc_second"]) {
            await user.tab()
            expect(screen.getByRole("link", { name: ref })).toHaveFocus()
        }
        await user.tab()
        expect(screen.getByRole("link", { name: "after" })).toHaveFocus()
    })
})

describe("lineage", () => {
    const edges = [
        edge("freq_on", "parent", "calc_self", "calc_child"),
        edge("optimized_from", "child", "calc_parent", "calc_self"),
    ]

    it("keeps the edges this calculation depends on apart from the ones that depend on it", () => {
        expect(dependsOn(edges).map((step) => step.ref)).toEqual(["calc_parent"])
        expect(usedBy(edges).map((step) => step.ref)).toEqual(["calc_child"])
    })

    it("spells out what the other calculation is from the edge role", () => {
        expect(usedBy(edges)[0].words).toBe("Frequency calculation")
        expect(dependsOn([edge("freq_on", "child", "calc_opt", "calc_self")])[0].words).toBe("Optimisation")
    })

    it("falls back to the role with spaces for a role it has no words for", () => {
        expect(dependsOn([edge("some_future_role", "child", "calc_x", "calc_self")])[0].words).toBe("some future role")
    })
})
