import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import type { TransitionStateBrowseRecord } from "../api/browseApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"

function token(value: string) {
    return value.replaceAll("_", " ")
}

// Single named seam for this row's link target. The in-flight ROUTING batch
// is repointing TS browse rows at a new `/transition-state-entries/:ref`
// page instead of the reaction they belong to -- when that lands, this is
// the only line it needs to change. Do not inline this expression anywhere
// else in this file.
function tsRowTarget(record: TransitionStateBrowseRecord): string | null {
    return record.reaction.reaction_ref ? `/reactions/${record.reaction.reaction_ref}` : null
}

/**
 * A transition state has no formula of its own -- it is identified by the
 * REACTION it connects (see `TransitionStatesBrowseRequest`'s module
 * docstring), so this row leads with the rendered equation and reaction
 * family rather than a molecular headline. Links to `/reactions/:ref`,
 * the existing (placeholder-for-now) reaction route, rather than to a
 * transition-state detail page -- no such page exists yet on this
 * frontend, and this row does not invent one. (That target route is a
 * separate, ongoing piece of work -- this file only decides WHETHER a
 * link exists and what it wraps, never what URL it points at.)
 *
 * Two presentation fixes from the same owner report as the species row's
 * pill split:
 *
 * - The label/status/review facts used to render as one run of plain text
 *   ("TS0 · optimized · review not reviewed") where `SpeciesBrowseRow.tsx`
 *   already uses pills for the equivalent facts -- same kind of
 *   information, two different treatments, and the literal word "review"
 *   glued onto its own status read as awkward. This row now reuses the
 *   EXACT SAME classes as the species row's entry chip
 *   (`browse-row-entries` / `browse-entry-chip` / `value-pill
 *   browse-entry-kind-pill` / `value-pill value-pill--muted
 *   browse-entry-review`) rather than inventing new styling: label+status
 *   combine into one pill (the species row's own kind+state pairing), and
 *   review status gets its own pill, unprefixed -- both parallel the
 *   species row fact-for-fact.
 * - Only the equation text used to be clickable. The whole row is the
 *   click target now, when a reaction ref exists: every other element in
 *   this row is non-interactive (no nested pill links, unlike the species
 *   row's per-entry links -- there is no transition-state detail route to
 *   send one to yet), so wrapping the full row content in one `<Link>` is
 *   valid HTML and does not create a nested-anchor conflict. The headline
 *   text itself can therefore no longer be its OWN nested link; it keeps
 *   its exact prior look (`.browse-row-title`'s emphasized, underlined
 *   style vs. the muted `span.browse-row-title` fallback already defined
 *   in `browse.css`) by choosing `<strong>` instead of `<a>` for the
 *   linked case -- `browse.css`'s override selector is written as
 *   `span.browse-row-title`, tag-specific, so any element with the class
 *   OTHER than a bare `<span>` still gets the emphasized rule. No new CSS
 *   needed or added.
 */
export function TransitionStateBrowseRow({ record }: { record: TransitionStateBrowseRecord }) {
    const evidence = [
        record.evidence_summary.has_opt && "opt",
        record.evidence_summary.has_freq && "freq",
        record.evidence_summary.has_sp && "sp",
        record.evidence_summary.has_irc && "irc",
        record.evidence_summary.has_path_search && "path search",
        record.evidence_summary.has_geometry_validation && "geometry validation",
        record.evidence_summary.has_scf_stability && "scf stability",
    ].filter((label): label is string => Boolean(label))

    const equation = record.reaction.equation ?? "Equation not recorded"
    const target = tsRowTarget(record)

    // Extracted (rather than re-derived inline) so the aria-label below and
    // the visible pills stay byte-identical in wording.
    const entryLabel = record.transition_state.label ?? "Unlabeled transition state"
    const entryStatusText = token(record.transition_state_entry.status)
    const entryReviewStatusText = token(record.transition_state_entry.review.status)
    const entryRef = record.transition_state_entry.transition_state_entry_ref

    const content: ReactNode = (
        <>
            <div className="browse-row-headline">
                {target
                    ? <strong className="browse-row-title">{equation}</strong>
                    : <span className="browse-row-title">{equation}</span>}
                <span className="browse-row-meta">
                    {record.reaction.family ? token(record.reaction.family) : "family not recorded"}
                    {" · "}
                    charge {chargeDisplay(record.transition_state_entry.charge)} · spin {spinDisplay(record.transition_state_entry.multiplicity)}
                </span>
            </div>
            <ul className="browse-row-entries">
                <li className="browse-entry-chip">
                    <span className="value-pill browse-entry-kind-pill">
                        {entryLabel}
                        {" · "}{entryStatusText}
                    </span>
                    <span className="value-pill value-pill--muted browse-entry-review">
                        {entryReviewStatusText}
                    </span>
                </li>
            </ul>
            <p className="browse-row-evidence">
                {evidence.length
                    ? `Evidence: ${evidence.join(" · ")} (${record.evidence_summary.calculation_count} calculations)`
                    : "No calculation evidence recorded"}
            </p>
            <code className="browse-row-ref">{entryRef}</code>
        </>
    )

    return (
        <li className="browse-row ts-browse-row">
            {/* No aria-label: the link wraps the whole row, so its text content
                -- equation, family/charge/spin, status pills, evidence line, ref
                -- IS its accessible name. A label here silenced everything it
                did not repeat (a re-review found family, charge, spin and the
                evidence line unannounced). */}
            {target
                ? (
                    <Link
                        className="browse-row-link"
                        to={target}
                    >
                        {content}
                    </Link>
                )
                : content}
        </li>
    )
}
