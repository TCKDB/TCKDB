import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { CalculationDependency } from "../api/calculationApi"
import { DEPENDENCY_ROLE_WORDING, dependencyEdgeLabel } from "../domain/dependencyWording"
import { CalculationDependencyGraph } from "./CalculationDependencyGraph"

afterEach(cleanup)

/**
 * The owner's ask ("I think this can be presented as a graph instead...
 * Would read/flow better") for what used to be plain Related-calculations
 * sentences with links -- see `CalculationDependencyGraph.tsx`'s own
 * docstring for the layout/accessibility design this exercises.
 *
 * `window.matchMedia` is not implemented by jsdom (confirmed against this
 * app's existing convention, `GeometryViewer.test.tsx`'s
 * `installFakeMatchMedia` helper, needed for the SAME reason) -- every
 * test here therefore renders the WIDE layout, the component's documented
 * fallback. The narrow (<=680px) layout is verified visually, with a real
 * browser, in the PR's own screenshots.
 */
function renderGraph(dependencies: CalculationDependency[], ownRef = "calc_own_ref", ownType = "opt") {
    return render(
        <MemoryRouter>
            <CalculationDependencyGraph dependencies={dependencies} ownRef={ownRef} ownType={ownType} />
        </MemoryRouter>,
    )
}

describe("CalculationDependencyGraph — zero dependencies", () => {
    it("renders no SVG (and nothing else) when given an empty dependencies array", () => {
        const { container } = renderGraph([])
        expect(container.querySelector("svg")).toBeNull()
        expect(container.firstChild).toBeNull()
    })
})

describe("CalculationDependencyGraph — nodes", () => {
    it("renders one node per distinct ref, plus the centre node, never duplicating a ref seen twice", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
            // A second edge to the SAME parent, under a different role --
            // must still be exactly one node for calc_opt_parent.
            { role: "arkane_source", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
            { role: "freq_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_one" },
            { role: "single_point_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_two" },
        ])

        // Centre + 1 distinct parent + 2 distinct children = 4 nodes.
        expect(screen.getByTestId("dep-node-centre-calc_own_ref")).toBeInTheDocument()
        expect(screen.getByTestId("dep-node-parent-calc_opt_parent")).toBeInTheDocument()
        expect(screen.getByTestId("dep-node-child-calc_child_one")).toBeInTheDocument()
        expect(screen.getByTestId("dep-node-child-calc_child_two")).toBeInTheDocument()
        expect(document.querySelectorAll("[data-testid^='dep-node-']")).toHaveLength(4)
    })

    it("shows the ref alone (no type pill) on a parent/child node -- the payload never carries the other calculation's type", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
        ])
        const parentNode = screen.getByTestId("dep-node-parent-calc_opt_parent")
        expect(within(parentNode).getByText("calc_opt_parent")).toBeInTheDocument()
        // No pill-styled text node on a satellite -- only the centre node
        // gets `.dep-graph-node-pill-text`.
        expect(parentNode.querySelector(".dep-graph-node-pill-text")).toBeNull()
    })

    it("shows a type pill and the ref on the centre node -- the one node whose type IS known", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
        ], "calc_own_ref", "freq")
        const centreNode = screen.getByTestId("dep-node-centre-calc_own_ref")
        expect(within(centreNode).getByText("Frequency")).toBeInTheDocument()
        expect(within(centreNode).getByText("calc_own_ref")).toBeInTheDocument()
    })

    it("links every parent/child node to /calculations/<ref>", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
            { role: "freq_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_one" },
        ])
        const parentLink = within(screen.getByTestId("dep-node-parent-calc_opt_parent")).getByRole("link")
        expect(parentLink).toHaveAttribute("href", "/calculations/calc_opt_parent")
        const childLink = within(screen.getByTestId("dep-node-child-calc_child_one")).getByRole("link")
        expect(childLink).toHaveAttribute("href", "/calculations/calc_child_one")
    })

    it("does not render the centre node as a link -- it is the page already being viewed", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
        ])
        const centreNode = screen.getByTestId("dep-node-centre-calc_own_ref")
        expect(within(centreNode).queryByRole("link")).toBeNull()
    })
})

