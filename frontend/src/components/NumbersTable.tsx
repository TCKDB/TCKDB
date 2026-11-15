import { ROUNDED_NOTE } from "./QuantityValue"
import { LevelLabel } from "./LevelLabel"
import { conformerGroupWords, RECORD_KIND_NOUN } from "../domain/recordWords"
import { Fragment, useId, useMemo, useRef, useState, type ReactNode } from "react"
import { useOverflows } from "../hooks/useOverflows"
import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { HiddenByDefault } from "../api/listEnvelope"
import {
    distinctLevelCount, groupByLevel, isOneLevelThroughout, levelPartKey, levelParts, NO_LEVEL_KEY, numberAnchor, numberColumns, pointGroupParts,
    type Describes, type NumberColumn, type NumberKind, type NumberRow, type NumberValue,
} from "../domain/entryNumbers"
import { hiddenByDefaultNote } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

const KIND_WORDS: Record<NumberKind, string> = {
    thermo: "Thermochemistry",
    statmech: "Statistical mechanics",
    transport: "Transport",
}

export function PointGroup({ value }: { value: string }) {
    const { symbol, subscript } = pointGroupParts(value)
    return <span className="t-preserve-case">{symbol}{subscript && <sub>{subscript}</sub>}</span>
}

function ValueCell({ value, to, unit = true }: { value: NumberValue; to: string | null; unit?: boolean }) {
    if (value.value === null) return <span className="entry-numbers-absent">{value.absent ?? "Not recorded"}</span>
    if (value.kind === "pointGroup") return <PointGroup value={value.value} />
    if (value.kind === "code") return <code className="t-preserve-case">{value.value}</code>
    if (value.kind === "text") return <>{value.value}</>
    const number: ReactNode = value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value
    return <ValueLink value={number} unit={unit ? (value.unit ?? undefined) : undefined} to={to} full={value.full} />
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

/** A label read mid-sentence: its first letter lowered unless the word is a symbol. */
function lowerFirst(label: string): string {
    return /^[A-Z][a-z]/.test(label) ? label.charAt(0).toLowerCase() + label.slice(1) : label
}

/**
 * One table per kind of record, one row per record and one column per field, the unit in the column header. A field no
 * record of the kind carries is named once under the table. `hrefs` says where each row's values and `describes` link
 * go; `version` adds a line naming the version a row belongs to.
 */
export function NumbersTable<R extends NumberRow>({ rows, caption, label, groupLabels, hrefs, version, compact = false }: {
    rows: readonly R[]
    caption: string
    label: string
    groupLabels: Map<string, string>
    hrefs: (row: R) => { to: string | null; entryPath: string | null }
    version?: (row: R) => ReactNode
    /** The short form: no level count and no grouping control. */
    compact?: boolean
}) {
    const [grouped, setGrouped] = useState(false)
    const positions = useMemo(() => recordPositions(rows), [rows])
    const kinds = useMemo(() => [...new Set(rows.map((row) => row.kind))], [rows])
    const levelCount = new Set(rows.flatMap((row) => row.levels.map((level) => level.label))).size
    return (
        <>
            {!compact && levelCount > 0 && <p className="entry-numbers-levels">{levelCount} {levelCount === 1 ? "level" : "levels"} of theory.</p>}
            {!compact && distinctLevelCount(rows) >= 2 && (
                <div className="entry-numbers-controls">
                    <button type="button" className="entry-numbers-toggle" aria-pressed={grouped} onClick={() => setGrouped(!grouped)}>
                        Group by level of theory
                    </button>
                    {grouped && <span className="note">Rows keep their order inside each group.</span>}
                </div>
            )}
            {rows.some((row) => isOneLevelThroughout(row)) && (
                <p className="note">A level with no roles listed was used for geometry, frequency and energy.</p>
            )}
            {kinds.map((kind) => (
                <KindTable
                    key={kind}
                    kind={kind}
                    rows={rows.filter((row) => row.kind === kind)}
                    caption={`${caption}: ${KIND_WORDS[kind].toLowerCase()}`}
                    label={`${label}, ${KIND_WORDS[kind].toLowerCase()}`}
                    compact={compact}
                    grouped={grouped}
                    groupLabels={groupLabels}
                    hrefs={hrefs}
                    version={version}
                    positions={positions}
                />
            ))}
            {rows.some((row) => row.values.some((value) => value.full !== undefined)) && <p className="note">{ROUNDED_NOTE}</p>}
        </>
    )
}

function KindTable<R extends NumberRow>({ kind, rows, caption, label, compact, grouped, groupLabels, hrefs, version, positions }: {
    kind: NumberKind
    rows: readonly R[]
    caption: string
    label: string
    compact: boolean
    grouped: boolean
    groupLabels: Map<string, string>
    hrefs: (row: R) => { to: string | null; entryPath: string | null }
    version?: (row: R) => ReactNode
    positions: Map<R, number>
}) {
    const columns = useMemo(() => numberColumns(rows), [rows])
    const shown = columns.filter((column) => !column.allAbsent)
    const hidden = columns.filter((column) => column.allAbsent)
    const withDescribes = rows.some((row) => row.describes !== null)
    const groups = useMemo(() => (grouped ? groupByLevel(rows) : [{ key: null, rows: [...rows] }]), [grouped, rows])
    const span = 1 + shown.length + (withDescribes ? 1 : 0) + 1
    const boxRef = useRef<HTMLDivElement | null>(null)
    const hintId = useId()
    const overflows = useOverflows(boxRef)
    const Heading = compact ? "h4" : "h3"
    return (
        <div className="entry-numbers-kind-block">
            <Heading className="t-heading-2 entry-numbers-kind-heading">{KIND_WORDS[kind]} <span className="entry-numbers-count">{rows.length} {rows.length === 1 ? "record" : "records"}</span></Heading>
            {overflows && <p id={hintId} className="note entry-numbers-scroll-hint">Scroll sideways inside the box to see every column.</p>}
            <div className="table-scroll entry-numbers-table-box" ref={boxRef} tabIndex={0} role="region" aria-label={label} aria-describedby={overflows ? hintId : undefined}>
                <table className="data-table entry-numbers-table">
                    <caption className="entry-numbers-caption">{caption}</caption>
                    <thead>
                        <tr>
                            <th scope="col">Record</th>
                            {shown.map((column) => (
                                <th scope="col" key={column.label}>{column.label}{column.unit && <span className="entry-numbers-unit t-preserve-case"> ({column.unit})</span>}</th>
                            ))}
                            {withDescribes && <th scope="col">Describes</th>}
                            <th scope="col">Source</th>
                        </tr>
                    </thead>
                    {groups.map((group) => (
                        <tbody key={group.key ?? "all"}>
                            {group.key !== null && (
                                <tr className="entry-numbers-group">
                                    <th scope="rowgroup" colSpan={span}>
                                        <LevelHeading levels={group.rows[0].levels} /> · {group.rows.length} {group.rows.length === 1 ? "record" : "records"}
                                    </th>
                                </tr>
                            )}
                            {group.rows.map((row) => {
                                const { to, entryPath } = hrefs(row)
                                return (
                                    <NumberRowView
                                        key={`${row.kind}-${row.ref}`}
                                        row={row}
                                        columns={shown}
                                        withDescribes={withDescribes}
                                        entryPath={entryPath}
                                        to={to}
                                        groupLabels={groupLabels}
                                        version={version?.(row)}
                                        position={positions.get(row)}
                                    />
                                )
                            })}
                        </tbody>
                    ))}
                </table>
            </div>
            {hidden.length > 0 && <p className="note entry-numbers-absent-note">{rows.length === 1 ? "Not recorded in this record: " : "Not recorded in any of these records: "}{hidden.map((column) => lowerFirst(column.label)).join("; ")}.</p>}
        </div>
    )
}

/** A row's place among the rows of its kind and version, for telling same-kind records apart; absent for the only one. */
function recordPositions<R extends NumberRow>(rows: readonly R[]): Map<R, number> {
    const groupOf = (row: R) => `${row.kind}|${(row as { entryRef?: string | null }).entryRef ?? ""}`
    const sizes = new Map<string, number>()
    for (const row of rows) sizes.set(groupOf(row), (sizes.get(groupOf(row)) ?? 0) + 1)
    const seen = new Map<string, number>()
    const positions = new Map<R, number>()
    for (const row of rows) {
        const key = groupOf(row)
        const position = (seen.get(key) ?? 0) + 1
        seen.set(key, position)
        if ((sizes.get(key) ?? 0) > 1) positions.set(row, position)
    }
    return positions
}

function LevelHeading({ levels }: { levels: NumberRow["levels"] }) {
    if (levels.length === 0) return <>{NO_LEVEL_KEY}</>
    return <>{levelParts(levels).map((part, index) => (
        <Fragment key={levelPartKey(part)}>{index > 0 && " · "}{part.level ? <LevelLabel levelOfTheory={part.level} /> : <span className="t-preserve-case">{part.label}</span>} ({part.roles.join(", ")})</Fragment>
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
        const name = groupLabels.get(ref) ?? conformerGroupWords()
        body = <>one shape, {entryPath ? <Link to={`${entryPath}?conformer=${encodeURIComponent(ref)}`}>{name}</Link> : name}</>
    }
    return <>{body}{describes.implied && " (implied by the CHEMKIN format)"}</>
}

function NumberRowView({ row, columns, withDescribes, entryPath, to, groupLabels, version, position }: {
    row: NumberRow
    columns: readonly NumberColumn[]
    withDescribes: boolean
    position?: number
    entryPath: string | null
    to: string | null
    groupLabels: Map<string, string>
    version?: ReactNode
}) {
    const noun = RECORD_KIND_NOUN[row.kind]
    const named = position ? `${noun} ${position}` : noun
    return (
        <tr id={numberAnchor(row.kind, row.ref)}>
            <th scope="row">
                <span className="entry-numbers-record">
                    {to ? <Link to={to} aria-label={`Source: ${named}`}>{named}</Link> : named}
                </span>
                <span className="entry-numbers-review"><ReviewChip status={row.review} explain={false} /></span>
                {version && <span className="entry-numbers-version">{version}</span>}
            </th>
            {columns.map((column) => {
                const value = row.values.find((candidate) => candidate.label === column.label)
                return (
                    <td key={column.label}>
                        {value ? <ValueCell value={value} to={to} unit={column.unitInCell} /> : <span className="entry-numbers-absent">Not recorded</span>}
                    </td>
                )
            })}
            {withDescribes && (
                <td>
                    {row.describes ? <DescribesLine describes={row.describes} entryPath={entryPath} groupLabels={groupLabels} /> : <span className="entry-numbers-absent">Not recorded</span>}
                </td>
            )}
            <td className="entry-numbers-source">
                <SourceCell row={row} />
            </td>
        </tr>
    )
}

/** The levels a record was made at, one label per distinct level with the roles it played, then the programs. */
function SourceCell({ row }: { row: NumberRow }) {
    const parts = levelParts(row.levels)
    const plain = isOneLevelThroughout(row)
    const shownPrograms = row.programs.filter(Boolean)
    if (parts.length === 0 && shownPrograms.length === 0) return <span className="entry-numbers-absent">Not recorded</span>
    return (
        <>
            {parts.map((part) => (
                <span key={levelPartKey(part)} className="entry-numbers-source-level">
                    {part.level ? <LevelLabel levelOfTheory={part.level} /> : <span className="t-preserve-case">{part.label}</span>}
                    {!plain && <span className="entry-numbers-roles"> ({part.roles.join(", ")})</span>}
                </span>
            ))}
            {shownPrograms.length > 0 && <span className="entry-numbers-programs t-preserve-case">{shownPrograms.join(", ")}</span>}
        </>
    )
}
