import { useMemo } from "react"
import type { ReactNode } from "react"
import { SectionRegistry } from "../domain/sectionRegistry"
import { SectionRegistryContext, useRegisteredSection } from "../hooks/usePageSections"

/**
 * Owns one `SectionRegistry` for everything rendered beneath it.
 * `PageShell` wraps every record page's content in exactly one of these
 * -- nesting a second provider inside the first would split registrations
 * across two registries and make `TableOfContents` see only half of them.
 */
export function PageSectionsProvider({ children }: { children: ReactNode }) {
    const registry = useMemo(() => new SectionRegistry(), [])
    return <SectionRegistryContext.Provider value={registry}>{children}</SectionRegistryContext.Provider>
}

/**
 * Drop-in replacement for `<h2 id="...">text</h2>` that also registers
 * the heading as a page section while mounted (`useRegisteredSection`,
 * `hooks/usePageSections.ts`). This is the mechanism the shell relies on
 * to compute "how many sections does this page have" from what actually
 * renders rather than from a per-file `<h2>` count or a hand-maintained
 * list: a component nested arbitrarily deep beneath the page (an entry
 * tab body, a lazy statmech subsection) contributes to its page's section
 * count the moment it mounts and stops the moment it unmounts, with
 * nothing else to keep in sync.
 *
 * `children` must be the heading's own visible text (a string) -- it
 * doubles as the ToC entry's label. A heading built from richer markup
 * should pass an explicit `label` instead.
 */
export function SectionHeading({ id, children, label, className }: {
    id: string
    children: ReactNode
    label?: string
    className?: string
}) {
    const resolvedLabel = label ?? (typeof children === "string" ? children : id)
    useRegisteredSection(id, resolvedLabel)
    return <h2 className={className} id={id}>{children}</h2>
}
