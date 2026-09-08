import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { BROWSE_KINDS, BROWSE_KIND_LABELS, BROWSE_KIND_PATHS } from "../api/browseApi"
import type { BrowseKind } from "../api/browseApi"
import { BrowseKindSelector } from "./BrowseKindSelector"

afterEach(cleanup)

function renderSelector(kind: BrowseKind, onSelect = vi.fn()) {
    render(
        <MemoryRouter>
            <BrowseKindSelector kind={kind} onSelect={onSelect} />
        </MemoryRouter>,
    )
    return onSelect
}

// Owner, three times: "Why is the browse reactions with the species/
// transition/vanderwaals browsing page?" / "there should be separate for
// reaction and species not slammed together". A `role="radio"` control
// frames the four kinds as MODES of one shared page; this suite pins the
// demotion to a plain navigation list of links to the OTHER kinds.
describe("BrowseKindSelector: demoted from a radiogroup to plain links", () => {
    // MUTATION CHECK (mutation table item e): reintroducing the
    // `<fieldset>`/`<legend>`/`role="radio"` markup is exactly the
    // regression this guards against.
    it("renders NO radio inputs and NO fieldset/legend -- it is a nav of links, not a radiogroup", () => {
        renderSelector("species")
        expect(screen.queryAllByRole("radio")).toHaveLength(0)
        expect(document.querySelector("fieldset")).toBeNull()
        expect(document.querySelector("legend")).toBeNull()
    })

    it("is a <nav> with an accessible name", () => {
        renderSelector("species")
        expect(screen.getByRole("navigation", { name: "Browse a different kind" })).toBeInTheDocument()
    })

    it.each(BROWSE_KINDS)("on kind=%s, every OTHER kind renders as a real link with the right href, and the CURRENT kind does not appear at all", (kind) => {
        renderSelector(kind)
        const nav = screen.getByRole("navigation", { name: "Browse a different kind" })
        const links = within(nav).getAllByRole("link")
        const otherKinds = BROWSE_KINDS.filter((option) => option !== kind)
        expect(links).toHaveLength(otherKinds.length)
        for (const option of otherKinds) {
            const link = within(nav).getByRole("link", { name: BROWSE_KIND_LABELS[option] })
            expect(link).toHaveAttribute("href", BROWSE_KIND_PATHS[option])
        }
        // The current kind's own label must not appear as a link here --
        // a page does not link to itself in its own "browse elsewhere" nav.
        expect(within(nav).queryByRole("link", { name: BROWSE_KIND_LABELS[kind] })).not.toBeInTheDocument()
    })

    it("clicking a link calls onSelect with that kind", async () => {
        const user = userEvent.setup()
        const onSelect = renderSelector("species")
        const nav = screen.getByRole("navigation", { name: "Browse a different kind" })
        await user.click(within(nav).getByRole("link", { name: "Reaction" }))
        expect(onSelect).toHaveBeenCalledWith("reaction")
        expect(onSelect).toHaveBeenCalledTimes(1)
    })

    it("each link is a real <a> (not a button pretending to be one) -- keyboard/middle-click/right-click all work the normal way", () => {
        renderSelector("transition_state")
        const nav = screen.getByRole("navigation", { name: "Browse a different kind" })
        const link = within(nav).getByRole("link", { name: "Species" })
        expect(link.tagName).toBe("A")
        expect(link).toHaveAttribute("href", "/species")
    })
})
