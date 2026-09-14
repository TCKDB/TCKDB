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
    it("renders the exact-identifier search, defaulting to species mode", () => {
        page()
        expect(screen.getByLabelText("Exact species identifier")).toBeInTheDocument()
        expect(screen.getByRole("radio", { name: "Species" })).toHaveAttribute("aria-checked", "true")
        expect(screen.getByRole("radio", { name: "Reactions" })).toHaveAttribute("aria-checked", "false")
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

// Browse reactions used to carry the SAME "Coming soon" label while
// linking to `/reactions` -- which, as of the per-kind browse paths
// change, is a working 42-record index (`BrowsePage`, kind=reaction), not
// the placeholder that label described. Leaving "Coming soon" on that
// card after the fix would falsify the label and leave the owner's
// original complaint ("Reactions is a dead end") alive on the page most
// readers see first, one click more prominent than the nav link this PR
// actually fixed. The action text must say, in words, whether a
// destination is a working index or not -- accurately, for every
// destination. Methods got the same fix next (methods-surface plan
// PR 3): `/methods` renders `MethodsIndexPage` now, not
// `RecordPlaceholderPage`, so it drops "Coming soon" too -- all three
// cards say "Open index →" today, with no placeholder card left on this
// page to prove the rule still discriminates.
describe("ArchiveHomePage: destinations are labelled accurately -- all three are working indexes now", () => {
    it("Browse reactions says 'Open index', matching Browse species -- its destination is real", () => {
        page()
        const reactions = screen.getByRole("link", { name: /Browse reactions/ })
        expect(reactions).toHaveAttribute("href", "/reactions")
        expect(reactions).toHaveTextContent("Open index")
        expect(reactions).not.toHaveTextContent("Coming soon")
    })

    it("Methods says 'Open index' now -- its destination (MethodsIndexPage) is real, not RecordPlaceholderPage", () => {
        page()
        const methods = screen.getByRole("link", { name: /Methods/ })
        expect(methods).toHaveAttribute("href", "/methods")
        expect(methods).toHaveTextContent("Open index")
        expect(methods).not.toHaveTextContent("Coming soon")
    })

    it("keeps Browse species as a working index, still saying 'Open index'", () => {
        page()
        const species = screen.getByRole("link", { name: /Browse species/ })
        expect(species).toHaveTextContent("Open index")
        expect(species).not.toHaveTextContent("Coming soon")
    })

    it("no destination card says 'Coming soon' any more -- the last placeholder is gone", () => {
        page()
        expect(screen.queryByText("Coming soon")).not.toBeInTheDocument()
    })

    it("does not remove any destination -- all three stay real links", () => {
        page()
        expect(screen.getByRole("link", { name: /Browse species/ })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: /Browse reactions/ })).toBeInTheDocument()
        expect(screen.getByRole("link", { name: /Methods/ })).toBeInTheDocument()
    })
})
