import { useEffect, useRef, useState, type MutableRefObject } from "react"
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

// How long to wait, after the MOST RECENT scroll event, before treating a
// programmatic scroll (an explicit click's `scrollIntoView`, or a resolved
// `#fragment`'s) as finished. `scrollIntoView` fires a whole BURST of
// scroll events while it animates, not one -- a one-shot "ignore the very
// next event" flag consumes only the first of that burst and then lets
// every later one in the same burst overwrite the reader's explicit
// choice with whatever `computeActive` reads at that intermediate
// position (the "click Statistical Mechanics, it lands there, then the
// highlight jumps to Review History" report). Re-arming this timeout on
// every scroll event received while suppressed -- see
// `beginScrollSuppression` -- means it only fires once the events stop
// arriving, i.e. once the scroll has actually settled, however many
// events the browser needed to get there.
export const SCROLL_SETTLE_MS = 150

// Starts (or restarts) the suppression window covering one programmatic
// scroll. Deliberately does NOT recompute the active section when the
// window elapses -- an explicit click or `#fragment` is a STATEMENT of
// intent, and `computeActive`'s geometry read is only ever an INFERENCE
// of it; running that inference at the exact moment the suppression lifts
// would let the geometry at the scroll's resting position silently
// overrule the reader's own click (the same bug, one layer down: a click
// near the bottom of a tall page settles at a scroll position where the
// widened activation line reaches every heading, and the LAST one -- not
// the clicked one -- would win). The clicked/resolved section is left as
// `activeId` set it, and stays active until a REAL scroll event arrives
// after this window has elapsed with nothing re-arming it.
//
// A module-level function, not a `useCallback`, because it closes over
// nothing but the two refs passed in -- both stable for the lifetime of
// the component -- so there is no per-render identity for it to need to
// keep stable, and no missing-dependency question to answer.
function beginScrollSuppression(
    suppressedRef: MutableRefObject<boolean>,
    settleTimeoutIdRef: MutableRefObject<number | null>,
) {
    suppressedRef.current = true
    if (settleTimeoutIdRef.current !== null) window.clearTimeout(settleTimeoutIdRef.current)
    settleTimeoutIdRef.current = window.setTimeout(() => {
        suppressedRef.current = false
        settleTimeoutIdRef.current = null
    }, SCROLL_SETTLE_MS)
}

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
    // Set for the full duration of an explicit click's or a resolved
    // #fragment's `scrollIntoView` -- which fires a BURST of "scroll"
    // events while it animates, not one -- so that none of those
    // incidental events re-run the offset computation and stomp the
    // selection the reader just made. Cleared only once
    // `SCROLL_SETTLE_MS` has passed with no further scroll event
    // arriving (see `beginScrollSuppression`); any REAL scrolling after
    // that resumes normal scroll-spying.
    const suppressScrollSpyRef = useRef(false)
    // The pending "the programmatic scroll has settled" timer, so a new
    // scroll event arriving mid-burst can re-arm it (extend the
    // suppression window) instead of leaving an earlier, shorter timer to
    // fire underneath it.
    const settleTimeoutIdRef = useRef<number | null>(null)

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
            // How far the reader has scrolled through the page, as a
            // fraction from 0 (top) to 1 (bottom) -- NOT how much scroll
            // room is left. The two sound similar but differ at the top
            // of a short page: "room left" there equals the page's own
            // (small) scroll range, so a page that barely scrolls at all
            // reads as "basically at the bottom" from its very first
            // frame. Scroll PROGRESS instead reads 0 at the top on every
            // page regardless of height, which is what keeps a freshly
            // loaded page -- whether it is Geometry (one short section)
            // or Statistical Mechanics (four, much taller) -- landing on
            // its FIRST heading rather than one keyed to how tall that
            // particular tab happens to be.
            //
            // The line still widens smoothly from the fixed
            // ACTIVE_OFFSET_PX (progress 0) towards the full viewport
            // height (progress 1) as the reader nears the end of a
            // document that runs out of scroll room before its trailing
            // headings can each individually cross that line -- the
            // reported bug, "the page cannot move further down to make
            // Torsions the top of the page" -- so trailing headings still
            // become current one at a time as they enter view, rather
            // than every one of them collapsing onto "the last section"
            // only once scrolling stops completely. It is only the
            // ANCHOR of that widening that moved, from "room left" to
            // "progress made".
            const progress = maxScrollY > 0 ? window.scrollY / maxScrollY : 0
            const effectiveOffset =
                ACTIVE_OFFSET_PX + Math.max(0, window.innerHeight - ACTIVE_OFFSET_PX) * progress
            let current: string | null = null
            for (const section of sections) {
                const heading = document.getElementById(section.id)
                if (!heading) continue
                if (heading.getBoundingClientRect().top <= effectiveOffset) current = section.id
            }
            setActiveId(current ?? sections[0]?.id ?? null)
        }
        function handleScroll() {
            if (suppressScrollSpyRef.current) {
                // Still inside a programmatic scroll's burst of events --
                // re-arm the settle timer rather than computing, so the
                // suppression window covers the WHOLE burst instead of
                // just its first event.
                beginScrollSuppression(suppressScrollSpyRef, settleTimeoutIdRef)
                return
            }
            computeActive()
        }
        handleScroll()
        window.addEventListener("scroll", handleScroll, { passive: true })
        return () => window.removeEventListener("scroll", handleScroll)
    }, [sections, showList])

    // Clears the settle timer on unmount so it never fires (harmlessly,
    // since it only flips refs) against an unmounted component. This
    // intentionally reads `settleTimeoutIdRef.current` AT CLEANUP TIME,
    // not at effect-setup time -- the whole point is to catch whatever
    // timer is pending when the component goes away, which is scheduled
    // long after this effect ran (from a click, or a #fragment resolving)
    // and is null at mount. Copying it into a effect-scoped variable, as
    // the lint rule's own suggestion would have it, would instead always
    // capture that mount-time null and clear nothing.
    useEffect(() => {
        return () => {
            // eslint-disable-next-line react-hooks/exhaustive-deps -- see comment above
            if (settleTimeoutIdRef.current !== null) window.clearTimeout(settleTimeoutIdRef.current)
        }
    }, [])

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
        beginScrollSuppression(suppressScrollSpyRef, settleTimeoutIdRef)
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
                                            // clicked one. Suppressed for
                                            // the browser's own anchor-jump
                                            // scroll burst that follows,
                                            // for its full duration -- see
                                            // `beginScrollSuppression` --
                                            // so nothing in that burst
                                            // overwrites this with whatever
                                            // section the geometry reads at
                                            // an intermediate scroll
                                            // position on the way there.
                                            setActiveId(section.id)
                                            resolvedHash.current = section.id
                                            beginScrollSuppression(suppressScrollSpyRef, settleTimeoutIdRef)
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
