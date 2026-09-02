import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import ArchiveHomePage from "./ArchiveHomePage"

afterEach(cleanup)

function page() {
    return render(<MemoryRouter><ArchiveHomePage /></MemoryRouter>)
}

/**
 * "why is there a struct search with smiles box on the front page" -- the
 * owner, after an earlier pass put `<StructureSearch />` there. It moved
 * (folded into `BrowseFilterForm.tsx`'s composition fields, per a later
 * correction -- "just make the struct and smiles search part of the
 * browser-filters class"). The front page keeps ONLY the exact-identifier
 * search (`IdentifierSearch`).
 */
describe("ArchiveHomePage: no structure/SMILES search on the front page", () => {
    it("renders the exact-identifier search", () => {
        page()
        expect(screen.getByLabelText("Exact species identifier")).toBeInTheDocument()
    })

    it("does not render a structure-search mode fieldset, a SMARTS toggle, or a 'Search structures' control", () => {
        page()
        expect(screen.queryByText("Structure search")).not.toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Search structures" })).not.toBeInTheDocument()
        expect(screen.queryByText("Search mode")).not.toBeInTheDocument()
        expect(screen.queryByLabelText(/SMARTS/)).not.toBeInTheDocument()
    })

    it("still links to Browse species, where structure search now lives", () => {
        page()
        expect(screen.getByRole("link", { name: /Browse species/ })).toHaveAttribute("href", "/species")
    })
})
