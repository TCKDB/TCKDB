import { Link } from "react-router-dom"
import { BROWSE_KINDS, BROWSE_KIND_LABELS, BROWSE_KIND_PATHS } from "../api/browseApi"
import type { BrowseKind } from "../api/browseApi"

/**
 * Demoted from a radiogroup (owner, three times: "Why is the browse
 * reactions with the species/transition/vanderwaals browsing page?" / "in
 * the Browse Archive there should be separate for reaction and species not
 * slammed together"). A `role="radio"` control frames the four kinds as
 * MODES of one shared page -- pick a mode, the page underneath stays the
 * same -- which is exactly the "one catalogue in four modes" reading the
 * owner objected to, even after each kind got its own heading (see
 * `BROWSE_KIND_CONTENT`, `BrowsePage.tsx`). Each kind is genuinely its own
 * index at its own URL now, so this is a plain navigation list: real
 * `<Link>`s to the OTHER three indexes, not a control that "selects" among
 * four options on the page you are already looking at -- the current kind
 * is not itself listed here, the same way a page does not link to itself
 * in its own nav.
 *
 * `onSelect` still runs `clearInapplicableFilters`/resets pagination
 * (`BrowsePage.tsx`'s `selectKind`) so a filter that cannot apply to the
 * next kind does not silently ride along -- but it no longer calls
 * `navigate()` itself: the `<Link>`'s own click handling does that (real
 * `href`s, so a reader can still open a kind in a new tab or middle-click
 * it, which the old radio input could never offer). Both the filter-clear
 * side effect and the browser's own navigation happen inside the SAME
 * click, in the same order the old `selectKind` produced, since
 * `BrowsePage` never remounts across the four kind paths (see its own
 * comment on that).
 */
export function BrowseKindSelector({ kind, onSelect }: { kind: BrowseKind; onSelect: (kind: BrowseKind) => void }) {
    const otherKinds = BROWSE_KINDS.filter((option) => option !== kind)
    return (
        <nav aria-label="Browse a different kind" className="browse-kind-links">
            <p className="browse-kind-links-label">Also in this archive</p>
            <ul className="browse-kind-link-list">
                {otherKinds.map((option) => (
                    <li key={option}>
                        <Link
                            className="browse-kind-link"
                            onClick={() => onSelect(option)}
                            to={BROWSE_KIND_PATHS[option]}
                        >
                            {BROWSE_KIND_LABELS[option]}
                        </Link>
                    </li>
                ))}
            </ul>
        </nav>
    )
}
