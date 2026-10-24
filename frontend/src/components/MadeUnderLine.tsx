import { describeMadeUnder, type MadeUnder, type MadeUnderKind } from "../domain/madeUnder"
import "../made-under.css"

/**
 * The shared "made under" line. Every record page that shows a trust or evidence assessment renders this beside it,
 * so the reader is told which rubric judged it, at which version, and, when that version is not the current one, that
 * the assessment is stale and why. The wording lives in `domain/madeUnder.ts`; this component only lays it out.
 *
 * A stale assessment carries a "Stale" badge; a record nobody has assessed carries a muted "Not assessed" badge, a
 * different thing. `kind="review"` words a stored machine review ("Reviewed under ..."). `data-component="made-under"`
 * and `data-state` mark every instance in the DOM, so a page test can assert that a line came from this component
 * without depending on its copy.
 */
export function MadeUnderLine({ madeUnder, kind = "assessment" }: {
    madeUnder: MadeUnder | null | undefined
    kind?: MadeUnderKind
}) {
    if (!madeUnder) return null
    const wording = describeMadeUnder(madeUnder, kind)
    return (
        <p className="made-under" data-component="made-under" data-state={wording.state}>
            {wording.badge && wording.state === "stale" && (
                <span className="value-pill made-under-badge made-under-badge-stale">{wording.badge}</span>
            )}
            {wording.badge && wording.state !== "stale" && (
                <span className="value-pill value-pill--muted made-under-badge">{wording.badge}</span>
            )}
            <span className="made-under-text">{wording.sentence}</span>
        </p>
    )
}
