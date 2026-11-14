import { Link } from "react-router-dom"
import { RecordLink } from "./RecordLink"
import { recordWords } from "../domain/recordWords"
import "../molecule-page.css"
import type { ConformerUsage, ConformerUsageGroup } from "../api/conformerUsageApi"
import type { LoadedState } from "../hooks/useLoaded"

/** The thermochemistry and rate coefficients that name one conformer group, and where it was merged; never inferred. */
export function ConformerUsedBy({ usage, group, entryRef, groupRef, labelOf, showLabel = true }: {
    usage: LoadedState<ConformerUsage>
    group: ConformerUsageGroup | undefined
    entryRef: string
    groupRef: string
    labelOf: (ref: string) => string
    /** False under a heading that already says "Used by". */
    showLabel?: boolean
}) {
    if (usage.status !== "ready") return null
    if (!group || (group.thermo.length === 0 && group.kinetics.length === 0 && !group.merged_into_conformer_group_ref)) {
        return <p className="shape-usedby">{showLabel && <span className="shape-usedby-key">Used by</span>} No thermochemistry or rate coefficient names this shape.</p>
    }
    const merged = group.merged_into_conformer_group_ref
    return (
        <div className="shape-usedby">
            {showLabel && <span className="shape-usedby-key">Used by</span>}
            {group.thermo.length > 0 && (
                <p>
                    Thermochemistry:{" "}
                    {group.thermo.map((record, index) => (
                        <span key={record.thermo_ref}>
                            {index > 0 && ", "}
                            <RecordLink kind="thermo" to={`/species-entries/${entryRef}/thermo?conformer=${encodeURIComponent(groupRef)}`} hash={`thermo-heading-${record.thermo_ref}`} label={recordWords(index + 1)} />
                        </span>
                    ))}
                </p>
            )}
            {group.kinetics.length > 0 && (
                <p>
                    Rate coefficients:{" "}
                    {group.kinetics.map((record, index) => (
                        <span key={`${record.kinetics_ref}-${record.role ?? ""}-${index}`}>
                            {index > 0 && ", "}
                            <RecordLink kind="kinetics" to={`/reaction-entries/${record.reaction_entry_ref}`} hash="kinetics-heading" label={recordWords(index + 1)} />
                            {record.role && ` (${record.role})`}
                        </span>
                    ))}
                </p>
            )}
            {merged && (
                <p>This shape was merged into <Link to={`/conformer-groups/${merged}`}>{labelOf(merged)}</Link>. Records that name it are listed here and were not moved.</p>
            )}
        </div>
    )
}
