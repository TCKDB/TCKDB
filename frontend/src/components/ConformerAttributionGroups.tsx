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
    // A record can legitimately be named by MORE THAN ONE other-conformer
    // bucket (`partitionByConformerLink` files a multi-linked record under
    // EVERY group it names, on purpose -- an ensemble-level statmech
    // treatment spanning several basins is the real, on-the-wire case). The
    // buckets therefore are not disjoint: summing their lengths (the prior
    // `reduce`) double-counts a record naming two other groups, and mapping
    // each bucket independently rendered that SAME record twice into the
    // DOM with the SAME `aria-labelledby`/id (`thermo-heading-<ref>` /
    // `statmech-heading-<ref>`), making the id ambiguous. Grouping by
    // object identity first -- the same `record` reference IS pushed into
    // every bucket it belongs to, so `Map` keyed on the record itself finds
    // exactly the true duplicates -- fixes both: the count becomes the
    // number of DISTINCT records, and each one renders exactly once, under
    // a heading naming every other conformer it traces to.
    const labelsByRecord = new Map<T, string[]>()
    for (const { label, records } of attribution.otherConformers) {
        for (const record of records) {
            const labels = labelsByRecord.get(record)
            if (labels) labels.push(label)
            else labelsByRecord.set(record, [label])
        }
    }
    const otherRecordCount = labelsByRecord.size
    // Records that trace to the exact same SET of other conformers still
    // share one heading (two different records both linked only to
    // "Conformer Group 1" render together under "From Conformer Group 1",
    // as before) -- only a record whose own label set differs gets its own
    // joint heading, e.g. "From Conformer Group 2, Conformer Group 3" for
    // the one record actually linked to both.
    const otherGroupsByLabel = new Map<string, T[]>()
    for (const [record, labels] of labelsByRecord) {
        const key = labels.join(", ")
        const bucket = otherGroupsByLabel.get(key)
        if (bucket) bucket.push(record)
        else otherGroupsByLabel.set(key, [record])
    }
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
            {otherRecordCount > 0 && (
                <details className="conformer-attribution-other">
                    <summary>
                        {otherRecordCount} record{otherRecordCount === 1 ? "" : "s"} from other conformers
                    </summary>
                    {[...otherGroupsByLabel.entries()].map(([labelKey, records]) => (
                        <AttributionGroup
                            key={labelKey}
                            title={`From ${labelKey}`}
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
