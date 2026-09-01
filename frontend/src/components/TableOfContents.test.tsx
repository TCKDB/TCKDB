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

/** Reproduces the conformer-group-switch bug: a fixed "early" and "late"
 *  section always mount first, and the slot between them holds whichever
 *  evidence section is currently selected -- under a DIFFERENT id each
 *  time, the way switching conformer group swaps in a differently-id'd
 *  "Evidence for Conformer Group X" section. Its DOM position is always
 *  the same physical slot; only ITS ID, and therefore its registration
 *  time relative to "late" (already registered from the first render),
 *  changes. */
function ReorderHarness({ evidenceId }: { evidenceId: string | null }) {
    return (
        <MemoryRouter>
            <PageSectionsProvider>
                <TableOfContents />
                <SectionHeading id="early">Early</SectionHeading>
                {evidenceId && <SectionHeading id={evidenceId}>{`Evidence ${evidenceId}`}</SectionHeading>}
                <SectionHeading id="late">Late</SectionHeading>
            </PageSectionsProvider>
        </MemoryRouter>
    )
}

describe("TableOfContents: document order, not mount order", () => {
    it("re-sorts the ToC after a conformer switch swaps one evidence section for a differently-id'd one in the same slot", () => {
        const { rerender } = render(<ReorderHarness evidenceId="evidence-cg1" />)
        expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Early", "Evidence evidence-cg1", "Late"])

        // Conformer group switch: the old evidence section unmounts...
        rerender(<ReorderHarness evidenceId={null} />)
        // ...and a NEW one, under a different id, mounts into the same
        // physical slot. Its registration happens strictly after "late"
        // was already registered (from the very first render), so
        // mount-order bookkeeping alone would append it at the end.
        rerender(<ReorderHarness evidenceId="evidence-cg2" />)

        expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Early", "Evidence evidence-cg2", "Late"])
    })
})

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

    it("a click lands on that section immediately and survives the ONE scroll event the anchor-jump itself fires, then resumes normal scroll-spying on the next real scroll", () => {
        render(<Harness count={4} />)
        stubScrollRoom()
        stubTop("section-0", 40)
        stubTop("section-1", 900)
        stubTop("section-2", 1200)
        stubTop("section-3", 1500)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 0" })).toHaveAttribute("aria-current", "true")

        // Click "Section 2" -- not yet reachable by the offset computation.
        fireEvent.click(screen.getByRole("link", { name: "Section 2" }))
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")

        // The incidental scroll event the browser's own anchor-jump fires
        // right after the click, with the geometry unchanged -- this must
        // NOT immediately recompute and revert to Section 0.
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")

        // A REAL further scroll, under the reader's own control, must
        // resume normal scroll-spying rather than staying pinned forever.
        stubTop("section-0", -500)
        stubTop("section-1", 100)
        stubTop("section-2", 800)
        stubTop("section-3", 1100)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 1" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 2" })).not.toHaveAttribute("aria-current")
    })

    it("a page that is not scrollable at all keeps a clicked section active across a subsequent scroll event", () => {
        render(<Harness count={4} />)
        // A short page: little to no scroll room at all, so nothing below
        // the fold ever needs "reaching" -- unlike the tall-page click
        // test above, a genuinely non-scrollable document.
        stubAtBottom(700)
        fireEvent.click(screen.getByRole("link", { name: "Section 2" }))
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")

        // The first scroll event after the click is the incidental one
        // the anchor-jump itself fires -- suppressed regardless of the
        // page's scrollability. Fire a SECOND one, standing in for a real
        // (if tiny/rubber-band) further scroll gesture on this short page,
        // to actually exercise the "nothing to scroll-spy on this page"
        // behaviour rather than only the one-event suppression.
        fireEvent.scroll(window)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 0" })).not.toHaveAttribute("aria-current")
        expect(screen.getByRole("link", { name: "Section 3" })).not.toHaveAttribute("aria-current")
    })

    it("two distinct trailing sections, neither of which can ever cross the activation offset, are independently selectable as the reader scrolls through the final screenful -- not collapsed onto a single 'last section'", () => {
        render(<Harness count={4} />)
        // A document only 2000px tall with an 800px viewport: max scroll is
        // 1200px. Sections 2 and 3 sit close enough to the very end that
        // even at max scroll their tops (250px and 300px past max scroll)
        // never reach ACTIVE_OFFSET_PX (160px) -- both are "stuck" the way
        // Torsions was in the original bug report.
        Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 2000 })
        Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 })

        // Partway into the final screenful (scrollY 950 of a 1200 max):
        // section 2 has entered view enough to become current, section 3
        // has not yet.
        Object.defineProperty(window, "scrollY", { configurable: true, value: 950, writable: true })
        stubTop("section-0", 40 - 950)
        stubTop("section-1", 900 - 950)
        stubTop("section-2", 1450 - 950)
        stubTop("section-3", 1600 - 950)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 2" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 3" })).not.toHaveAttribute("aria-current")

        // Scrolling further within that same final screenful (scrollY
        // 1050) moves the highlight on to section 3 -- the two trailing
        // sections are reached one at a time, not both collapsed onto
        // whichever is literally last the instant scrolling stops.
        Object.defineProperty(window, "scrollY", { configurable: true, value: 1050, writable: true })
        stubTop("section-0", 40 - 1050)
        stubTop("section-1", 900 - 1050)
        stubTop("section-2", 1450 - 1050)
        stubTop("section-3", 1600 - 1050)
        fireEvent.scroll(window)
        expect(screen.getByRole("link", { name: "Section 3" })).toHaveAttribute("aria-current", "true")
        expect(screen.getByRole("link", { name: "Section 2" })).not.toHaveAttribute("aria-current")
    })
})
