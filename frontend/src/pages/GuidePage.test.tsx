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

    it("has an entry for every guide anchor that a page links to", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        const ids = new Set(screen.getAllByRole("heading", { level: 2 }).map((h) => h.id))
        const sources = import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>
        const linked = new Set<string>()
        for (const [path, text] of Object.entries(sources)) {
            if (/\.test\.tsx?$/.test(path)) continue
            for (const match of text.matchAll(/<GuideLink to="([^"]+)"/g)) linked.add(match[1])
            for (const match of text.matchAll(/\/guide#([a-z-]+)/g)) linked.add(match[1])
        }
        expect(linked.size).toBeGreaterThan(5)
        expect([...linked].filter((id) => !ids.has(id))).toEqual([])
    })

    it("sets the page title", () => {
        render(<MemoryRouter><GuidePage /></MemoryRouter>)
        expect(document.title).toBe("Guide · TCKDB")
    })
})
