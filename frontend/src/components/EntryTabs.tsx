import type { KeyboardEvent } from "react"
import { useRef } from "react"
import { Link } from "react-router-dom"
import { sectionLabels } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"

const TAB_ORDER = Object.keys(sectionLabels) as EntrySection[]

/**
 * A real ARIA tablist (manual-activation pattern: arrow keys move focus
 * among tabs, Enter/Space on the focused link -- a real `<a>`, so it stays
 * a normal link for open-in-new-tab/copy-link -- activates it). Replaces
 * the three-group chapter nav: one flat row of five chemistry-named panels,
 * each scoped to whichever conformer is selected via `conformerQuery`.
 */
export function EntryTabs({ entryRef, activeSection, conformerQuery }: {
    entryRef: string
    activeSection: EntrySection
    conformerQuery: string
}) {
    const tabRefs = useRef<Partial<Record<EntrySection, HTMLAnchorElement | null>>>({})

    function focusTab(section: EntrySection) {
        tabRefs.current[section]?.focus()
    }

    function handleKeyDown(event: KeyboardEvent<HTMLAnchorElement>, index: number) {
        if (event.key === "ArrowRight") {
            event.preventDefault()
            focusTab(TAB_ORDER[(index + 1) % TAB_ORDER.length])
        } else if (event.key === "ArrowLeft") {
            event.preventDefault()
            focusTab(TAB_ORDER[(index - 1 + TAB_ORDER.length) % TAB_ORDER.length])
        } else if (event.key === "Home") {
            event.preventDefault()
            focusTab(TAB_ORDER[0])
        } else if (event.key === "End") {
            event.preventDefault()
            focusTab(TAB_ORDER[TAB_ORDER.length - 1])
        }
    }

    return (
        <div className="entry-tabs" role="tablist" aria-label="Conformer evidence">
            {TAB_ORDER.map((section, index) => {
                const isActive = section === activeSection
                return (
                    <Link
                        key={section}
                        role="tab"
                        id={`tab-${section}`}
                        aria-selected={isActive}
                        aria-controls={`panel-${section}`}
                        tabIndex={isActive ? 0 : -1}
                        ref={(node) => { tabRefs.current[section] = node }}
                        to={`/species-entries/${entryRef}/${section}${conformerQuery}`}
                        onKeyDown={(event) => handleKeyDown(event, index)}
                    >
                        {sectionLabels[section]}
                    </Link>
                )
            })}
        </div>
    )
}
