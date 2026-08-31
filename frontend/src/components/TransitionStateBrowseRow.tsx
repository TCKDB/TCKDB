import { Link } from "react-router-dom"
import type { TransitionStateBrowseRecord } from "../api/browseApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"

function token(value: string) {
    return value.replaceAll("_", " ")
}

/**
 * A transition state has no formula of its own -- it is identified by the
 * REACTION it connects (see `TransitionStatesBrowseRequest`'s module
 * docstring), so this row leads with the rendered equation and reaction
 * family rather than a molecular headline. Links to `/reactions/:ref`,
 * the existing (placeholder-for-now) reaction route, rather than to a
 * transition-state detail page -- no such page exists yet on this
 * frontend, and this row does not invent one.
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

    return (
        <li className="browse-row ts-browse-row">
            <div className="browse-row-headline">
                {record.reaction.reaction_ref
                    ? <Link className="browse-row-title" to={`/reactions/${record.reaction.reaction_ref}`}>{equation}</Link>
                    : <span className="browse-row-title">{equation}</span>}
                <span className="browse-row-meta">
                    {record.reaction.family ? token(record.reaction.family) : "family not recorded"}
                    {" · "}
                    charge {chargeDisplay(record.transition_state_entry.charge)} · spin {spinDisplay(record.transition_state_entry.multiplicity)}
                </span>
            </div>
            <p className="browse-row-ts-label">
                {record.transition_state.label ?? "Unlabeled transition state"} · {token(record.transition_state_entry.status)}
                {" · review "}{token(record.transition_state_entry.review.status)}
            </p>
            <p className="browse-row-evidence">
                {evidence.length
                    ? `Evidence: ${evidence.join(" · ")} (${record.evidence_summary.calculation_count} calculations)`
                    : "No calculation evidence recorded"}
            </p>
            <code className="browse-row-ref">{record.transition_state_entry.transition_state_entry_ref}</code>
        </li>
    )
}
