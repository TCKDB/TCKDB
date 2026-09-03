import type { KeyboardEvent } from "react"
import { useRef } from "react"
import { Link } from "react-router-dom"
import { sectionLabels } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"

const TAB_ORDER = Object.keys(sectionLabels) as EntrySection[]

// The entry's own `availability` flags (`SpeciesEntryProjection["availability"]`
// -- already fetched for the hero's "Archive availability" fact row, see
// `SpeciesEntrySummary.tsx`) are the ONLY signal this reuses to mark a tab,
// rather than this component deriving a second, independent notion of
// "has content". Geometry and single-point energy are both per-CONFORMER
// evidence -- neither has its own availability flag -- so both key off
// `has_conformers`: a conformer basin is the precondition for either tab
// ever having anything to show.
export type EntryTabAvailability = {
    has_conformers: boolean
    has_thermo: boolean
    has_statmech: boolean
    has_transport: boolean
}

function hasContent(section: EntrySection, availability: EntryTabAvailability): boolean {
    switch (section) {
        case "geometry":
        case "sp":
            return availability.has_conformers
        case "statmech":
            return availability.has_statmech
        case "thermo":
            return availability.has_thermo
        case "transport":
            return availability.has_transport
    }
}

/**
 * A real ARIA tablist (manual-activation pattern: arrow keys move focus
 * among tabs, Enter/Space on the focused link -- a real `<a>`, so it stays
 * a normal link for open-in-new-tab/copy-link -- activates it). Replaces
 * the three-group chapter nav: one flat row of five chemistry-named panels,
 * each scoped to whichever conformer is selected via `conformerQuery`.
 *
 * Each tab is marked when its section actually has content -- a small dot
 * (shape, not colour, so it survives a colour-blind or high-contrast
 * viewer; `aria-hidden` since it adds nothing a screen reader needs -- the
 * populated/empty distinction is a convenience for a sighted reader
 * scanning the row, not new information the panel beneath doesn't already
 * state honestly either way). An empty tab's `title` names the fact for
 * anyone hovering, WITHOUT joining the link's own visible text and
 * changing its accessible NAME -- the tab must still be reachable as
 * exactly "Transport", not "Transport (no data yet)". Without any of
 * this, a reader has no way to tell "Transport" is empty for THIS entry
 * until after clicking it -- the same information `EntryIdentity`'s
 * "Archive availability" fact already carries, just not yet surfaced on
 * the tab that would otherwise waste the click.
 */
export function EntryTabs({ entryRef, activeSection, conformerQuery, availability }: {
    entryRef: string
    activeSection: EntrySection
    conformerQuery: string
    availability: EntryTabAvailability
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
                const populated = hasContent(section, availability)
                return (
                    <Link
                        key={section}
                        role="tab"
                        id={`tab-${section}`}
                        aria-selected={isActive}
                        aria-controls={`panel-${section}`}
                        tabIndex={isActive ? 0 : -1}
                        data-has-content={populated}
                        title={populated ? undefined : "No data recorded for this section yet"}
                        ref={(node) => { tabRefs.current[section] = node }}
                        to={`/species-entries/${entryRef}/${section}${conformerQuery}`}
                        onKeyDown={(event) => handleKeyDown(event, index)}
                    >
                        {sectionLabels[section]}
                        {populated && <span className="entry-tab-dot" aria-hidden="true" />}
                    </Link>
                )
            })}
        </div>
    )
}
