import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
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
