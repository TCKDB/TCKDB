import { useEffect, useRef, useState } from "react"
import { useLocation } from "react-router-dom"
import "../page-shell.css"
import { usePageSections } from "../hooks/usePageSections"

// Below this many REGISTERED sections there is nothing worth a jump list
// -- a 1-section page (a single tab body registering one heading) does not
// get one. The COLUMN itself, unlike the list, is not gated on this at
// all: it is always reserved (see the component doc below) so a page
// crossing this threshold at runtime -- a tab switch, a lazy section
// finishing its first fetch -- never shifts the content pane sideways.
export const MIN_SECTIONS_FOR_LIST = 2

// How close a heading's top edge must be to the viewport top before it
// counts as "the current section" -- a fixed offset rather than something
// read back from a sticky header's own height, since this must also work
// in jsdom, which has no layout at all.
const ACTIVE_OFFSET_PX = 160

// How close the viewport's bottom edge must be to the document's own
// bottom edge before "the reader is at the end of the page" overrides the
// offset computation below. A few px of slack for sub-pixel scroll
// rounding, not a meaningful distance.
const BOTTOM_EPSILON_PX = 2

/**
 * A sticky side rail of links to whatever sections are currently mounted
 * (`usePageSections`). The COLUMN -- the space this component occupies in
 * `.page-shell-layout` -- is always rendered on a record page; only the
 * `<nav>`/`<ul>` *inside* it is conditional on
 * `sections.length >= MIN_SECTIONS_FOR_LIST`. A 1-section page reserves
 * the same width as a 6-section one and renders an empty column, not a
 * missing one -- see `page-shell.css`'s `.page-toc:empty` rule for how
 * that empty column stays visually silent (no stray border/padding) while
 * still holding its place. This is what item 2/3 of the design brief asks
 * for directly: switching from the Geometry tab (1 section) to Statistical
 * Mechanics (usually 4+) must never mount or unmount the column and must
 * never shift the content pane sideways.
 *
 * Collapses out of the flow entirely on narrow viewports when it has
 * nothing to show (see `page-shell.css`'s `62rem` breakpoint); every entry
 * is a real `<a href="#...">`, so it is keyboard-reachable and
 * focus-visible without this component doing anything special for either.
 */
export function TableOfContents() {
    const sections = usePageSections()
    const location = useLocation()
    const [activeId, setActiveId] = useState<string | null>(null)
    const showList = sections.length >= MIN_SECTIONS_FOR_LIST
    // Which hash this rail has already resolved -- so a mount-time hash
    // that arrives before its target section has registered (data still
    // loading) gets retried as `sections` grows, but a hash that WAS
    // already resolved is not force-scrolled to again on every unrelated
    // re-render.
    const resolvedHash = useRef<string | null>(null)

    useEffect(() => {
        if (!showList) return
        function computeActive() {
            const doc = document.documentElement
            const atBottom = window.innerHeight + window.scrollY >= doc.scrollHeight - BOTTOM_EPSILON_PX
            if (atBottom) {
                // The page has run out of room to scroll further. A
                // section's heading can be permanently unable to reach
                // `ACTIVE_OFFSET_PX` from the top -- not because it isn't
                // the current section, but because everything below it on
                // the page is too short to push it that far up (the
                // reported bug: "the page cannot move further down to make
                // Torsions the top of the page"). At the bottom of the
                // scrollable area the LAST section is always the answer,
                // regardless of where its heading happens to sit.
                setActiveId(sections[sections.length - 1]?.id ?? null)
                return
            }
            let current: string | null = null
            for (const section of sections) {
                const heading = document.getElementById(section.id)
                if (!heading) continue
                if (heading.getBoundingClientRect().top <= ACTIVE_OFFSET_PX) current = section.id
            }
            setActiveId(current ?? sections[0]?.id ?? null)
        }
        computeActive()
        window.addEventListener("scroll", computeActive, { passive: true })
        return () => window.removeEventListener("scroll", computeActive)
    }, [sections, showList])

    // Resolve a `#fragment` explicitly rather than relying on the
    // browser's own initial-load fragment scroll: this is an SPA whose
    // content (and section ids) arrive from a fetch, so the target element
    // usually doesn't exist yet at the moment the browser would normally
    // try. Re-runs as `sections` grows (each fetch resolving, each tab
    // mounting), so a fragment naming a section that shows up two renders
    // later still resolves once it exists. `location.hash` -- not
    // `window.location.hash` -- so this reads correctly with a query
    // string also present (`?conformer=cg_1#section-conformer-context`).
    useEffect(() => {
        const hash = location.hash.replace(/^#/, "")
        if (!hash) {
            resolvedHash.current = null
            return
        }
        if (resolvedHash.current === hash) return
        if (!sections.some((section) => section.id === hash)) return
        const target = document.getElementById(hash)
        if (!target) return
        target.scrollIntoView?.()
        // This is the one case the design brief calls out by name ("make an
        // explicit click or a #fragment land on that section immediately
        // rather than waiting for a scroll event to correct it"). Deferring
        // to the scroll-driven `computeActive` effect instead would be
        // exactly that wrong behaviour -- `scrollIntoView` does not
        // synchronously fire a `scroll` event (and does nothing at all in
        // jsdom), so there is no external-system callback here to move this
        // into; the imperative DOM read (`document.getElementById`) that
        // justifies this being an effect at all is inseparable from the
        // state it resolves to.
        // eslint-disable-next-line react-hooks/set-state-in-effect -- see comment above
        setActiveId(hash)
        resolvedHash.current = hash
    }, [location.hash, sections])

    return (
        <div className="page-toc">
            {showList && (
                <nav aria-label="Sections on this page">
                    <ul>
                        {sections.map((section) => {
                            const isActive = section.id === activeId
                            return (
                                <li key={section.id}>
                                    <a
                                        aria-current={isActive ? "true" : undefined}
                                        className={isActive ? "page-toc-active" : undefined}
                                        href={`#${section.id}`}
                                        onClick={() => {
                                            // Land on the clicked section
                                            // immediately rather than
                                            // waiting for a scroll event to
                                            // "correct" the active marker
                                            // -- the fix for the reported
                                            // bug where clicking a section
                                            // near the end of a short page
                                            // left an earlier section
                                            // underlined, because the page
                                            // had nowhere left to scroll to
                                            // ever cross the offset for the
                                            // clicked one.
                                            setActiveId(section.id)
                                            resolvedHash.current = section.id
                                        }}
                                    >
                                        {section.label}
                                    </a>
                                </li>
                            )
                        })}
                    </ul>
                </nav>
            )}
        </div>
    )
}
