import { afterEach, describe, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { PageShell } from "./PageShell"
import { SectionHeading } from "./PageSections"
import pageShellCss from "../page-shell.css?raw"

afterEach(cleanup)

/** Wide: the contents column follows the content in the markup (CSS puts it on the left). Narrow: the disclosure comes first. */
describe("PageShell: contents position in document order", () => {
    afterEach(() => { delete (window as { matchMedia?: unknown }).matchMedia })

    function renderShell() {
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
        expect(content.querySelector('[data-testid="content-marker"]')).not.toBeNull()
        return { content, toc }
    }

    it("renders the .page-toc column after the content pane on a wide screen", () => {
        const { content, toc } = renderShell()
        expect(content.compareDocumentPosition(toc) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it("renders the .page-toc column before the content pane on a narrow screen", () => {
        window.matchMedia = vi.fn().mockImplementation((query: string) => ({
            matches: query.includes("62rem"), media: query, addEventListener: () => {}, removeEventListener: () => {},
        })) as unknown as typeof window.matchMedia
        const { content, toc } = renderShell()
        expect(content.compareDocumentPosition(toc) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    })
})

/**
 * ToC-top-alignment: the ToC column starts level with the
 * identity/header block, just under the breadcrumbs, with the header
 * narrowing to make room. That is only true if `identity` renders INSIDE
 * `.page-shell-content` (the flex row's own content column), not as a
 * sibling ABOVE `.page-shell-layout` spanning full width -- a full-width
 * sibling is exactly what a reader would see if `PageShell` reverted to
 * a header spanning the full width, so this test asserts the containment directly
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
        // Negative half of the same claim: the layout is the shell's only child,
        // so identity is NOT a sibling rendered above the grid.
        expect(container.firstElementChild).toBe(container.querySelector(".page-shell"))
        expect(container.querySelector(".page-shell")?.firstElementChild).toBe(layout)
        expect(layout.contains(identity)).toBe(true)
    })
})

describe("PageShell: the contents column", () => {
    afterEach(() => { vi.useRealTimers() })

    const column = (container: HTMLElement) => container.querySelector(".page-toc")
    const tree = (children: ReactNode) => <MemoryRouter><PageShell>{children}</PageShell></MemoryRouter>

    it('contents="none" renders no column at all', () => {
        const { container } = render(<MemoryRouter><PageShell contents="none"><p>List</p></PageShell></MemoryRouter>)
        expect(column(container)).toBeNull()
    })

    it('contents="list" (the default) always has the column, with no timer that removes it later', () => {
        vi.useFakeTimers()
        const { container, rerender } = render(tree(<SectionHeading id="only">Only</SectionHeading>))
        expect(column(container)).not.toBeNull()
        act(() => { vi.advanceTimersByTime(10_000) })
        expect(column(container)).not.toBeNull()
        rerender(tree(<><SectionHeading id="one">One</SectionHeading><SectionHeading id="two">Two</SectionHeading></>))
        expect(column(container)?.querySelector("nav")).not.toBeNull()
        expect(column(container)?.hasAttribute("data-hidden")).toBe(false)
    })

    it("has the column on a wide and on a narrow screen alike, so crossing the breakpoint adds none", () => {
        const wide = render(tree(<p>Content</p>))
        expect(wide.container.querySelectorAll(".page-toc")).toHaveLength(1)
        cleanup()
        window.matchMedia = vi.fn().mockImplementation((query: string) => ({
            matches: query.includes("62rem"), media: query, addEventListener: () => {}, removeEventListener: () => {},
        })) as unknown as typeof window.matchMedia
        const narrow = render(tree(<p>Content</p>))
        expect(narrow.container.querySelectorAll(".page-toc")).toHaveLength(1)
        delete (window as { matchMedia?: unknown }).matchMedia
    })

    it("hides the empty column on a narrow screen without a gap", () => {
        expect(pageShellCss).toMatch(/\.page-toc:empty\s*\{\s*display:\s*none/)
    })
})

describe("PageShell: side column", () => {
    afterEach(() => { delete (window as { matchMedia?: unknown }).matchMedia })

    function renderWithSide() {
        const { container } = render(
            <MemoryRouter>
                <PageShell side={<p data-testid="side-marker">Map</p>}>
                    <p>Page content</p>
                    <SectionHeading id="one">One</SectionHeading>
                    <SectionHeading id="two">Two</SectionHeading>
                </PageShell>
            </MemoryRouter>,
        )
        const layout = container.querySelector(".page-shell-layout") as HTMLElement
        return {
            content: layout.querySelector(".page-shell-content") as HTMLElement,
            side: layout.querySelector(".page-shell-side") as HTMLElement,
            toc: layout.querySelector(".page-toc") as HTMLElement,
        }
    }

    it("always has the side column on a wide screen, even with nothing in it", () => {
        const { container } = render(<MemoryRouter><PageShell contents="none"><p>Content</p></PageShell></MemoryRouter>)
        expect(container.querySelector(".page-shell-side")).not.toBeNull()
        expect(container.querySelector(".page-toc")).toBeNull()
    })

    it("places the side column after the content, with the contents list above the side block", () => {
        const { content, side, toc } = renderWithSide()
        expect(side).toContainElement(screen.getByTestId("side-marker"))
        expect(side).toContainElement(toc)
        expect(content.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(toc.compareDocumentPosition(screen.getByTestId("side-marker")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it("also follows the content on a narrow screen, and is left out there when it has no block", () => {
        window.matchMedia = vi.fn().mockImplementation((query: string) => ({
            matches: query.includes("62rem"), media: query, addEventListener: () => {}, removeEventListener: () => {},
        })) as unknown as typeof window.matchMedia
        const { content, side } = renderWithSide()
        expect(content.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        cleanup()
        const { container } = render(<MemoryRouter><PageShell><p>Content</p></PageShell></MemoryRouter>)
        expect(container.querySelector(".page-shell-side")).toBeNull()
    })

    it("renders a breadcrumb trail before the identity block", () => {
        const { container } = render(
            <MemoryRouter>
                <PageShell crumbs={[{ label: "Methods", to: "/methods" }, { label: "Here" }]} identity={<p data-testid="id">Identity</p>}>
                    <p>Content</p>
                </PageShell>
            </MemoryRouter>,
        )
        const head = container.querySelector(".page-shell-identity") as HTMLElement
        expect(head.firstElementChild).toBe(head.querySelector("nav.breadcrumbs"))
        expect(head).toContainElement(screen.getByTestId("id"))
    })

    it("owns the page width: one grid with a fixed side column and gap, no right rail", () => {
        expect(pageShellCss).toMatch(/\.page-shell \{[^}]*max-width:\s*var\(--page-max\)/)
        expect(pageShellCss).toMatch(/\.page-shell-layout \{[^}]*grid-template-columns:\s*14rem minmax\(0, 1fr\)[^}]*gap:\s*3rem/)
        expect(pageShellCss).not.toMatch(/page-shell-rail/)
    })
})
