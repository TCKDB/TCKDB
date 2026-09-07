import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { CalculationDependency } from "../api/calculationApi"
import { buildDependencyGraphModel, computeNarrowLayout, computeWideLayout } from "../domain/dependencyGraphLayout"
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
        const path = screen.getByTestId("dep-edge-path-calc_opt_parent-calc_own_ref-optimized_from")
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
        const path = screen.getByTestId("dep-edge-path-calc_own_ref-calc_child_one-freq_on")
        expect(path).toHaveAttribute("data-from", "calc_own_ref")
        expect(path).toHaveAttribute("data-to", "calc_child_one")
    })
})

describe("CalculationDependencyGraph — paint order (every path before every label)", () => {
    it("renders every <path> before any edge-label <g>, so a later edge's line can never paint over an earlier edge's label", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_p1", child_calculation_ref: "calc_own_ref" },
            { role: "freq_on", direction: "child", parent_calculation_ref: "calc_p2", child_calculation_ref: "calc_own_ref" },
            { role: "single_point_on", direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_c1" },
        ])
        const svg = screen.getByRole("img")
        const children = Array.from(svg.children)
        const lastPathIndex = children.map((el) => el.tagName.toLowerCase()).lastIndexOf("path")
        const firstLabelIndex = children.findIndex((el) => el.getAttribute("data-testid")?.startsWith("dep-edge-label-"))
        expect(lastPathIndex).toBeGreaterThan(-1)
        expect(firstLabelIndex).toBeGreaterThan(-1)
        expect(lastPathIndex).toBeLessThan(firstLabelIndex)
    })
})

// These assert against `dependencyEdgeLabel`/`DEPENDENCY_ROLE_WORDING`
// themselves (structural: the rendered label IS whatever the table says) --
// the LITERAL words are pinned separately, in `domain/dependencyWording
// .test.ts`, so a wording change shows up there as a real diff instead of
// silently staying green here.
describe("CalculationDependencyGraph — edge labels reuse the sentence-list wording", () => {
    it.each(Object.keys(DEPENDENCY_ROLE_WORDING))("labels a %s edge with dependencyEdgeLabel's own word, not a re-derived one", (role) => {
        renderGraph([
            { role, direction: "parent", parent_calculation_ref: "calc_own_ref", child_calculation_ref: "calc_child_one" },
        ])
        const label = screen.getByTestId(`dep-edge-label-calc_own_ref-calc_child_one-${role}`)
        expect(within(label).getByText(dependencyEdgeLabel(role))).toBeInTheDocument()
    })

    it("falls back to the raw (spaced) role token for a role with no bespoke wording, same as the sentence list", () => {
        renderGraph([
            { role: "scan_parent", direction: "child", parent_calculation_ref: "calc_scan_owner", child_calculation_ref: "calc_own_ref" },
        ])
        const label = screen.getByTestId("dep-edge-label-calc_scan_owner-calc_own_ref-scan_parent")
        expect(within(label).getByText("scan parent")).toBeInTheDocument()
    })
})

