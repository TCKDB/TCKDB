import type { ComponentPropsWithoutRef, ReactNode } from "react"

// No `import "../design-system.css"` here (post-review, PR D): `index.css`
// already `@import`s `design-system.css` once, globally, from `main.tsx` --
// every route this component can possibly render on has it loaded before
// this component ever mounts. A component-local import on top of that is
// not just redundant: Vite treats it as a genuine dependency edge and
// emits a SECOND copy of `design-system.css` into whichever route chunk
// lazily imports this component, which a browser then injects as a second,
// LATER stylesheet -- reordering the cascade for every rule in the file
// (not just this component's own `.disclosure` rules) relative to
// `index.css`'s own rules, entirely by accident of which chunk happened to
// load. See `design-system.css`'s own comment above `.value-pill` for the
// bug this caused. Confirmed fixed: `dist/assets/*.css` after a production
// build contains exactly one copy of the `.disclosure` rules.

export interface DisclosureProps {
    summary: ReactNode
    count?: number
    defaultOpen?: boolean
    id?: string
    onToggle?: (open: boolean) => void
    /** Added ALONGSIDE the primitive's own `.disclosure` class (not in
     *  place of it), so a caller that needs page-specific box styling on
     *  top of the shared chrome -- `RefsDisclosure`'s `.conformer-card`-
     *  scoped border override is the one real case in this app today --
     *  gets both. */
    className?: string
    /** Passthrough attributes for the `<summary>` element itself -- e.g.
     *  `aria-describedby`, for a caller whose disclosure sits beside (and
     *  should stay associated with) another heading it does not itself
     *  render, such as `SpeciesOverviewPage.tsx`'s per-state-group
     *  `EntryStateGroup`. Spread AFTER this component's own `{summary}`/
     *  `{count}` children, so a caller can add attributes but never
     *  override the summary's actual rendered content through this prop. */
    summaryProps?: ComponentPropsWithoutRef<"summary">
    children: ReactNode
}

/**
 * The one canonical `<details>`/`<summary>` disclosure the app renders
 * through -- see the file header comment above `.disclosure` in
 * `design-system.css` for the review finding this replaces (7 different
 * `<summary>` styles measured across the live site, 5 different box
 * styles, and no chevron anywhere -- callers relied on the bare UA
 * triangle, sized by whatever font the summary happened to inherit,
 * 11px to 30px across those 7).
 *
 * `defaultOpen` sets the details element's initial `open` state without
 * making it a React-controlled prop: once mounted, native `<details>`
 * toggling (click or keyboard on the summary) manages `open` itself, and
 * this component never re-renders with a DIFFERENT `open` value to fight
 * that -- `defaultOpen` is read once, on mount, exactly like a real
 * `defaultChecked`/`defaultValue` prop would be.
 */
export function Disclosure({ summary, count, defaultOpen, id, onToggle, className, summaryProps, children }: DisclosureProps) {
    return (
        <details
            id={id}
            className={className ? `disclosure ${className}` : "disclosure"}
            open={defaultOpen}
            onToggle={onToggle ? (event) => onToggle((event.target as HTMLDetailsElement).open) : undefined}
        >
            <summary {...summaryProps}>
                {summary}
                {count !== undefined && <span className="disclosure-count"> ({count})</span>}
            </summary>
            <div className="disclosure-body">{children}</div>
        </details>
    )
}
