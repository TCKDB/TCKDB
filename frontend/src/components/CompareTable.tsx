import { MIXED_ROUNDED_NOTE } from "./QuantityValue"
import type { ReactNode } from "react"
import "../data-table.css"
import { displayLabel, rowDiffers, type CompareCell, type CompareRow } from "../domain/speciesCompare"
import { PointGroup } from "./NumbersTable"
import { ValueLink } from "./ValueLink"

export type CompareTableColumn = {
    key: string
    header: ReactNode
    /** Where each value of this column is evidenced. */
    to: string | null
}

export type CompareTableGroup = {
    key: string
    /** The version the records below it belong to. */
    header: ReactNode
    columns: CompareTableColumn[]
    /** A difference column closing the group, for the rows that carry a difference for it. Never spans two groups. */
    delta?: { header: ReactNode }
}

/** One cell as every table of values shows it: the same numbers, exponents and point-group subscripts. */
export function CompareCellView({ cell, to, showUnit = false }: { cell: CompareCell; to: string | null; showUnit?: boolean }) {
    if (cell.kind === "absent") return <span className="compare-absent">{cell.words}</span>
    const { value } = cell
    if (cell.note) return <><CompareValue value={value} to={to} showUnit={showUnit} /><span className="compare-cell-note">{cell.note}</span></>
    return <CompareValue value={value} to={to} showUnit={showUnit} />
}

function CompareValue({ value, to, showUnit }: { value: Extract<CompareCell, { kind: "value" }>["value"]; to: string | null; showUnit: boolean }) {
    if (value.kind === "pointGroup") return <PointGroup value={value.value as string} />
    if (value.kind === "code") return <code className="t-preserve-case">{value.value}</code>
    if (value.kind === "text") return <>{value.value}</>
    return <ValueLink value={value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value} unit={showUnit ? (value.unit ?? undefined) : undefined} to={to} full={value.full} />
}

/**
 * One column per record, grouped by version, one row per quantity, with the unit in its own column. A row for a setting
 * (treatment, point group and the like) is marked when the setting is not the same within a version; the
 * legend says that is not a verdict. A measured number is never marked, ordered or called best.
 */
export function CompareTable({ caption, label, groups, rows, groupNoun = "version" }: {
    caption: string
    /** What a group is, for the corner cell and the marks: "version" (the default) or "group". */
    groupNoun?: string
    label: string
    groups: readonly CompareTableGroup[]
    rows: readonly CompareRow[]
}) {
    const spans = groups.map((group) => group.columns.length)
    const hasSettings = rows.some((row) => row.compare === "exact")
    const starts = groups.map((_group, index) => spans.slice(0, index).reduce((sum, span) => sum + span, 0))
    const edge = (index: number) => (index > 0 ? " compare-group-start" : "")
    return (
        <>
            <div className="table-scroll data-table-scroll" tabIndex={0} role="region" aria-label={label}>
                <table className="data-table data-table-grid compare-table">
                    <caption className="data-table-caption">{caption}</caption>
                    <colgroup><col className="compare-col-quantity" /><col className="compare-col-unit" /></colgroup>
                    {groups.map((group) => (
                        <colgroup key={group.key} className="compare-colgroup">
                            {group.columns.map((column) => <col key={column.key} />)}
                            {group.delta && <col />}
                        </colgroup>
                    ))}
                    <thead>
                        <tr>
                            <td className="compare-corner">{groupNoun === "version" ? "Version" : "Group"}</td>
                            <td className="compare-unit-col" aria-hidden="true" />
                            {groups.map((group) => <th key={group.key} scope="colgroup" colSpan={group.columns.length + (group.delta ? 1 : 0)} className="compare-group">{group.header}</th>)}
                        </tr>
                        <tr>
                            <th scope="col">Quantity</th>
                            <th scope="col" className="compare-unit-col">Unit</th>
                            {groups.flatMap((group, groupIndex) => [
                                ...group.columns.map((column, index) => (
                                    <th key={column.key} scope="col" className={index === 0 && groupIndex > 0 ? "compare-record compare-group-start" : "compare-record"}>{column.header}</th>
                                )),
                                ...(group.delta ? [<th key={`${group.key}-delta`} scope="col" className="compare-record compare-delta">{group.delta.header}</th>] : []),
                            ])}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
                            const differs = rowDiffers(row, spans)
                            return (
                                <tr key={row.key}>
                                    <th scope="row">
                                        {displayLabel(row.label)}
                                        {row.unit && <span className="compare-row-unit t-preserve-case">{row.unit}</span>}
                                        {differs && <span className="compare-differs"><span aria-hidden="true">≠</span> Not the same within a {groupNoun}</span>}
                                    </th>
                                    <td className="compare-unit-col t-preserve-case">{row.unit}</td>
                                    {groups.flatMap((group, groupIndex) => {
                                        const cells = group.columns.map((column, index) => (
                                            <td key={column.key} className={index === 0 ? `compare-cell${edge(groupIndex)}` : "compare-cell"}>
                                                <CompareCellView cell={row.cells[starts[groupIndex] + index]} to={column.to} showUnit={row.unit === null} />
                                            </td>
                                        ))
                                        const delta = row.deltas?.[groupIndex]
                                        return group.delta
                                            ? [...cells, <td key={`${group.key}-delta`} className="compare-cell compare-delta">{delta && <CompareCellView cell={delta} to={null} showUnit={row.unit === null} />}</td>]
                                            : cells
                                    })}
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
            {rows.some((row) => row.cells.some((cell) => cell.kind === "value" && cell.value.full !== undefined)) && <p className="note">{MIXED_ROUNDED_NOTE}</p>}
            {hasSettings && <p className="compare-legend"><span aria-hidden="true">≠</span> a setting that is not the same within one {groupNoun}; not a verdict.</p>}
        </>
    )
}
