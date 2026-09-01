import { useEffect, useState } from "react"
import "../page-shell.css"
import { usePageSections } from "../hooks/usePageSections"

// Below this many sections a ToC has nothing worth navigating -- a
// 2-section page (ConformerGroupPage) does not get one, matching the
// design brief's own worked example. A 4-section page (GeometryDetailPage,
// ConformerObservationPage, CalculationDetailPage, and -- via child
// component registration -- SpeciesEntryPage) does.
export const MIN_SECTIONS_FOR_TOC = 4

// How close a heading's top edge must be to the viewport top before it
// counts as "the current section" -- a fixed offset rather than something
// read back from a sticky header's own height, since this must also work
// in jsdom, which has no layout at all.
const ACTIVE_OFFSET_PX = 160

/**
 * A sticky side rail of links to whatever sections are currently mounted
 * (`usePageSections`) -- renders nothing at all below
 * `MIN_SECTIONS_FOR_TOC`, so a short page never reserves layout space for
 * an empty list. Collapses to a horizontal strip on narrow viewports (see
 * `page-shell.css`'s `62rem` breakpoint); every entry is a real `<a
 * href="#...">`, so it is keyboard-reachable and focus-visible without
 * this component doing anything special for either.
 */
export function TableOfContents() {
    const sections = usePageSections()
    const [activeId, setActiveId] = useState<string | null>(null)
    const showToc = sections.length >= MIN_SECTIONS_FOR_TOC

    useEffect(() => {
        if (!showToc) return
        function computeActive() {
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
    }, [sections, showToc])

    if (!showToc) return null

    return (
        <nav aria-label="Sections on this page" className="page-toc">
            <ul>
                {sections.map((section) => {
                    const isActive = section.id === activeId
                    return (
                        <li key={section.id}>
                            <a
                                aria-current={isActive ? "true" : undefined}
                                className={isActive ? "page-toc-active" : undefined}
                                href={`#${section.id}`}
                            >
                                {section.label}
                            </a>
                        </li>
                    )
                })}
            </ul>
        </nav>
    )
}
