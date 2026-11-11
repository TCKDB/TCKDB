import { useMemo } from "react"
import type { ReactNode } from "react"
import { INTRO_WORD_LIMIT, introWordCount } from "../domain/introWords"
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
 * `kicker` and `intro` are optional pieces: `kicker` renders as a `.t-kicker`
 * eyebrow line immediately above the `<h2>`, so the eyebrow is part
 * of the heading's own markup rather than a sibling a caller could drop
 * or reorder independently. `intro` renders as a `--type-body` paragraph
 * capped to `--measure-prose`, in place of a caller's own trailing
 * `<p>` description. Neither prop is required, and a caller that never passes
 * them renders the bare `<h2>`.
 *
 * `className` (default none) is composed alongside the canonical
 * `t-heading-1` step (`design-system.css`) rather than replacing it, so
 * a caller can add a page-scoped modifier without having to re-specify
 * the base heading typography.
 *
 * `kicker` is suppressed when it case-insensitively equals `children`'s
 * own text, OR is a case-insensitive PREFIX of it: a kicker earns its place
 * only when it names a category the title itself lacks ("Deposited
 * provenance" above "Structure view", say). A kicker that restates the
 * heading ("Result" / "Result", "Structure" / "Structure view", "Raw" /
 * "Raw XYZ", "Review" / "Review history") adds no information, and a kicker that
 * is just the title's own leading words reads
 * exactly the same as a full restatement once stacked visually. `prefix`
 * exactly the same as a full restatement once stacked visually. `prefix`
 * (not `includes`/substring anywhere) is deliberately narrow: it must
 * catch "Review" ahead of "Review history" without also catching an
 * unrelated kicker that merely shares a word with the title midway
 * through it ("Reaction context" ahead of "Reaction", say, is NOT a
 * restatement -- "Reaction" is a prefix of "Reaction context", the
 * OPPOSITE direction, and stays legitimate). Enforced HERE, once, rather
 * than trusted to every call site getting it right on its own -- a
 * caller that still passes a redundant kicker (today or in the future)
 * renders correctly regardless.
 */
export function SectionHeading({ id, children, label, className, kicker, intro, subsection }: {
    id: string
    children: ReactNode
    label?: string
    className?: string
    kicker?: ReactNode
    intro?: ReactNode
    /** An `<h3>` listed under the section above it, numbered 3.1 in the contents list. */
    subsection?: boolean
}) {
    if (import.meta.env.MODE === "test" && intro && introWordCount(intro) > INTRO_WORD_LIMIT) {
        throw new Error(`Section intro for "${id}" is over ${INTRO_WORD_LIMIT} words; shorten it or move it to the guide.`)
    }
    const resolvedLabel = label ?? (typeof children === "string" ? children : id)
    useRegisteredSection(id, resolvedLabel, subsection ? 2 : 1)
    const titleText = typeof children === "string" ? children : undefined
    const kickerIsRedundant = typeof kicker === "string" && titleText !== undefined
        && titleText.trim().toLowerCase().startsWith(kicker.trim().toLowerCase())
    const showKicker = kicker != null && kicker !== "" && !kickerIsRedundant
    return (
        <>
            {showKicker && <p className="t-kicker section-kicker">{kicker}</p>}
            {subsection
                ? <h3 className={className ? `t-heading-2 ${className}` : "t-heading-2"} id={id}>{children}</h3>
                : <h2 className={className ? `t-heading-1 ${className}` : "t-heading-1"} id={id}>{children}</h2>}
            {intro && <p className="note section-intro">{intro}</p>}
        </>
    )
}
