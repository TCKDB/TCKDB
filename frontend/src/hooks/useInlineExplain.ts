import { useState, type KeyboardEvent } from "react"

/**
 * State for a single `InlineExplain.tsx` disclosure. Split into its own
 * module (rather than living alongside `InlineExplainNote` in
 * `components/InlineExplain.tsx`) because a file that exports both a
 * component and a plain function trips this repo's `react-refresh/
 * only-export-components` lint rule -- every other hook in the app
 * (`useVocabulary.ts`, `useBrowse.ts`, ...) already lives in `hooks/` for
 * the same reason.
 *
 * See `InlineExplainNote`'s own doc comment for the full accessibility
 * rationale: `open`/`close`/`toggle` are exposed as plain functions, not
 * baked into one fixed trigger markup, because a field's `<label>` and its
 * `<input>` are two SEPARATE elements that both need to drive (and both
 * benefit from reflecting) the SAME open state.
 */
export function useInlineExplain() {
    const [isOpen, setIsOpen] = useState(false)
    return {
        isOpen,
        open: () => setIsOpen(true),
        close: () => setIsOpen(false),
        toggle: () => setIsOpen((wasOpen) => !wasOpen),
        // Escape is the APG tooltip pattern's own documented dismissal key --
        // offered on top of (never instead of) blur, since a mouse user who
        // hovered the trigger has no blur event to dismiss it with at all.
        onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
            if (event.key === "Escape") setIsOpen(false)
        },
    }
}
