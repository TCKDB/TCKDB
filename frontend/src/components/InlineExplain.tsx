import "../inline-explain.css"

/**
 * A small on-demand explanation disclosure -- the WAI-ARIA APG "tooltip"
 * pattern: a trigger element carries `aria-describedby` pointing at this
 * note, and hover/focus/tap toggle its own VISIBILITY (an `--open` class),
 * never its presence in the DOM. It stays in the accessibility tree in
 * BOTH states (`opacity`, not `display:none`/`visibility:hidden`, gates it
 * -- see `inline-explain.css`), so a screen reader announces it via
 * `aria-describedby` the moment the DESCRIBED control is focused, regardless
 * of whether the box happens to be drawn on screen at that instant.
 *
 * Generalizes the disclosure `ArrheniusChart.tsx` built first for its
 * disabled y-axis unit control (`title` + `aria-describedby` + a
 * visually-hidden paragraph) into something any trigger can wire up --
 * first used here by `BrowseFilterForm.tsx`'s field labels, via the
 * `useInlineExplain` state hook (`hooks/useInlineExplain.ts`).
 *
 * Always rendered once `note` is non-empty -- toggling `open` only ever
 * changes the CSS class, never whether this element exists, which is what
 * keeps `aria-describedby` valid (and the note screen-reader-reachable) in
 * the CLOSED state too.
 */
export function InlineExplainNote({ id, note, open, className }: {
    id: string
    note: string
    open: boolean
    className?: string
}) {
    return (
        <span
            className={`inline-explain-note${open ? " inline-explain-note--open" : ""}${className ? ` ${className}` : ""}`}
            id={id}
            role="tooltip"
        >
            {note}
        </span>
    )
}
