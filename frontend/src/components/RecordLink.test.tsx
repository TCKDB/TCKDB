import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import { calculationWords, geometryWords, looksLikeRef, versionWords } from "../domain/recordWords"
import { ParticipantLines, RecordLink } from "./RecordLink"

const REF = "rxe_kkakfn257abcdefghijklmnop"

afterEach(cleanup)

describe("RecordLink", () => {
    it("reads as words and names the kind in its accessible name", () => {
        render(<MemoryRouter><RecordLink kind="reaction-entry" to={`/reaction-entries/${REF}`} label={versionWords(2)} /></MemoryRouter>)
        const link = screen.getByRole("link", { name: "Reaction entry: Version 2" })
        expect(link).toHaveTextContent("Version 2")
        expect(link).toHaveAttribute("href", `/reaction-entries/${REF}`)
    })

    it("tells repeated words apart through a context", () => {
        render(<MemoryRouter>
            <RecordLink kind="calculation" to="/calculations/a" label={calculationWords("sp")} context="energy" />
            <RecordLink kind="calculation" to="/calculations/b" label={calculationWords("sp")} context="geometry" />
        </MemoryRouter>)
        expect(screen.getByRole("link", { name: "Calculation: Single-point energy, energy" })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: "Calculation: Single-point energy, geometry" })).toBeInTheDocument()
    })

    it("never prints a ref as its text", () => {
        render(<MemoryRouter><RecordLink kind="reaction-entry" to="/x" label={REF} /></MemoryRouter>)
        expect(screen.getByRole("link")).toHaveTextContent("Reaction entry")
        expect(document.body.textContent).not.toContain(REF)
    })

    it("carries a detail line and an anchor", () => {
        render(<MemoryRouter><RecordLink kind="kinetics" to="/reaction-entries/e" hash="kinetics-k" label="Record 1" detail="Modified Arrhenius" /></MemoryRouter>)
        expect(screen.getByRole("link")).toHaveAttribute("href", "/reaction-entries/e#kinetics-k")
        expect(screen.getByText("Modified Arrhenius")).toBeInTheDocument()
    })
})

describe("ParticipantLines", () => {
    it("puts each participant in its own list item", () => {
        render(<ParticipantLines>{[<span key="a">CH4</span>, <span key="b">OH</span>]}</ParticipantLines>)
        expect(screen.getAllByRole("listitem")).toHaveLength(2)
        expect(document.body.textContent).not.toContain("·")
    })
})

describe("record words", () => {
    it("recognises public refs", () => {
        expect(looksLikeRef(REF)).toBe(true)
        expect(looksLikeRef("Version 2")).toBe(false)
        expect(looksLikeRef("Single-point energy")).toBe(false)
    })

    it("words a geometry by its atom count", () => {
        expect(geometryWords(12)).toBe("Geometry, 12 atoms")
        expect(geometryWords(1)).toBe("Geometry, 1 atom")
        expect(geometryWords(null)).toBe("Geometry")
    })
})
