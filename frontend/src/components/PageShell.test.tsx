import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { PageShell } from "./PageShell"
import { SectionHeading } from "./PageSections"

afterEach(cleanup)

/**
 * The ToC's visual side ("I want ToC on the right I think") is a CSS
 * `order` value, not testable through jsdom's layout-free DOM -- but the
 * markup order underneath it is real and a11y-load-bearing regardless of
 * which side the CSS puts it on: a keyboard/screen-reader user must reach
 * this page's own content before the navigation rail that follows it, the
 * same order a footnote reads in relative to the prose that cites it.
 */
describe("PageShell: content precedes the ToC in document order", () => {
    it("renders the content pane before the .page-toc column in the DOM, regardless of the ToC's visual (CSS) side", () => {
        const { container } = render(
            <MemoryRouter>
                <PageShell>
                    <p data-testid="content-marker">Page content</p>
                    <SectionHeading id="one">One</SectionHeading>
                    <SectionHeading id="two">Two</SectionHeading>
                </PageShell>
            </MemoryRouter>,
        )
        const layout = container.querySelector(".page-shell-layout") as HTMLElement
        const content = layout.querySelector(".page-shell-content") as HTMLElement
        const toc = layout.querySelector(".page-toc") as HTMLElement
        expect(content).not.toBeNull()
        expect(toc).not.toBeNull()
        expect(content.querySelector('[data-testid="content-marker"]')).not.toBeNull()
        // `compareDocumentPosition` -- not array index on `layout.children`,
        // which would pass even if either element were nested deeper (this
        // is exactly that shape: `.page-toc` is `TableOfContents`'s own
        // rendered root, `.page-shell-content` wraps the page's children).
        expect(content.compareDocumentPosition(toc) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })
})

/**
 * ToC-top-alignment (follow-up to PR #321/#322/#323): the owner's chosen
 * mockup over the rejected "keep the header full-width, just tighten the
 * sticky offset" alternative -- "the ToC column starts level with the
 * identity/header block, just under the breadcrumbs, with the header
 * narrowing to make room". That is only true if `identity` renders INSIDE
 * `.page-shell-content` (the flex row's own content column), not as a
 * sibling ABOVE `.page-shell-layout` spanning full width -- a full-width
 * sibling is exactly what a reader would see if `PageShell` reverted to
 * its pre-follow-up shape, so this test asserts the containment directly
 * rather than only asserting `identity` renders somewhere on the page.
 */
describe("PageShell: the identity slot renders inside the content column", () => {
    it("nests the identity block inside .page-shell-content, not as a sibling above .page-shell-layout", () => {
        const { container } = render(
            <MemoryRouter>
                <PageShell identity={<h1 data-testid="identity-marker">Formula</h1>}>
                    <p data-testid="content-marker">Page content</p>
                </PageShell>
            </MemoryRouter>,
        )
        const layout = container.querySelector(".page-shell-layout") as HTMLElement
        const content = layout.querySelector(".page-shell-content") as HTMLElement
        const identity = screen.getByTestId("identity-marker")
        expect(content).not.toBeNull()
        // Positive containment assertion -- `.page-shell-content` really
        // does contain the identity node, not merely "the identity node
        // exists somewhere in the document".
        expect(content.contains(identity)).toBe(true)
        // And it is the FIRST thing in that column, ahead of the page's
        // own children -- so the header, not arbitrary page content, is
        // what the ToC rail lines up against at the top.
        expect(content.firstElementChild).toBe(container.querySelector(".page-shell-identity"))
        expect(content.firstElementChild?.contains(identity)).toBe(true)
        // Negative half of the same claim: nothing sits between
        // `.page-shell-layout`'s open tag and the identity's ancestor --
        // i.e. identity is NOT a sibling rendered above the flex row.
        expect(container.firstElementChild).toBe(layout)
        expect(layout.contains(identity)).toBe(true)
    })
})
