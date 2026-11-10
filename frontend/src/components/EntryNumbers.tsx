import { Fragment, useMemo, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { EntryNumbers } from "../api/entryNumbersApi"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import {
    distinctLevelCount, evidenceIds, groupByLevel, levelParts, NO_LEVEL_KEY, numberAnchor, numbersOrderSentence,
    pointGroupParts, statmechRow, thermoRow, transportRow,
    type Describes, type NumberKind, type NumberRow, type NumberValue,
} from "../domain/entryNumbers"
import { hiddenByDefaultNote } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useEntryNumbers } from "../hooks/useEntryNumbers"
import { useScrollToHash } from "../hooks/useScrollToHash"
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

function PointGroup({ value }: { value: string }) {
    const { symbol, subscript } = pointGroupParts(value)
    return <span className="t-preserve-case">{symbol}{subscript && <sub>{subscript}</sub>}</span>
}

function ValueCell({ value, to }: { value: NumberValue; to: string }) {
    if (value.value === null) return <span className="entry-numbers-absent">{value.absent ?? "Not recorded"}</span>
    if (value.kind === "pointGroup") return <PointGroup value={value.value} />
    if (value.kind === "code") return <code className="t-preserve-case">{value.value}</code>
    if (value.kind === "text") return <>{value.value}</>
    const number: ReactNode = value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value
    return <ValueLink value={number} unit={value.unit ?? undefined} to={to} />
}

/** Every thermo, statmech and transport record of this entry with its values, levels, programs and review status. */
export function EntryNumbers({ entryRef, entryPath, conformerQuery, conformers }: {
    entryRef: string
    entryPath: string
    /** `?conformer=...` of the shape on screen, kept on same-page links so a link does not change the selection. */
    conformerQuery: string
    conformers: ConformerProjection[]
}) {
    useScrollToHash()
    const state = useEntryNumbers(entryRef)
    const [grouped, setGrouped] = useState(false)
    const data = state.status === "ready" ? state.data : null
    const rows = useMemo(() => (data
        ? [...data.thermo.records.map(thermoRow), ...data.statmech.records.map(statmechRow), ...data.transport.records.map(transportRow)]
        : []), [data])
    const evidence = useMemo(() => (data ? evidenceIds(data.thermo.records, data.statmech.records) : new Map<string, string>()), [data])
    const groups = useMemo(() => (grouped ? groupByLevel(rows) : [{ key: null, rows }]), [grouped, rows])
    const groupLabels = useMemo(() => new Map(conformers.map((conformer) => [conformer.conformer_group.conformer_group_ref, conformerLabel(conformer)])), [conformers])
    const levelCount = new Set(rows.map((row) => row.levelKey).filter((key) => key !== NO_LEVEL_KEY)).size
    const tabPath = (kind: NumberKind) => `${entryPath.replace(/\/(geometry|sp|statmech|thermo|transport)$/, "")}/${kind}`
    const tabLink = (kind: "thermo" | "statmech") => `${tabPath(kind)}${conformerQuery}`
    return (
        <section className="entry-numbers" aria-labelledby="entry-numbers">
            <SectionHeading
                id="entry-numbers"
                intro={<>
                    Every single value deposited for this one entry, each with the levels of theory and programs behind it. Values are never copied in from another entry.
                    Heat-capacity points, polynomial coefficients and frequencies are in the <Link to={tabLink("thermo")}>Thermochemistry</Link> and <Link to={tabLink("statmech")}>Statistical mechanics</Link> tabs below.
                </>}
            >
                Numbers for this entry
            </SectionHeading>
            {state.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the numbers for this entry…</p>}
            {state.status === "retrying" && (
                <p className="note" role="status" aria-busy="true">The archive is busy. Retrying in {formatWaitSeconds(state.retryAfterSeconds)}.</p>
            )}
            {state.status === "rate-limited" && (
                <p className="note" role="alert">The archive is receiving too many requests right now. Wait {formatWaitSeconds(state.retryAfterSeconds)} and reload the page.</p>
            )}
            {state.status === "failed" && <p className="note" role="alert">The numbers for this entry could not be loaded. The tabs below still show each record.</p>}
            {data && rows.length === 0 && (
                <p className="empty-projection">No thermochemistry, statistical mechanics or transport records are deposited for this entry.</p>
            )}
            {data && rows.length > 0 && (
                <>
                    <OrderNote order={numbersOrderSentence((["thermo", "statmech", "transport"] as const)
                        .filter((kind) => data[kind].records.length > 0)
                        .map((kind) => ({ kind, sort: data[kind].sort })))}
                    />
                    <ListNotes data={data} />
                    {levelCount > 0 && <p className="entry-numbers-levels">These values were computed at {levelCount} {levelCount === 1 ? "level" : "levels"} of theory.</p>}
                    {distinctLevelCount(rows) >= 2 && (
                        <div className="entry-numbers-controls">
                            <button type="button" className="entry-numbers-toggle" aria-pressed={grouped} onClick={() => setGrouped(!grouped)}>
                                Group by level of theory
                            </button>
                            {grouped && <span className="note">Rows keep their order inside each group.</span>}
                        </div>
                    )}
                    <div className="table-scroll entry-numbers-scroll" tabIndex={0} role="region" aria-label="Table of numbers">
                        <table className="data-table entry-numbers-table">
                            <caption className="entry-numbers-caption">Numbers for this entry, one row per record</caption>
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Values</th>
                                </tr>
                            </thead>
                            {groups.map((group) => (
                                <tbody key={group.key ?? "all"}>
                                    {group.key !== null && (
                                        <tr className="entry-numbers-group">
                                            <th scope="rowgroup" colSpan={2}>
                                                <LevelHeading levels={group.rows[0].levels} /> · {group.rows.length} {group.rows.length === 1 ? "record" : "records"}
                                            </th>
                                        </tr>
                                    )}
                                    {group.rows.map((row) => {
                                        const query = row.conformerGroupRef ? `?conformer=${encodeURIComponent(row.conformerGroupRef)}` : conformerQuery
                                        const to = `${tabPath(row.kind)}${query}#${evidence.get(numberAnchor(row.kind, row.ref)) ?? `${row.kind}-heading-${row.ref}`}`
                                        return <NumberRowView key={`${row.kind}-${row.ref}`} row={row} entryPath={entryPath} to={to} groupLabels={groupLabels} />
                                    })}
                                </tbody>
                            ))}
                        </table>
                    </div>
                </>
            )}
        </section>
    )
}

