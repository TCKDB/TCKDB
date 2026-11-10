import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { Lede } from "./Lede"
import { ValueLink } from "./ValueLink"
import { ValueSource } from "./ValueSource"
import { OrderNote } from "./OrderNote"
import { DepositorNote } from "./DepositorNote"
import { Breadcrumbs } from "./Breadcrumbs"
import { DEPOSITOR_SENTINEL } from "../test/depositorSentinel"

afterEach(cleanup)

const inRouter = (node: React.ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>)

describe("Lede", () => {
    it("renders the sentence it is given as one paragraph", () => {
        inRouter(<Lede>A computed species entry with 3 calculations.</Lede>)
        expect(screen.getByText("A computed species entry with 3 calculations.").tagName).toBe("P")
    })
})

describe("ValueLink", () => {
    it("links the value and unit to the evidence, with the unit in a .u span that keeps its case", () => {
        inRouter(<ValueLink value="-74.5" unit="kJ/mol" to="/calculations/calc_a" />)
        const link = screen.getByRole("link")
        expect(link).toHaveAttribute("href", "/calculations/calc_a")
        expect(within(link).getByText("-74.5")).toHaveClass("value-link-number")
        const unit = within(link).getByText("kJ/mol")
        expect(unit).toHaveClass("u")
        expect(unit).toHaveClass("t-preserve-case")
    })

    it("is plain text, not a dead link, when no page exists for the evidence", () => {
        inRouter(<ValueLink value="12.3" unit="J/(mol K)" to={null} />)
        expect(screen.queryByRole("link")).toBeNull()
        expect(screen.getByText("12.3")).toBeVisible()
    })
})

describe("ValueSource", () => {
    it("shows the calculation link, what it did, level, program and review status", () => {
        inRouter(<ValueSource calculationRef="calc_a" did="single point" levelOfTheory="CCSD(T)-F12/cc-pVTZ-F12" program="Molpro 2024" reviewStatus="approved" />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(within(line).getByRole("link")).toHaveAttribute("href", "/calculations/calc_a")
        for (const text of ["Source", "single point", "CCSD(T)-F12/cc-pVTZ-F12", "Molpro 2024", "approved"]) {
            expect(line).toHaveTextContent(text)
        }
    })

    it("shows nothing for a part the record lacks and never invents one", () => {
        inRouter(<ValueSource calculationRef="calc_a" />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(line.textContent).toBe("Source calc_a")
        expect(line.querySelector(".review-chip")).toBeNull()
    })

    it("treats empty strings as missing", () => {
        inRouter(<ValueSource calculationRef="calc_a" did="" program="" levelOfTheory="" />)
        expect(document.querySelector(".value-source")?.textContent).toBe("Source calc_a")
    })

    it("has a short form that is only a link", () => {
        inRouter(<ValueSource calculationRef="calc_a" did="opt" compact />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(line).toHaveClass("value-source--compact")
        expect(line.textContent).toBe("from calc_a")
    })
})

describe("OrderNote", () => {
    it("says deposit order is not a recommendation and links to the guide", () => {
        inRouter(<OrderNote />)
        expect(document.querySelector(".order-note")).toHaveTextContent("Listed in deposit order. This order is not a recommendation.")
        expect(screen.getByRole("link")).toHaveAttribute("href", "/guide#order")
    })

    it("states another order truthfully when a list has one", () => {
        inRouter(<OrderNote order="Listed by review status, then newest first." />)
        const note = document.querySelector(".order-note") as HTMLElement
        expect(note).toHaveTextContent("Listed by review status, then newest first. This order is not a recommendation.")
        expect(note).not.toHaveTextContent("deposit order")
    })
})

describe("DepositorNote", () => {
    it("quotes the depositor's text under a visible label", () => {
        inRouter(<DepositorNote>{`${DEPOSITOR_SENTINEL}_note_1`}</DepositorNote>)
        const note = screen.getByLabelText("Note from the depositor")
        expect(within(note).getByText("Note from the depositor")).toBeVisible()
        expect(note.querySelector("blockquote")).toHaveTextContent(`${DEPOSITOR_SENTINEL}_note_1`)
    })

    it.each([null, undefined, ""])("renders nothing for %p", (value) => {
        inRouter(<DepositorNote>{value}</DepositorNote>)
        expect(document.querySelector(".depositor-note")).toBeNull()
    })
})

describe("Breadcrumbs", () => {
    it("is a labelled nav with parents as links and the last crumb marked as the current page", () => {
        inRouter(<Breadcrumbs crumbs={[{ label: "Molecules", to: "/species" }, { label: "Ethanol", to: "/species/spc_a" }, { label: "Entry" }]} />)
        const nav = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(nav).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/species", "/species/spc_a"])
        const current = nav.querySelector('[aria-current="page"]') as HTMLElement
        expect(current).toHaveTextContent("Entry")
        expect(nav.querySelectorAll("[aria-current]")).toHaveLength(1)
    })

    it("does not link the last crumb even when it has a path", () => {
        inRouter(<Breadcrumbs crumbs={[{ label: "Molecules", to: "/species" }, { label: "Here", to: "/here" }]} />)
        expect(screen.queryByRole("link", { name: "Here" })).toBeNull()
    })

    it("renders nothing for an empty list", () => {
        inRouter(<Breadcrumbs crumbs={[]} />)
        expect(screen.queryByRole("navigation")).toBeNull()
    })
})
