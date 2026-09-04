import type { ReactNode } from "react"
import "../design-system.css"

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
export function Disclosure({ summary, count, defaultOpen, id, onToggle, className, children }: DisclosureProps) {
    return (
        <details
            id={id}
            className={className ? `disclosure ${className}` : "disclosure"}
            open={defaultOpen}
            onToggle={onToggle ? (event) => onToggle((event.target as HTMLDetailsElement).open) : undefined}
        >
            <summary>
                {summary}
                {count !== undefined && <span className="disclosure-count"> ({count})</span>}
            </summary>
            <div className="disclosure-body">{children}</div>
        </details>
    )
}
