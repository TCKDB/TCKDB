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

// Slack for sub-pixel scroll rounding, not a meaningful distance. Used
// both to decide a document has essentially no scroll room left at all
// (see `maxScrollY` below) and, historically, to decide "the reader is at
// the end of the page" -- see the comment on `effectiveOffset`.
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
    // Set immediately before an explicit click or a resolved #fragment
    // triggers the browser's own anchor-jump / `scrollIntoView`, both of
    // which fire a "scroll" event of their own as a side effect. That ONE
    // incidental scroll event must not immediately re-run the offset
    // computation and stomp the selection the reader just made -- so it
    // is consumed (reset to false) the very next time the scroll handler
    // runs, not held indefinitely. Any REAL scrolling after that resumes
    // normal scroll-spying.
    const suppressNextScrollRef = useRef(false)

    useEffect(() => {
        if (!showList) return
        function computeActive() {
            const doc = document.documentElement
            const maxScrollY = Math.max(0, doc.scrollHeight - window.innerHeight)
            if (maxScrollY <= BOTTOM_EPSILON_PX) {
                // The whole page already fits in the viewport -- there is
                // no scroll position to read the reader's attention from.
                // Leave whatever is active (an explicit click, a resolved
                // #fragment, or nothing yet) alone rather than guessing at
                // it, so a page that never scrolls can't permanently pin
                // the highlight on any one section regardless of what the
                // reader clicked.
                return
            }
            // How much scroll room is left below the current position.
            // Far from the bottom this leaves the fixed ACTIVE_OFFSET_PX
            // line untouched. As the reader nears the end of a document
            // that runs out of scroll room before its trailing headings
            // can each individually cross that line -- the reported bug,
            // "the page cannot move further down to make Torsions the top
            // of the page" -- the line widens smoothly towards the full
            // viewport height, so trailing headings become current one at
            // a time as they enter view, rather than every one of them
            // collapsing onto "the last section" only once scrolling
            // stops completely.
            const remainingScroll = Math.max(0, maxScrollY - window.scrollY)
            const effectiveOffset = Math.max(ACTIVE_OFFSET_PX, window.innerHeight - remainingScroll)
            let current: string | null = null
            for (const section of sections) {
                const heading = document.getElementById(section.id)
                if (!heading) continue
                if (heading.getBoundingClientRect().top <= effectiveOffset) current = section.id
            }
            setActiveId(current ?? sections[0]?.id ?? null)
        }
        function handleScroll() {
            if (suppressNextScrollRef.current) {
                suppressNextScrollRef.current = false
                return
            }
            computeActive()
        }
        handleScroll()
        window.addEventListener("scroll", handleScroll, { passive: true })
        return () => window.removeEventListener("scroll", handleScroll)
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
        suppressNextScrollRef.current = true
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
                                            suppressNextScrollRef.current = true
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
