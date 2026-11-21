import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { methodCatalogueCardSchema, basisSetCatalogueCardSchema } from "../api/catalogueApi"
import { CatalogueCard, NotCatalogued } from "./CatalogueCard"
import { auxCard, gfn2Card, gfn2Reference } from "../test/catalogueFixtures"

afterEach(cleanup)

function renderCard(card: Record<string, unknown>, noun = "method") {
    const parsed = "role" in card && !("kind" in card) ? basisSetCatalogueCardSchema.parse(card) : methodCatalogueCardSchema.parse(card)
    return render(<MemoryRouter><CatalogueCard card={parsed} noun={noun} headingId="card-heading" /></MemoryRouter>)
}

describe("CatalogueCard", () => {
    it("shows the short and full name, family, description, spellings, usage and the entry link", () => {
        renderCard(gfn2Card())
        const card = screen.getByRole("region", { name: "About this method" })
        expect(within(card).getByText("GFN2-xTB")).toBeInTheDocument()
        expect(within(card).getByText("Geometry, Frequency, Noncovalent, eXtended Tight Binding, second parametrisation")).toBeInTheDocument()
        expect(within(card).getByText("semiempirical")).toBeInTheDocument()
        expect(within(card).getByText("A self-consistent tight-binding method with multipole electrostatics.")).toBeInTheDocument()
        expect(within(card).getByText("gfn2")).toBeInTheDocument()
        expect(within(card).getByText("Used by 1 level of theory · 2 calculations")).toBeInTheDocument()
        expect(within(card).getByRole("link", { name: "Open the method page" })).toHaveAttribute("href", "/methods/method/gfn2-xtb")
    })

    it("cites each reference under its role, with authors, journal, year, volume and pages", () => {
        renderCard(gfn2Card())
        expect(screen.getByText("Original article")).toBeInTheDocument()
        expect(screen.getByText("Implementation")).toBeInTheDocument()
        expect(screen.getByText("Bannwarth, Ehlert, Grimme.")).toBeInTheDocument()
        expect(screen.getByText("Journal of Chemical Theory and Computation 2019, 15, 1652–1671.")).toBeInTheDocument()
        // More than six authors end in "et al.".
        expect(screen.getByText("Bannwarth, Caldeweyher, Ehlert, Hansen, Pracht, Seibert et al.")).toBeInTheDocument()
    })

    it("links a DOI only when the server served one", () => {
        renderCard(gfn2Card({ references: [gfn2Reference({ doi: null })] }))
        expect(screen.getByText("Bannwarth, Ehlert, Grimme.")).toBeInTheDocument()
        expect(screen.queryByRole("link", { name: /doi:/ })).toBeNull()
        cleanup()

        renderCard(gfn2Card({ references: [gfn2Reference()] }))
        expect(screen.getByRole("link", { name: "doi:10.1021/acs.jctc.8b01176" })).toHaveAttribute("href", "https://doi.org/10.1021/acs.jctc.8b01176")
    })

    it("prints no depositor text for a reference the registry did not resolve, even when a DOI arrives", () => {
        renderCard(gfn2Card({ references: [gfn2Reference({ title_source: "user_supplied", journal: "Depositor Journal", authors: ["Typed"] })] }))
        expect(screen.getByText("Title not verified")).toBeInTheDocument()
        expect(screen.getByText(/lit_gfn2/)).toBeInTheDocument()
        expect(screen.queryByText(/Depositor Journal/)).toBeNull()
        expect(screen.queryByText(/Typed/)).toBeNull()
        expect(screen.queryByRole("link", { name: /doi:/ })).toBeNull()
    })

    it("words a basis set's family and role, and links an entry whose key holds a slash by its ref", () => {
        renderCard(auxCard(), "auxiliary basis set")
        const card = screen.getByRole("region", { name: "About this auxiliary basis set" })
        expect(within(card).getByText("Dunning correlation-consistent · auxiliary basis, correlation fitting")).toBeInTheDocument()
        expect(within(card).getByText("No reference is recorded for this entry.")).toBeInTheDocument()
        expect(within(card).getByRole("link", { name: "Open the basis set page" })).toHaveAttribute("href", "/methods/basis/bcat_augccpvtzc")
    })
})

describe("NotCatalogued", () => {
    it("says plainly that a spelling is not in the catalogue", () => {
        const { container } = render(<NotCatalogued noun="basis set" spelling="cc-pvtz-f12" />)
        expect(container).toHaveTextContent("Basis set cc-pvtz-f12: not in the catalogue.")
    })
})
