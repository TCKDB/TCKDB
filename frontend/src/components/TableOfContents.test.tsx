import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { PageSectionsProvider, SectionHeading } from "./PageSections"
import { TableOfContents, MIN_SECTIONS_FOR_LIST } from "./TableOfContents"

afterEach(cleanup)

/** A tiny page-shaped harness: `count` real `<h2>` sections, each
 *  registering itself the same way a real page's `SectionHeading` call
 *  sites do, plus the ToC that reads that registration live. Wrapped in a
 *  `MemoryRouter` -- `TableOfContents` reads `useLocation()` to resolve a
 *  `#fragment` -- so `initialEntries` lets a test start on a specific
 *  path/search/hash. */
function Harness({ count, initialEntries = ["/"] }: { count: number; initialEntries?: string[] }) {
    return (
        <MemoryRouter initialEntries={initialEntries}>
            <PageSectionsProvider>
                <TableOfContents />
                {Array.from({ length: count }, (_, index) => (
                    <SectionHeading id={`section-${index}`} key={index}>{`Section ${index}`}</SectionHeading>
                ))}
            </PageSectionsProvider>
        </MemoryRouter>
    )
}

describe("TableOfContents: the reserved column", () => {
    it("always renders the column, even below the list threshold", () => {
        render(<Harness count={MIN_SECTIONS_FOR_LIST - 1} />)
        expect(document.querySelector(".page-toc")).not.toBeNull()
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
    })

    it("renders the same reserved column element at 1 section and at 4 sections -- a tab switch does not mount/unmount it", () => {
        const { rerender } = render(<Harness count={1} />)
        expect(document.querySelector(".page-toc")).not.toBeNull()
        rerender(<Harness count={4} />)
        const columns = document.querySelectorAll(".page-toc")
        // Exactly one column, still -- not a second one appended, not the
        // first one replaced by a differently-shaped element.
        expect(columns).toHaveLength(1)
        expect(columns[0]).not.toBeNull()
    })

    it(`renders no list below ${MIN_SECTIONS_FOR_LIST} registered sections`, () => {
        render(<Harness count={MIN_SECTIONS_FOR_LIST - 1} />)
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
    })

    // Hardcoded to the literal 1/2, NOT `MIN_SECTIONS_FOR_LIST -/+ 1` --
    // every other test in this describe block reads the threshold
    // symbolically off the same constant it is testing, which would keep
    // passing unchanged if that constant's VALUE drifted away from 2. This
    // one pins the actual number the design brief asks for.
    it("renders no list at exactly 1 registered section, and a list at exactly 2", () => {
        const { rerender } = render(<Harness count={1} />)
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
        rerender(<Harness count={2} />)
        expect(screen.getByRole("navigation", { name: "Sections on this page" })).toBeInTheDocument()
        expect(screen.getAllByRole("link")).toHaveLength(2)
    })

    it(`renders a link per section at exactly ${MIN_SECTIONS_FOR_LIST}`, () => {
        render(<Harness count={MIN_SECTIONS_FOR_LIST} />)
        const nav = screen.getByRole("navigation", { name: "Sections on this page" })
        const links = screen.getAllByRole("link")
        expect(links).toHaveLength(MIN_SECTIONS_FOR_LIST)
        expect(links[0]).toHaveAttribute("href", "#section-0")
        expect(links[0]).toHaveTextContent("Section 0")
        expect(nav).toBeVisible()
    })

    it("the list disappears again once enough sections unmount to drop back below the threshold, but the column itself stays", () => {
        const { rerender } = render(<Harness count={MIN_SECTIONS_FOR_LIST} />)
        expect(screen.getByRole("navigation", { name: "Sections on this page" })).toBeInTheDocument()
        rerender(<Harness count={MIN_SECTIONS_FOR_LIST - 1} />)
        expect(screen.queryByRole("navigation", { name: "Sections on this page" })).not.toBeInTheDocument()
        expect(document.querySelector(".page-toc")).not.toBeNull()
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

    /** Stubs the document as NOT at the bottom of its scroll range --
     *  the default the `computeActive`/bottom-clamp tests need to
     *  override deliberately, and every OTHER test in this file relies on
     *  implicitly: jsdom reports `scrollHeight: 0` by default, which
     *  `innerHeight + scrollY >= scrollHeight - epsilon` would otherwise
     *  read as "already at the bottom" on every single render. */
    function stubScrollRoom() {
        Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 4000 })
        Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 })
        Object.defineProperty(window, "scrollY", { configurable: true, value: 0, writable: true })
    }

    function stubAtBottom(scrollHeight: number) {
        Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: scrollHeight })
        Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 })
        Object.defineProperty(window, "scrollY", { configurable: true, value: scrollHeight - 800, writable: true })
    }

    it("marks the section nearest the top of the viewport active, and updates on scroll", () => {
        // A fixed literal count (not `MIN_SECTIONS_FOR_LIST + 2`): every
        // rendered section is individually stubbed below, so this must not
        // grow silently if the threshold constant changes -- an unstubbed
        // section defaults to jsdom's zero `getBoundingClientRect`, which
        // reads as "reached", and being last in iteration order would win
        // the active computation and break this test for a reason that has
        // nothing to do with what it is actually testing.
        render(<Harness count={4} />)
        stubScrollRoom()

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
        render(<Harness count={4} />)
        stubScrollRoom()
        stubTop("section-0", -800)
        stubTop("section-1", -400)
        stubTop("section-2", 20)
        stubTop("section-3", 900)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 1" })).not.toHaveAttribute("aria-current")
    })

    it("activates the LAST section once the viewport reaches the bottom of the page, even though its heading never crosses the offset -- the reported bug (\"the page cannot move further down to make Torsions the top of the page\")", () => {
        render(<Harness count={4} />)
        // Every heading stays well below the active offset -- the short-page
        // case where the document runs out of room before any later
        // section's top can ever reach 160px from the viewport top.
        stubTop("section-0", 40)
        stubTop("section-1", 500)
        stubTop("section-2", 700)
        stubTop("section-3", 750)
        stubAtBottom(1550)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 3" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 0" })).not.toHaveAttribute("aria-current")
    })

    it("an intermediate section (between the last offset-reachable one and the true last) is independently selectable by clicking it, without needing a scroll event to land there", () => {
        render(<Harness count={4} />)
        stubScrollRoom()
        // Nothing has scrolled yet -- section 0 is active by default.
        stubTop("section-0", 40)
        stubTop("section-1", 900)
        stubTop("section-2", 1200)
        stubTop("section-3", 1500)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 0" })).toHaveAttribute("aria-current", "true")

        // Clicking "Section 2" (not the last, and not yet reachable by the
        // offset computation above) must mark it active immediately.
        fireEvent.click(screen.getByRole("link", { name: "Section 2" }))
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 0" })).not.toHaveAttribute("aria-current")
        expect(screen.getByRole("link", { name: "Section 3" })).not.toHaveAttribute("aria-current")
    })

    it("resolves a #fragment to the right section even with a query string present", () => {
        render(<Harness count={4} initialEntries={["/species-entries/spe_demo/statmech?conformer=cg_1#section-2"]} />)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
    })
})
