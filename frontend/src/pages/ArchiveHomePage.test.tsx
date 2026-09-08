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

// Methods (still `RecordPlaceholderPage`) stays labelled "Coming soon";
// Browse reactions used to carry that SAME label while linking to
// `/reactions` -- which, as of the per-kind browse paths change, is a
// working 42-record index (`BrowsePage`, kind=reaction), not the
// placeholder that label described. Leaving "Coming soon" on that card
// after the fix would falsify the label and leave the owner's original
// complaint ("Reactions is a dead end") alive on the page most readers see
// first, one click more prominent than the nav link this PR actually
// fixed. The action text must say, in words, whether a destination is a
// working index or not -- accurately, for BOTH kinds of destination.
describe("ArchiveHomePage: destinations are labelled accurately -- working index vs still-unbuilt", () => {
    it("Browse reactions now says 'Open index', matching Browse species -- its destination is real now", () => {
        page()
        const reactions = screen.getByRole("link", { name: /Browse reactions/ })
        expect(reactions).toHaveAttribute("href", "/reactions")
        expect(reactions).toHaveTextContent("Open index")
        expect(reactions).not.toHaveTextContent("Coming soon")
    })

    it("Methods still says 'Coming soon' -- its destination (RecordPlaceholderPage) has not changed", () => {
        page()
        const methods = screen.getByRole("link", { name: /Methods/ })
        expect(methods).toHaveAttribute("href", "/methods")
        expect(methods).toHaveTextContent("Coming soon")
    })

    it("keeps Browse species as a working index, still saying 'Open index'", () => {
        page()
        const species = screen.getByRole("link", { name: /Browse species/ })
        expect(species).toHaveTextContent("Open index")
        expect(species).not.toHaveTextContent("Coming soon")
    })

    it("does not remove any destination -- all three stay real links", () => {
        page()
        expect(screen.getByRole("link", { name: /Browse species/ })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: /Browse reactions/ })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: /Methods/ })).toBeInTheDocument()
    })
})
