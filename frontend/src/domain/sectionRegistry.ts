export type RegisteredSection = { id: string; label: string }

/**
 * Orders sections the way they appear on the page, not the way they
 * registered. Registration order is a REACT IMPLEMENTATION DETAIL --
 * whichever `SectionHeading` happens to run its mount effect first --
 * and diverges from visual order the moment two sections don't mount in
 * the same commit. The reported bug: switching conformer group unmounts
 * one "Evidence for Conformer Group X" and mounts a different one in the
 * SAME on-page slot, but under a NEW id, so `register()` appends it to
 * the end of the mount-order list even though its `<h2>` sits between
 * two already-registered headings.
 *
 * `compareDocumentPosition` is the ground truth for "where is this on
 * the page" -- it reads the live DOM, not any bookkeeping this class
 * keeps. Sections whose element resolves in the document are ordered
 * against each other by their actual position. A section that doesn't
 * currently resolve (its element isn't mounted yet, or was removed a
 * tick ago) is never dropped and never shuffled to an arbitrary end --
 * `Array.prototype.sort` is a stable sort, and this comparator returns 0
 * for any pair it can't compare via the DOM, so an unresolved section
 * simply keeps its place relative to whatever it was already sorted next
 * to (its mount-order neighbours) until it resolves.
 */
function compareByDocumentPosition(a: RegisteredSection, b: RegisteredSection): number {
    const elA = document.getElementById(a.id)
    const elB = document.getElementById(b.id)
    if (!elA || !elB) return 0
    const position = elA.compareDocumentPosition(elB)
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1 // b comes after a in the document
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1 // b comes before a in the document
    return 0
}

/**
 * Tracks which page sections are ACTUALLY mounted right now, not which
 * ones a page's own file happens to declare. This is the mechanism the
 * side table of contents (`components/TableOfContents.tsx`) uses to
 * decide both WHETHER to render (a 4+-section page gets one, a
 * 2-section page does not) and WHAT to list — computed from runtime
 * registration, never from a hardcoded per-page list or a static `<h2>`
 * count of one file.
 *
 * `SpeciesEntryPage.tsx` is the case this exists for: it has exactly one
 * `<h2>` in its own file, because its sections live in child components
 * (`EntryThermoSection`, `ConformerAttributionGroups`, the individual
 * entry tabs, ...). Each of those registers itself the moment it mounts
 * (see `SectionHeading` in `components/PageSections.tsx`) and
 * unregisters the moment it unmounts — a tab switch, a conditional empty
 * state, a lazy disclosure never opened — so the count this class reports
 * is always exactly what a reader can currently scroll to, on any page,
 * without either this class or `TableOfContents` needing to know which
 * page it is.
 *
 * One instance per `<PageSectionsProvider>` (`PageShell` creates one per
 * page render), so two independent record pages never share state and a
 * client-side navigation to a new record starts from zero registrations.
 */
export class SectionRegistry {
    private readonly sections = new Map<string, RegisteredSection>()
    // Registration (mount) order -- authoritative for "does this id exist
    // right now", and the stable fallback order for a section that can't
    // yet be placed by document position (see `compareByDocumentPosition`).
    // NOT the order rendered; see `snapshot`.
    private order: string[] = []
    private readonly listeners = new Set<() => void>()
    // Cached rather than recomputed per `getSnapshot()` call:
    // `useSyncExternalStore` requires a snapshot that is referentially
    // stable across renders when nothing changed, or React re-renders in
    // a loop treating every call as a fresh change. `refresh()` is the
    // only place this is reassigned, and it only runs when a section
    // actually registers or unregisters -- a `register()` call fires
    // from a `SectionHeading`'s mount effect, which React only runs
    // after the DOM for that commit is already in place, so the document
    // position read here reflects reality, not a stale or empty DOM.
    private snapshot: RegisteredSection[] = []

    register = (id: string, label: string): (() => void) => {
        if (!this.sections.has(id)) this.order.push(id)
        this.sections.set(id, { id, label })
        this.refresh()
        return () => {
            this.sections.delete(id)
            this.order = this.order.filter((existingId) => existingId !== id)
            this.refresh()
        }
    }

    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener)
        return () => { this.listeners.delete(listener) }
    }

    getSnapshot = (): RegisteredSection[] => this.snapshot

    private refresh() {
        const byMountOrder = this.order.map((id) => this.sections.get(id))
            .filter((section): section is RegisteredSection => section !== undefined)
        this.snapshot = [...byMountOrder].sort(compareByDocumentPosition)
        for (const listener of this.listeners) listener()
    }
}
