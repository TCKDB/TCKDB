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
 *
 * `kicker` and `intro` are additive, optional pieces (design/foundations
 * PR B, "record pages" consolidation): `kicker` renders as a `.t-kicker`
 * eyebrow line immediately above the `<h2>` -- callers that used to
 * render their own `<p className="eyebrow">` immediately before this
 * component now pass that same text here instead, so the eyebrow is part
 * of the heading's own markup rather than a sibling a caller could drop
 * or reorder independently. `intro` renders as a `--type-body` paragraph
 * capped to `--measure-prose`, replacing a caller's own trailing
 * `<p>` description that used to follow the `<h2>` outside this
 * component. Neither prop is required, and a caller that never passes
 * them (every consumer outside the record pages, as of this writing)
 * renders exactly the bare `<h2>` it always did -- this is a strictly
 * additive change to a component used far outside those five pages.
 *
 * `className` (default none) is composed alongside the canonical
 * `t-heading-1` step (`design-system.css`) rather than replacing it, so
 * a caller can add a page-scoped modifier without having to re-specify
 * the base heading typography.
 *
 * `kicker` is suppressed when it case-insensitively equals `children`'s
 * own text (design/foundations PR E, "record-page residuals" re-review,
 * SHOULD-FIX-6): a kicker earns its place only when it names a category
 * the title itself lacks ("Deposited provenance" above "Structure view",
 * say) -- four callers across this app's record pages used to pass the
 * heading's own title straight back in as its kicker ("Result" / "Result",
 * "Geometries" / "Geometries", ...), MEASURED as a plain restatement with
 * no added information. Enforced HERE, once, rather than trusted to every
 * call site getting it right on its own -- a caller that still passes a
 * redundant kicker (today or in the future) renders correctly regardless.
 */
export function SectionHeading({ id, children, label, className, kicker, intro }: {
    id: string
    children: ReactNode
    label?: string
    className?: string
    kicker?: ReactNode
    intro?: ReactNode
}) {
    const resolvedLabel = label ?? (typeof children === "string" ? children : id)
    useRegisteredSection(id, resolvedLabel)
    const titleText = typeof children === "string" ? children : undefined
    const kickerIsRedundant = typeof kicker === "string" && titleText !== undefined
        && kicker.trim().toLowerCase() === titleText.trim().toLowerCase()
    const showKicker = kicker != null && kicker !== "" && !kickerIsRedundant
    return (
        <>
            {showKicker && <p className="t-kicker section-kicker">{kicker}</p>}
            <h2 className={className ? `t-heading-1 ${className}` : "t-heading-1"} id={id}>{children}</h2>
            {intro && <p className="t-body section-intro">{intro}</p>}
        </>
    )
}
