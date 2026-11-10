import { Fragment, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { loadEntryNumbers, type EntryNumbers as EntryNumbersData } from "../api/entryNumbersApi"
import { conformerLabel } from "../domain/conformerEvidence"
import {
    groupByLevel, numberAnchor, thermoRecordProductLevels, numbersOrderSentence, statmechRow, thermoRow, transportRow,
    type Describes, type NumberKind, type NumberRow,
} from "../domain/entryNumbers"
import { hiddenByDefaultNote } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { SectionHeading } from "./PageSections"
import { OrderNote } from "./OrderNote"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"
import { ValueSource } from "./ValueSource"

const KIND_WORDS: Record<NumberKind, string> = {
    thermo: "Thermochemistry",
    statmech: "Statistical mechanics",
    transport: "Transport",
}

const KIND_TAB: Record<NumberKind, string> = { thermo: "thermo", statmech: "statmech", transport: "transport" }

function rowsOf(data: EntryNumbersData): NumberRow[] {
    return [
        ...data.thermo.records.map((record) => thermoRow(record, thermoRecordProductLevels(record))),
        ...data.statmech.records.map(statmechRow),
        ...data.transport.records.map(transportRow),
    ]
}

/** Every thermo, statmech and transport record of this entry with its values, levels, programs and review status. */
export function EntryNumbers({ entryRef, entryPath, conformerQuery, conformers }: {
    entryRef: string
    entryPath: string
    /** `?conformer=...` of the shape on screen, kept on same-page links so a link does not change the selection. */
    conformerQuery: string
    conformers: ConformerProjection[]
}) {
    const state = useScientificRecord(entryRef, loadEntryNumbers)
    const [grouped, setGrouped] = useState(false)
    const data = state.status === "ready" ? state.record : null
    const rows = useMemo(() => (data ? rowsOf(data) : []), [data])
    const groups = useMemo(() => (grouped ? groupByLevel(rows) : [{ key: null, rows }]), [grouped, rows])
    const groupLabels = useMemo(() => new Map(conformers.map((conformer) => [conformer.conformer_group.conformer_group_ref, conformerLabel(conformer)])), [conformers])

    return (
        <section className="entry-numbers" aria-labelledby="entry-numbers">
            <SectionHeading id="entry-numbers" intro="Every value deposited for this one entry, each with the level of theory and program that produced it. Values are never copied in from another entry.">
                Numbers for this entry
            </SectionHeading>
            {state.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the numbers for this entry…</p>}
            {state.status === "retrying" && <p className="note" role="status" aria-busy="true">The archive is busy. Retrying automatically.</p>}
            {state.status === "rate-limited" && (
                <p className="note" role="alert">The archive is receiving too many requests right now. Wait {formatWaitSeconds(state.retryAfterSeconds)} and reload the page.</p>
            )}
            {(state.status === "missing" || state.status === "invalid" || state.status === "unprocessable" || state.status === "malformed" || state.status === "unavailable") && (
                <p className="note" role="alert">The numbers for this entry could not be loaded. The tabs below still show each record.</p>
            )}
            {data && rows.length === 0 && (
                <p className="empty-projection">No thermochemistry, statistical mechanics or transport records are deposited for this entry.</p>
            )}
            {data && rows.length > 0 && (
                <>
                    <OrderNote order={numbersOrderSentence({ thermo: data.thermo.sort, statmech: data.statmech.sort, transport: data.transport.sort })} />
                    <ListNotes data={data} />
                    <div className="entry-numbers-controls">
                        <button type="button" className="entry-numbers-toggle" aria-pressed={grouped} onClick={() => setGrouped(!grouped)}>
                            Group by level of theory
                        </button>
                        {grouped && <span className="note">Rows keep their order inside each group.</span>}
                    </div>
                    <div className="table-scroll entry-numbers-scroll" tabIndex={0} role="region" aria-label="Table of numbers">
                        <table className="data-table entry-numbers-table">
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Values</th>
                                    <th scope="col">Level of theory</th>
                                    <th scope="col">Program</th>
                                    <th scope="col">Review status</th>
                                </tr>
                            </thead>
                            {groups.map((group) => (
                                <tbody key={group.key ?? "all"}>
                                    {group.key !== null && (
                                        <tr className="entry-numbers-group">
                                            <th scope="colgroup" colSpan={5}>
                                                <span className="t-preserve-case">{group.key}</span> · {group.rows.length} {group.rows.length === 1 ? "record" : "records"}
                                            </th>
                                        </tr>
                                    )}
                                    {group.rows.map((row) => (
                                        <NumberRowView key={`${row.kind}-${row.ref}`} row={row} entryPath={entryPath} conformerQuery={conformerQuery} groupLabels={groupLabels} />
                                    ))}
                                </tbody>
                            ))}
                        </table>
                    </div>
                </>
            )}
        </section>
    )
}

function ListNotes({ data }: { data: EntryNumbersData }) {
    const notes: string[] = []
    for (const [name, list] of [["thermochemistry", data.thermo], ["statistical mechanics", data.statmech], ["transport", data.transport]] as const) {
        if (list.capped) notes.push(`Showing the first ${list.records.length} of ${list.total} ${name} records. The tab for it below pages through all of them.`)
        const hidden = hiddenByDefaultNote(list.hiddenByDefault, `${name} records`)
        if (hidden) notes.push(hidden)
    }
    return <>{notes.map((note) => <p className="note" key={note} role="note">{note}</p>)}</>
}

function DescribesLine({ describes, entryPath, groupLabels }: { describes: Describes; entryPath: string; groupLabels: Map<string, string> }) {
    if (describes.kind === "all") return <>All shapes together</>
    if (describes.kind === "unstated") return <>Not stated by the depositor</>
    const label = groupLabels.get(describes.groupRef)
    return (
        <>One shape: <Link to={`${entryPath}?conformer=${encodeURIComponent(describes.groupRef)}`}>{label ?? <code className="t-preserve-case">{describes.groupRef}</code>}</Link></>
    )
}

function NumberRowView({ row, entryPath, conformerQuery, groupLabels }: {
    row: NumberRow
    entryPath: string
    conformerQuery: string
    groupLabels: Map<string, string>
}) {
    const anchor = numberAnchor(row.kind, row.ref)
    const tabPath = entryPath.replace(/\/(geometry|sp|statmech|thermo|transport)$/, "") + `/${KIND_TAB[row.kind]}`
    const evidence = `${tabPath}${conformerQuery}#${row.kind}-heading-${row.ref}`
    const sameRow = `${entryPath}${conformerQuery}#${anchor}`
    return (
        <tr id={anchor}>
            <th scope="row">
                <span className="entry-numbers-kind">{KIND_WORDS[row.kind]}</span>
                <code className="t-preserve-case">{row.ref}</code>
            </th>
            <td>
                <dl className="entry-numbers-values">
                    {row.values.map((value) => (
                        <Fragment key={value.label}>
                            <dt>{value.label}</dt>
                            <dd>{value.value === null ? <span className="entry-numbers-absent">Not recorded</span> : <ValueLink value={value.value} unit={value.unit ?? undefined} to={evidence} />}</dd>
                        </Fragment>
                    ))}
                </dl>
                {row.describes && (
                    <p className="entry-numbers-describes"><span className="entry-numbers-key">Describes</span> <DescribesLine describes={row.describes} entryPath={entryPath} groupLabels={groupLabels} /></p>
                )}
                <ValueSource
                    source={{ recordType: row.kind, ref: row.ref, to: sameRow }}
                    levels={row.kind === "thermo" ? row.levels : undefined}
                    programs={row.kind === "thermo" ? row.programs : undefined}
                    review={row.kind === "thermo" ? row.review : undefined}
                    compact={row.kind !== "thermo"}
                />
            </td>
            <td>
                {row.levels.length === 0
                    ? <span className="entry-numbers-absent">Not recorded</span>
                    : <ul className="entry-numbers-list">{row.levels.map((level) => <li key={level.role}>{level.role} <span className="t-preserve-case">{level.label}</span></li>)}</ul>}
            </td>
            <td>
                {row.programs.length === 0
                    ? <span className="entry-numbers-absent">Not recorded</span>
                    : <ul className="entry-numbers-list">{row.programs.map((program) => <li key={program} className="t-preserve-case">{program}</li>)}</ul>}
            </td>
            <td><ReviewChip status={row.review} explain={false} /></td>
        </tr>
    )
}