function LevelHeading({ levels }: { levels: NumberRow["levels"] }) {
    if (levels.length === 0) return <>{NO_LEVEL_KEY}</>
    return <>{levelParts(levels).map((part, index) => (
        <Fragment key={part.label}>{index > 0 && " · "}<span className="t-preserve-case">{part.label}</span> ({part.roles.join(", ")})</Fragment>
    ))}</>
}

function ListNotes({ data }: { data: EntryNumbers }) {
    const notes: string[] = []
    for (const [name, label, list] of [["Thermochemistry", "thermochemistry", data.thermo], ["Statistical mechanics", "statistical mechanics", data.statmech], ["Transport", "transport", data.transport]] as const) {
        if (list.capped) notes.push(`Showing the first ${list.records.length} of ${list.total} ${label} records. The ${name} tab below pages through all of them.`)
        const hidden = hiddenByDefaultNote(list.hiddenByDefault, `${label} records`)
        if (hidden) notes.push(hidden)
    }
    return <>{notes.map((note) => <p className="note" key={note} role="note">{note}</p>)}</>
}

function DescribesLine({ describes, entryPath, groupLabels }: { describes: Describes; entryPath: string; groupLabels: Map<string, string> }) {
    let body: ReactNode
    if (describes.kind === "all") body = "all shapes together"
    else if (describes.kind === "unnamed") body = "one shape (not named)"
    else if (describes.kind === "unknown") body = <code className="t-preserve-case">{describes.code}</code>
    else if (describes.kind === "unstated") body = "not stated by the depositor"
    else {
        const ref = describes.groupRef as string
        body = <>one shape, <Link to={`${entryPath}?conformer=${encodeURIComponent(ref)}`}>{groupLabels.get(ref) ?? <code className="t-preserve-case">{ref}</code>}</Link></>
    }
    return <>{body}{describes.implied && " (implied by the CHEMKIN format)"}</>
}

function NumberRowView({ row, entryPath, to, groupLabels }: {
    row: NumberRow
    entryPath: string
    to: string
    groupLabels: Map<string, string>
}) {
    return (
        <tr id={numberAnchor(row.kind, row.ref)}>
            <th scope="row">
                <span className="entry-numbers-kind">{KIND_WORDS[row.kind]}</span>
                <code className="t-preserve-case">{row.ref}</code>
                <span className="entry-numbers-review"><ReviewChip status={row.review} explain={false} /></span>
            </th>
            <td>
                <dl className="entry-numbers-values">
                    {row.values.map((value) => (
                        <Fragment key={value.label}>
                            <dt>{value.label}</dt>
                            <dd><ValueCell value={value} to={to} /></dd>
                        </Fragment>
                    ))}
                </dl>
                {row.describes && (
                    <p className="entry-numbers-describes"><span className="entry-numbers-key">Describes:</span> <DescribesLine describes={row.describes} entryPath={entryPath} groupLabels={groupLabels} /></p>
                )}
                <ValueSource source={{ recordType: row.kind, ref: row.ref, to }} levels={row.levels} programs={row.programs} />
            </td>
        </tr>
    )
}