describe("CalculationDependencyGraph — edges point parent -> child", () => {
    it("a parent-tier edge (the other calc IS the parent) runs from the other calc's ref to ownRef", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
        ])
        const edge = screen.getByTestId("dep-edge-calc_opt_parent-calc_own_ref-optimized_from")
        const path = edge.querySelector("path")!
        expect(path).toHaveAttribute("data-from", "calc_opt_parent")
        expect(path).toHaveAttribute("data-to", "calc_own_ref")
        // The arrowhead sits at the path's destination end (marker-end),
        // never marker-start -- so it always points toward the child.
        expect(path).toHaveAttribute("marker-end")
        expect(path).not.toHaveAttribute("marker-start")
    })

    it("a child-tier edge (the other calc IS the child) runs from ownRef to the other calc's ref", () => {
        renderGraph([
            { role: "freq_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_one" },
        ])
        const edge = screen.getByTestId("dep-edge-calc_own_ref-calc_child_one-freq_on")
        const path = edge.querySelector("path")!
        expect(path).toHaveAttribute("data-from", "calc_own_ref")
        expect(path).toHaveAttribute("data-to", "calc_child_one")
    })
})

describe("CalculationDependencyGraph — edge labels reuse the sentence-list wording", () => {
    it.each(Object.keys(DEPENDENCY_ROLE_WORDING))("labels a %s edge with dependencyEdgeLabel's own word, not a re-derived one", (role) => {
        renderGraph([
            { role, direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_one" },
        ])
        const edge = screen.getByTestId(`dep-edge-calc_own_ref-calc_child_one-${role}`)
        expect(within(edge).getByText(dependencyEdgeLabel(role))).toBeInTheDocument()
    })

    it("falls back to the raw (spaced) role token for a role with no bespoke wording, same as the sentence list", () => {
        renderGraph([
            { role: "scan_parent", direction: "child", parent_calculation_ref: "calc_scan_owner", child_calculation_ref: "calc_own_ref" },
        ])
        const edge = screen.getByTestId("dep-edge-calc_scan_owner-calc_own_ref-scan_parent")
        expect(within(edge).getByText("scan parent")).toBeInTheDocument()
    })
})

describe("CalculationDependencyGraph — accessibility", () => {
    it("gives the SVG role=img and an aria-label counting parents and children", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_p1", child_calculation_ref: "calc_own_ref" },
            { role: "freq_on", direction: "child", parent_calculation_ref: "calc_p2", child_calculation_ref: "calc_own_ref" },
            { role: "single_point_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_c1" },
        ])
        const svg = screen.getByRole("img")
        expect(svg.tagName.toLowerCase()).toBe("svg")
        expect(svg).toHaveAttribute("aria-label", expect.stringContaining("2 parents, 1 child"))
    })

    it("counts a single parent/child in the singular", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_p1", child_calculation_ref: "calc_own_ref" },
        ])
        expect(screen.getByRole("img")).toHaveAttribute("aria-label", expect.stringContaining("1 parent"))
        expect(screen.getByRole("img").getAttribute("aria-label")).not.toMatch(/child/)
    })

    it("keeps the sentence list in the DOM as the SVG's text equivalent, with the archive's exact prior wording", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_htgb7s5nakuw52eqhcxpvilpoq", child_calculation_ref: "calc_own_ref" },
        ], "calc_own_ref")
        const list = screen.getByRole("list", { name: "Dependency edges, as text" })
        const item = within(list).getByRole("listitem")
        expect(item.textContent).toBe("This was optimized from calc_htgb7s5nakuw52eqhcxpvilpoq")
        expect(within(list).getByRole("link", { name: "calc_htgb7s5nakuw52eqhcxpvilpoq" })).toHaveAttribute(
            "href", "/calculations/calc_htgb7s5nakuw52eqhcxpvilpoq",
        )
    })

    it("gives a graph node a DIFFERENT accessible name than the sentence-list link for the same ref, so page-wide role queries never collide", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_htgb7s5nakuw52eqhcxpvilpoq", child_calculation_ref: "calc_own_ref" },
        ], "calc_own_ref")
        // Exactly one link named plainly after the ref (the sentence list).
        expect(screen.getAllByRole("link", { name: "calc_htgb7s5nakuw52eqhcxpvilpoq" })).toHaveLength(1)
        // The graph node's own link carries a DIFFERENT accessible name.
        const nodeLink = within(screen.getByTestId("dep-node-parent-calc_htgb7s5nakuw52eqhcxpvilpoq")).getByRole("link")
        expect(nodeLink).not.toHaveAccessibleName("calc_htgb7s5nakuw52eqhcxpvilpoq")
    })
})
