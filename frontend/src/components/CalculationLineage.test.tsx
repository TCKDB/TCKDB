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
    it("is visited by Tab one parent after another, then on to the next link", async () => {
        const user = userEvent.setup()
        const steps = dependsOn([
            edge("single_point_on", "child", "calc_second", "calc_self"),
            edge("optimized_from", "child", "calc_first", "calc_self"),
            edge("freq_on", "child", "calc_third", "calc_self"),
        ], { calc_first: "opt", calc_second: "opt", calc_third: "composite" })
        render(
            <MemoryRouter>
                <a href="#before">before</a>
                <DependencyStrip steps={steps} ownWords="Single-point energy" />
                <a href="#after">after</a>
            </MemoryRouter>,
        )
        await user.tab()
        expect(screen.getByRole("link", { name: "before" })).toHaveFocus()
        // By type words, then ref, with no sequence implied: Coarse optimisation, Composite energy, Optimisation.
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

    it("names the other calculation by its real type, whatever the role says", () => {
        expect(usedBy(edges, { calc_child: "freq" })[0].words).toBe("Frequency calculation")
        expect(dependsOn([edge("freq_on", "child", "calc_x", "calc_self")], { calc_x: "composite" })[0].words).toBe("Composite energy")
    })

    it("uses the coarse and fine pass words for an optimized_from pair", () => {
        expect(dependsOn(edges, { calc_parent: "opt" })[0].words).toBe("Coarse optimisation")
        expect(usedBy([edge("optimized_from", "parent", "calc_self", "calc_next")], { calc_next: "opt" })[0].words).toBe("Fine optimisation")
    })

    it("claims no type until the type is known", () => {
        expect(dependsOn([edge("freq_on", "child", "calc_x", "calc_self")])[0].words).toBe("Geometry from")
        expect(usedBy([edge("freq_on", "parent", "calc_self", "calc_y")])[0].words).toBe("Ran on this")
        expect(dependsOn([edge("freq_on", "child", "calc_x", "calc_self")], {})[0].words).toBe("Geometry from")
    })

    it("falls back to the role with spaces for a role it has no words for", () => {
        expect(dependsOn([edge("some_future_role", "child", "calc_x", "calc_self")])[0].words).toBe("some future role")
    })
})
