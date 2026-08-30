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
 *
 * Visual priority answers the owner's report directly: "he opened the
 * Thermochemistry tab having selected conformer_2, and what sits there is
 * another conformer's record under a heading he has to read carefully to
 * notice. The tab answers a question he did not ask." So the selected
 * conformer's own group is THE ANSWER -- rendered first, and plainly, even
 * (especially) when it has nothing: an empty `thisConformer` bucket prints
 * its empty text with emphasis, before anything else on the panel. Records
 * traced to a DIFFERENT conformer are context, not the answer -- demoted
 * into a collapsed `<details>` below it. They are never filtered out and
 * never merged into the selected conformer's own group; only their default
 * visibility changes.
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
    const otherRecordCount = attribution.otherConformers.reduce((sum, group) => sum + group.records.length, 0)
    return (
        <>
            <AttributionGroup
                title={`From ${selectedLabel}`}
                note={thisConformerNote}
                records={attribution.thisConformer}
                emptyText={thisConformerEmptyText}
                renderRecord={renderRecord}
                primary
            />
            {attribution.otherConformers.length > 0 && (
                <details className="conformer-attribution-other">
                    <summary>
                        {otherRecordCount} record{otherRecordCount === 1 ? "" : "s"} from other conformers
                    </summary>
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
                </details>
            )}
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

function AttributionGroup<T>({ title, note, records, emptyText, renderRecord, primary = false }: {
    title: string
    note: string
    records: T[]
    emptyText: string
    renderRecord: (record: T) => ReactNode
    primary?: boolean
}) {
    return (
        <div className="conformer-evidence-group">
            <h3 className="conformer-evidence-group-heading">{title}</h3>
            <p className="section-note">{note}</p>
            {records.length === 0
                ? <p className={primary ? "conformer-attribution-answer" : "empty-projection"}>{emptyText}</p>
                : records.map((record) => renderRecord(record))}
        </div>
    )
}
