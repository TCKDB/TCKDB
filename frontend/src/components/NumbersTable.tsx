import { Fragment, useMemo, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { HiddenByDefault } from "../api/listEnvelope"
import {
    distinctLevelCount, groupByLevel, levelParts, NO_LEVEL_KEY, numberAnchor, pointGroupParts,
    type Describes, type NumberKind, type NumberRow, type NumberValue,
} from "../domain/entryNumbers"
import { hiddenByDefaultNote } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
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

function ValueCell({ value, to }: { value: NumberValue; to: string | null }) {
    if (value.value === null) return <span className="entry-numbers-absent">{value.absent ?? "Not recorded"}</span>
    if (value.kind === "pointGroup") return <PointGroup value={value.value} />
    if (value.kind === "code") return <code className="t-preserve-case">{value.value}</code>
    if (value.kind === "text") return <>{value.value}</>
    const number: ReactNode = value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value
    return <ValueLink value={number} unit={value.unit ?? undefined} to={to} />
}

/** The loading, waiting and failure lines of a list that walks every page of a read; nothing once it is ready. */
export function WalkStatus({ state, loading, failed }: {
    state: { status: string; retryAfterSeconds?: number }
    loading: string
    failed: string
}) {
    const wait = formatWaitSeconds(state.retryAfterSeconds ?? 0)
    if (state.status === "loading") return <p className="note" role="status" aria-busy="true">{loading}</p>
    if (state.status === "retrying") return <p className="note" role="status" aria-busy="true">The archive is busy. Retrying in {wait}.</p>
    if (state.status === "rate-limited") return <p className="note" role="alert">The archive is receiving too many requests right now. Wait {wait} and reload the page.</p>
    if (state.status === "failed") return <p className="note" role="alert">{failed}</p>
    return null
}

/**
 * Rows with their values, levels, programs and review status, optionally grouped by level of theory. `hrefs` says
 * where each row's values and `describes` link go; `version` adds a line naming the version a row belongs to.
 */
export function NumbersTable<R extends NumberRow>({ rows, caption, label, groupLabels, hrefs, version }: {
    rows: readonly R[]
    caption: string
    label: string
    groupLabels: Map<string, string>
    hrefs: (row: R) => { to: string | null; entryPath: string | null }
    version?: (row: R) => ReactNode
}) {
    const [grouped, setGrouped] = useState(false)
    const groups = useMemo(() => (grouped ? groupByLevel(rows) : [{ key: null, rows: [...rows] }]), [grouped, rows])
    const levelCount = new Set(rows.flatMap((row) => row.levels.map((level) => level.label))).size
    return (
        <>
            {levelCount > 0 && <p className="entry-numbers-levels">These values were computed at {levelCount} {levelCount === 1 ? "level" : "levels"} of theory.</p>}
            {distinctLevelCount(rows) >= 2 && (
                <div className="entry-numbers-controls">
                    <button type="button" className="entry-numbers-toggle" aria-pressed={grouped} onClick={() => setGrouped(!grouped)}>
                        Group by level of theory
                    </button>
                    {grouped && <span className="note">Rows keep their order inside each group.</span>}
                </div>
            )}
            <div className="table-scroll entry-numbers-scroll" tabIndex={0} role="region" aria-label={label}>
                <table className="data-table entry-numbers-table">
                    <caption className="entry-numbers-caption">{caption}</caption>
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
                                const { to, entryPath } = hrefs(row)
                                return <NumberRowView key={`${row.kind}-${row.ref}`} row={row} entryPath={entryPath} to={to} groupLabels={groupLabels} version={version?.(row)} />
                            })}
                        </tbody>
                    ))}
                </table>
            </div>
        </>
    )
}

function LevelHeading({ levels }: { levels: NumberRow["levels"] }) {
    if (levels.length === 0) return <>{NO_LEVEL_KEY}</>
    return <>{levelParts(levels).map((part, index) => (
        <Fragment key={part.label}>{index > 0 && " · "}<span className="t-preserve-case">{part.label}</span> ({part.roles.join(", ")})</Fragment>
    ))}</>
}

type CountedList = { capped: boolean; records: readonly unknown[]; total: number; hiddenByDefault: HiddenByDefault | null }

/** Notes for a list the cap cut short and for records the default visibility left out. `rest` says where the remainder is. */
export function ListNotes({ lists, rest }: { lists: readonly (readonly [string, CountedList])[]; rest: (label: string) => string }) {
    const notes: string[] = []
    for (const [label, list] of lists) {
        if (list.capped) notes.push(`Showing the first ${list.records.length.toLocaleString("en-US")} of ${list.total.toLocaleString("en-US")} ${label} records. ${rest(label)}`)
        const hidden = hiddenByDefaultNote(list.hiddenByDefault, `${label} records`, `${label} record`)
        if (hidden) notes.push(hidden)
    }
    return <>{notes.map((note) => <p className="note" key={note} role="note">{note}</p>)}</>
}

function DescribesLine({ describes, entryPath, groupLabels }: { describes: Describes; entryPath: string | null; groupLabels: Map<string, string> }) {
    let body: ReactNode
    if (describes.kind === "all") body = "all shapes together"
    else if (describes.kind === "unnamed") body = "one shape (not named)"
    else if (describes.kind === "unknown") body = <code className="t-preserve-case">{describes.code}</code>
    else if (describes.kind === "unstated") body = "not stated by the depositor"
    else {
        const ref = describes.groupRef as string
        const name = groupLabels.get(ref) ?? <code className="t-preserve-case">{ref}</code>
        body = <>one shape, {entryPath ? <Link to={`${entryPath}?conformer=${encodeURIComponent(ref)}`}>{name}</Link> : name}</>
    }
    return <>{body}{describes.implied && " (implied by the CHEMKIN format)"}</>
}

function NumberRowView({ row, entryPath, to, groupLabels, version }: {
    row: NumberRow
    entryPath: string | null
    to: string | null
    groupLabels: Map<string, string>
    version?: ReactNode
}) {
    return (
        <tr id={numberAnchor(row.kind, row.ref)}>
            <th scope="row">
                <span className="entry-numbers-kind">{KIND_WORDS[row.kind]}</span>
                <code className="t-preserve-case">{row.ref}</code>
                {version && <span className="entry-numbers-version">{version}</span>}
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
                <ValueSource source={{ recordType: row.kind, ref: row.ref, ...(to ? { to } : {}) }} levels={row.levels} programs={row.programs} />
            </td>
        </tr>
    )
}
