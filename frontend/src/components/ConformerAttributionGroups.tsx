import type { ReactNode } from "react"
import type { ConformerAttribution } from "../domain/conformerEvidence"

/**
 * Shared three-way conformer-attribution layout for `EntryThermoSection`
 * and `EntryStatmechSection`: records traced to the selected conformer,
 * records traced to a DIFFERENT named conformer (one heading per distinct
 * conformer the wire actually names, never merged into a generic "other"
 * bucket), and records carrying no conformer link at all. Each heading
 * claims only what is true of its own members — see
 * `domain/conformerEvidence.ts`'s `partitionByConformerLink`.
 */
export function ConformerAttributionGroups<T>({
    attribution, selectedLabel, renderRecord,
    thisConformerNote, thisConformerEmptyText,
    otherConformerNote,
    noLinkNote, noLinkEmptyText,
}: {
    attribution: ConformerAttribution<T>
    selectedLabel: string
    renderRecord: (record: T) => ReactNode
    thisConformerNote: string
    thisConformerEmptyText: string
    otherConformerNote: string
    noLinkNote: string
    noLinkEmptyText: string
}) {
    return (
        <>
            <AttributionGroup
                title={`From ${selectedLabel}`}
                note={thisConformerNote}
                records={attribution.thisConformer}
                emptyText={thisConformerEmptyText}
                renderRecord={renderRecord}
            />
            {attribution.otherConformers.map(({ ref, label, records }) => (
                <AttributionGroup
                    key={ref}
                    title={`From ${label}`}
                    note={otherConformerNote}
                    records={records}
                    emptyText=""
                    renderRecord={renderRecord}
                />
            ))}
            <AttributionGroup
                title="No conformer link"
                note={noLinkNote}
                records={attribution.noLink}
                emptyText={noLinkEmptyText}
                renderRecord={renderRecord}
            />
        </>
    )
}

function AttributionGroup<T>({ title, note, records, emptyText, renderRecord }: {
    title: string
    note: string
    records: T[]
    emptyText: string
    renderRecord: (record: T) => ReactNode
}) {
    return (
        <div className="conformer-evidence-group">
            <h3 className="conformer-evidence-group-heading">{title}</h3>
            <p className="section-note">{note}</p>
            {records.length === 0 ? <p className="empty-projection">{emptyText}</p> : records.map((record) => renderRecord(record))}
        </div>
    )
}