describe("CalculationDependencyGraph — centre node type pill", () => {
    it("draws a real pill (a background rect) behind the centre node's type text, not bare text", () => {
        renderGraph([
            { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_opt_parent", child_calculation_ref: "calc_own_ref" },
        ], "calc_own_ref", "freq")
        const centreNode = screen.getByTestId("dep-node-centre-calc_own_ref")
        const pillBg = centreNode.querySelector(".dep-graph-node-pill-bg")
        expect(pillBg).not.toBeNull()
        expect(pillBg?.tagName.toLowerCase()).toBe("rect")
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

/**
 * jsdom has no `ResizeObserver` at all, so proving the responsive switch
 * itself works -- not just that the component degrades honestly without
 * one -- needs a fake one installed for just this block, mirroring
 * `GeometryViewer.test.tsx`'s own `installFakeMatchMedia`/
 * `FakeResizeObserver` convention (see that file's "zero-size container"
 * describe block). Nothing else in this file exercises this branch: a
 * stubbed always-wide default (no `ResizeObserver`, matching every other
 * test above) and a `computeNarrowLayout` that silently returned the wide
 * layout would BOTH stay green against every other test in this file --
 * this block is what actually exercises the ResizeObserver/measured-
 * width branch `CalculationDependencyGraph.tsx` itself gates on, per the
 * post-review fix list.
 */
describe("CalculationDependencyGraph — responsive layout via ResizeObserver", () => {
    class FakeResizeObserver {
        static instances: FakeResizeObserver[] = []
        callback: ResizeObserverCallback
        constructor(callback: ResizeObserverCallback) {
            this.callback = callback
            FakeResizeObserver.instances.push(this)
        }
        observe() { /* no-op: the test triggers resizes manually */ }
        unobserve() { /* no-op */ }
        disconnect() { /* no-op */ }
        trigger(width: number) {
            this.callback([{ contentRect: { width } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver)
        }
    }

    let originalResizeObserver: typeof ResizeObserver | undefined

    beforeEach(() => {
        FakeResizeObserver.instances = []
        originalResizeObserver = (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver
        ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver
    })

    afterEach(() => {
        (globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalResizeObserver
    })

    const DEPENDENCIES: CalculationDependency[] = [
        { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_parent0abcdefghijklmnopqrstuvwx", child_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv" },
        { role: "freq_on", direction: "parent", parent_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv", child_calculation_ref: "calc_child0abcdefghijklmnopqrstuvwx" },
        { role: "single_point_on", direction: "parent", parent_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv", child_calculation_ref: "calc_child1abcdefghijklmnopqrstuvwx" },
    ]

    function expectedLayouts() {
        const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", DEPENDENCIES)
        return { wide: computeWideLayout(model), narrow: computeNarrowLayout(model) }
    }

    it("renders the wide layout before any measurement arrives (the documented fallback)", () => {
        renderGraph(DEPENDENCIES, "calc_own_ref_abcdefghijklmnopqrstuv")
        const { wide } = expectedLayouts()
        expect(screen.getByRole("img")).toHaveAttribute("viewBox", `0 0 ${wide.width} ${wide.height}`)
    })

    it("switches to the narrow layout once the measured container is narrower than the wide layout's own width", async () => {
        renderGraph(DEPENDENCIES, "calc_own_ref_abcdefghijklmnopqrstuv")
        await waitFor(() => expect(FakeResizeObserver.instances).toHaveLength(1))
        const { wide, narrow } = expectedLayouts()

        FakeResizeObserver.instances[0].trigger(wide.width - 50)

        await waitFor(() => {
            expect(screen.getByRole("img")).toHaveAttribute("viewBox", `0 0 ${narrow.width} ${narrow.height}`)
        })
    })

    it("stays on the wide layout when the measured container is at least as wide as the wide layout's own width", async () => {
        renderGraph(DEPENDENCIES, "calc_own_ref_abcdefghijklmnopqrstuv")
        await waitFor(() => expect(FakeResizeObserver.instances).toHaveLength(1))
        const { wide } = expectedLayouts()

        FakeResizeObserver.instances[0].trigger(wide.width + 50)

        await waitFor(() => {
            expect(screen.getByRole("img")).toHaveAttribute("viewBox", `0 0 ${wide.width} ${wide.height}`)
        })
    })

    it("switches back to wide if a later measurement widens past the wide layout's own width again", async () => {
        renderGraph(DEPENDENCIES, "calc_own_ref_abcdefghijklmnopqrstuv")
        await waitFor(() => expect(FakeResizeObserver.instances).toHaveLength(1))
        const { wide, narrow } = expectedLayouts()

        FakeResizeObserver.instances[0].trigger(wide.width - 50)
        await waitFor(() => {
            expect(screen.getByRole("img")).toHaveAttribute("viewBox", `0 0 ${narrow.width} ${narrow.height}`)
        })

        FakeResizeObserver.instances[0].trigger(wide.width + 50)
        await waitFor(() => {
            expect(screen.getByRole("img")).toHaveAttribute("viewBox", `0 0 ${wide.width} ${wide.height}`)
        })
    })

    // Review finding: with only a `maxWidth` cap (no floor), the ACTIVE
    // layout -- even the narrow one, already chosen for being the
    // narrower of the two -- still scaled itself down further via CSS
    // `width: 100%` on a container narrower than ITS OWN width (600px
    // measured at 0.89x/10.26px text, 400px at 0.57x/6.6px). `minWidth`
    // pinned to the same value as `maxWidth` is what holds the floor: at
    // ANY container width, the rendered SVG's own inline style -- not
    // just its `viewBox` -- must stay fixed at the active layout's own
    // computed width.
    it("holds the text-size floor at narrow viewport widths (400px, 600px) -- the SVG's own min/max width never shrinks below the active layout's width", async () => {
        renderGraph(DEPENDENCIES, "calc_own_ref_abcdefghijklmnopqrstuv")
        await waitFor(() => expect(FakeResizeObserver.instances).toHaveLength(1))
        const { wide, narrow } = expectedLayouts()

        for (const containerWidth of [400, 600]) {
            FakeResizeObserver.instances[0].trigger(containerWidth)
            const expected = containerWidth < wide.width ? narrow : wide
            await waitFor(() => {
                const svg = screen.getByRole("img")
                expect(svg).toHaveAttribute("viewBox", `0 0 ${expected.width} ${expected.height}`)
                expect(svg.style.minWidth).toBe(`${expected.width}px`)
                expect(svg.style.maxWidth).toBe(`${expected.width}px`)
            })
        }
    })
})
