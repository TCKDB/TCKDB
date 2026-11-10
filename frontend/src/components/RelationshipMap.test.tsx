import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { RelationshipMap } from "./RelationshipMap"
import type { NeighbourNode } from "../domain/neighbourNodes"
import mapCss from "../relationship-map.css?raw"

afterEach(cleanup)

const NODES: NeighbourNode[] = [
    { id: "molecule", kind: "species", label: "The molecule", relation: "All versions of this molecule", to: "/species/spc_1", found: null },
    { id: "conformers", kind: "conformer-group", label: "Conformer groups", relation: "Shapes of this entry", to: "/species-entries/spe_1/geometry", found: "3 found" },
    { id: "networks", kind: "network", label: "Networks", relation: "Pressure-dependent networks with this entry", to: null, found: "2 found" },
]

function Where() {
    return <p data-testid="where">{useLocation().pathname}</p>
}

function renderMap(nodes: NeighbourNode[] = NODES, status: "ready" | "loading" = "ready") {
    return render(
        <MemoryRouter initialEntries={["/species-entries/spe_1"]}>
            <Where />
            <Routes>
                <Route path="*" element={<RelationshipMap selfKind="species-entry" state={{ status, nodes }} />} />
            </Routes>
        </MemoryRouter>,
    )
}

describe("RelationshipMap", () => {
    it("draws a group of links under a heading, never one image around the links", () => {
        const { container } = renderMap()
        const svg = container.querySelector("svg.rel-map-figure")!
        expect(svg).toHaveAttribute("role", "group")
        expect(svg).toHaveAttribute("aria-labelledby", screen.getByRole("heading", { name: "Where this record sits" }).id)
        expect(container.querySelector('svg[role="img"].rel-map-figure')).toBeNull()
        expect(svg.querySelectorAll("a")).toHaveLength(3)
    })

    it("labels the current entry and each linked node", () => {
        renderMap()
        expect(screen.getByRole("link", { name: "This entry (you are here)" })).toHaveAttribute("aria-current", "page")
        expect(screen.getByRole("link", { name: "The molecule, All versions of this molecule" })).toHaveAttribute("href", "/species/spc_1")
        expect(screen.getByRole("link", { name: "Conformer groups, Shapes of this entry, 3 found" })).toBeInTheDocument()
    })

    it("has a Connected records list that carries every node, in the same order", () => {
        const { container } = renderMap()
        const list = screen.getByRole("list", { name: "Connected records" })
        const items = within(list).getAllByRole("listitem")
        expect(items.map((item) => item.textContent)).toEqual([
            expect.stringContaining("The molecule"),
            expect.stringContaining("Conformer groups"),
            expect.stringContaining("Networks"),
        ])
        const drawn = Array.from(container.querySelectorAll("svg.rel-map-figure a")).map((a) => a.getAttribute("href"))
        expect(drawn).toEqual(["/species-entries/spe_1", "/species/spc_1", "/species-entries/spe_1/geometry"])
    })

    it("leaves a group that stands for several records as text, not a link, in both the drawing and the list", () => {
        const { container } = renderMap()
        expect(screen.queryByRole("link", { name: /Networks/ })).toBeNull()
        expect(within(screen.getByRole("list", { name: "Connected records" })).getByText("Networks")).toBeInTheDocument()
        expect(container.querySelector("svg.rel-map-figure g.rel-map-node--plain")).not.toBeNull()
    })

    it("tabs through the drawn nodes in document order, then the list, and Enter on a list link navigates", async () => {
        const user = userEvent.setup()
        const { container } = renderMap()
        const drawn = Array.from(container.querySelectorAll("svg.rel-map-figure a")) as Element[]
        for (const node of drawn) {
            await user.tab()
            expect(node).toHaveFocus()
        }
        // user-event only activates HTML anchors on Enter; browsers activate SVG anchors too.
        const listLink = within(screen.getByRole("list", { name: "Connected records" })).getByRole("link", { name: "The molecule" })
        await user.tab()
        expect(listLink).toHaveFocus()
        await user.keyboard("{Enter}")
        expect(screen.getByTestId("where")).toHaveTextContent("/species/spc_1")
    })

    it("wraps long labels onto lines at word boundaries and never clips with an ellipsis", () => {
        const { container } = renderMap()
        const texts = Array.from(container.querySelectorAll("svg.rel-map-figure text"))
        expect(texts.some((t) => (t.textContent ?? "").includes("\u2026"))).toBe(false)
        const reactions = renderMap([{ id: "r", kind: "reaction", label: "Reactions with this molecule", relation: "x", to: null, found: "4 reaction entries" }])
        const label = Array.from(reactions.container.querySelectorAll("svg.rel-map-figure text.rel-map-label")).pop()!
        expect(Array.from(label.querySelectorAll("tspan")).map((t) => t.textContent)).toEqual(["Reactions with this", "molecule"])
    })

    it("draws a 30-character label with no spaces on several lines, none clipped", () => {
        const label = "CC(C)(C)OC(=O)N1CCC(CC1)C(=O)O"
        const { container } = renderMap([{ id: "p", kind: "species-entry", label, relation: "Reactant", to: "/species-entries/x", found: null }])
        const spans = Array.from(container.querySelectorAll("svg.rel-map-figure text.rel-map-label tspan")).map((t) => t.textContent ?? "")
        expect(spans.join("")).toBe(label)
        for (const span of spans) expect(span.length).toBeLessThanOrEqual(21)
        expect(container.querySelector("svg.rel-map-figure")!.textContent).not.toContain("\u2026")
    })

    it("says so when nothing is connected, and while it is loading", () => {
        renderMap([], "ready")
        expect(screen.getByText("No connected records were found.")).toBeInTheDocument()
        cleanup()
        renderMap([], "loading")
        expect(screen.getByText("Loading connected records.")).toBeInTheDocument()
    })

    it("hides the drawing and keeps the list below 560px, and keeps its labels at least 12px", () => {
        expect(mapCss).toMatch(/@media \(max-width: 560px\) \{\s*\.rel-map-figure \{\s*display: none;/)
        const sizes = [...mapCss.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1]))
        expect(sizes.length).toBeGreaterThan(0)
        expect(Math.min(...sizes)).toBeGreaterThanOrEqual(12)
    })
})
