import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { PageSectionsProvider, SectionHeading } from "./PageSections"
import { TableOfContents, MIN_SECTIONS_FOR_TOC } from "./TableOfContents"

afterEach(cleanup)

/** A tiny page-shaped harness: `count` real `<h2>` sections, each
 *  registering itself the same way a real page's `SectionHeading` call
 *  sites do, plus the ToC that reads that registration live. */
function Harness({ count }: { count: number }) {
    return (
        <PageSectionsProvider>
            <TableOfContents />
            {Array.from({ length: count }, (_, index) => (
                <SectionHeading id={`section-${index}`} key={index}>{`Section ${index}`}</SectionHeading>
            ))}
        </PageSectionsProvider>
    )
}

describe("TableOfContents: the 4+ section threshold", () => {
    it(`renders nothing below ${MIN_SECTIONS_FOR_TOC} registered sections`, () => {
        render(<Harness count={MIN_SECTIONS_FOR_TOC - 1} />)
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
    })

    it(`renders a link per section at exactly ${MIN_SECTIONS_FOR_TOC}`, () => {
        render(<Harness count={MIN_SECTIONS_FOR_TOC} />)
        const nav = screen.getByRole("navigation", { name: "Sections on this page" })
        const links = screen.getAllByRole("link")
        expect(links).toHaveLength(MIN_SECTIONS_FOR_TOC)
        expect(links[0]).toHaveAttribute("href", "#section-0")
        expect(links[0]).toHaveTextContent("Section 0")
        expect(nav).toBeVisible()
    })

    it("disappears again once enough sections unmount to drop back below the threshold", () => {
        const { rerender } = render(<Harness count={MIN_SECTIONS_FOR_TOC} />)
        expect(screen.getByRole("navigation", { name: "Sections on this page" })).toBeInTheDocument()
        rerender(<Harness count={MIN_SECTIONS_FOR_TOC - 1} />)
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
    })
})

describe("TableOfContents: active-section marking", () => {
    function stubTop(id: string, top: number) {
        const el = document.getElementById(id)
        if (!el) throw new Error(`No element #${id}`)
        el.getBoundingClientRect = () => ({
            top, left: 0, right: 0, bottom: top, width: 0, height: 0, x: 0, y: top,
            toJSON: () => ({}),
        })
    }

    it("marks the section nearest the top of the viewport active, and updates on scroll", () => {
        render(<Harness count={MIN_SECTIONS_FOR_TOC} />)

        // Page-top state: only section 0 has reached the active offset,
        // the rest are still below the fold. jsdom has no real layout, so
        // this is stubbed explicitly rather than relied on as a default.
        stubTop("section-0", 40)
        stubTop("section-1", 500)
        stubTop("section-2", 900)
        stubTop("section-3", 1300)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 0" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 1" })).not.toHaveAttribute("aria-current")

        // Scroll section 1's heading up past the active-offset threshold;
        // sections 2/3 stay below it (unscrolled-to yet).
        stubTop("section-0", -400)
        stubTop("section-1", 40)
        stubTop("section-2", 800)
        stubTop("section-3", 1200)
        fireEvent.scroll(window)

        expect(screen.getByRole("link", { name: "Section 1" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 1" })).toHaveClass("page-toc-active")
        expect(screen.getByRole("link", { name: "Section 0" })).not.toHaveAttribute("aria-current")
        expect(screen.getByRole("link", { name: "Section 2" })).not.toHaveAttribute("aria-current")
    })

    it("moves the active marker forward again as the reader scrolls past a later section", () => {
        render(<Harness count={MIN_SECTIONS_FOR_TOC} />)
        stubTop("section-0", -800)
        stubTop("section-1", -400)
        stubTop("section-2", 20)
        stubTop("section-3", 900)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 1" })).not.toHaveAttribute("aria-current")
    })
})
