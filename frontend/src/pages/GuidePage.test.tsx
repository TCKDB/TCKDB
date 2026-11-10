import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import GuidePage from "./GuidePage"

afterEach(cleanup)

describe("GuidePage", () => {
    it("has one h1 and eight concept headings, each with a definition", () => {
        const { container } = render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        const headings = screen.getAllByRole("heading", { level: 2 })
        expect(headings.map((h) => h.textContent)).toEqual([
            "Molecule (species)", "Molecule version (species entry)", "Conformer", "Calculation", "Level of theory", "Reaction", "Transition state", "Network",
        ])
        expect(container.querySelectorAll("dfn")).toHaveLength(8)
        expect(container.querySelectorAll(".guide-concepts li p")).toHaveLength(8)
    })

    it("states the ordering note under #order", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(document.getElementById("order")).toHaveTextContent("A list's order is never a recommendation. Each list says how it is ordered.")
    })

    it("sets the page title", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(document.title).toBe("Guide · TCKDB")
    })
})
