export type RegisteredSection = { id: string; label: string }

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
    private order: string[] = []
    private readonly listeners = new Set<() => void>()
    // Cached rather than recomputed per `getSnapshot()` call:
    // `useSyncExternalStore` requires a snapshot that is referentially
    // stable across renders when nothing changed, or React re-renders in
    // a loop treating every call as a fresh change.
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
        this.snapshot = this.order.map((id) => this.sections.get(id))
            .filter((section): section is RegisteredSection => section !== undefined)
        for (const listener of this.listeners) listener()
    }
}
