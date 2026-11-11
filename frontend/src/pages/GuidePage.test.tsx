import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { GUIDE_TOPICS } from "../domain/glossary"
import GuidePage from "./GuidePage"

afterEach(cleanup)

describe("GuidePage", () => {
    it("has one h1, ten record concepts and the reading topics, each with a definition", () => {
        const { container } = render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)
        expect(headings.slice(0, 10)).toEqual([
            "Molecule (species)", "Molecule version (species entry)", "Conformer", "Observation", "Calculation", "Level of theory", "Reaction", "Reaction entry", "Transition state", "Network",
        ])
        expect(headings.slice(10)).toEqual([
            "How lists are ordered", "Geometry sharing", "Energy profile", "Transition-state checks", "Level-of-theory identity", "What a level carries",
            "Correction scheme", "Composite recipe", "Staged optimisation", "How a molecule is drawn", "Relative energies", "Not recorded",
        ])
        expect(container.querySelectorAll("dfn")).toHaveLength(headings.length)
        expect(container.querySelectorAll(".guide-concepts li p")).toHaveLength(headings.length)
    })

    it("keeps each topic to one or two plain sentences", () => {
        for (const topic of GUIDE_TOPICS) {
            expect(topic.text.split(/(?<=[.!?])\s+/).length).toBeLessThanOrEqual(2)
        }
    })

    it("states the ordering note under #order", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(document.getElementById("order")).toHaveTextContent("How lists are ordered")
        expect(document.getElementById("order")?.closest("li")).toHaveTextContent("A list's order is never a recommendation.")
    })

    it("sets the page title", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(document.title).toBe("Guide · TCKDB")
    })
})
