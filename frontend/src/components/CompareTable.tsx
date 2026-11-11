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
}


/** One cell as every table of values shows it: the same numbers, exponents and point-group subscripts. */
export function CompareCellView({ cell, to }: { cell: CompareCell; to: string | null }) {
    if (cell.kind === "absent") return <span className="compare-absent">{cell.words}</span>
    const { value } = cell
    if (value.kind === "pointGroup") return <PointGroup value={value.value as string} />
    if (value.kind === "code") return <code className="t-preserve-case">{value.value}</code>
    if (value.kind === "text") return <>{value.value}</>
    return <ValueLink value={value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value} to={to} />
}

/**
 * One column per record, grouped by version, one row per quantity, with the unit in its own column. A row for a setting
 * (treatment, point group and the like) is marked when the setting is not the same in every record of a version; the
 * legend says that is not a verdict. A measured number is never marked, ordered or called best.
 */
export function CompareTable({ caption, label, groups, rows }: {
    caption: string
    label: string
    groups: readonly CompareTableGroup[]
    rows: readonly CompareRow[]
}) {
    const columns = groups.flatMap((group) => group.columns)
    const spans = groups.map((group) => group.columns.length)
    const hasSettings = rows.some((row) => row.compare === "exact")
    const groupStarts = new Set(groups.slice(1).map((_group, index) => groups.slice(0, index + 1).reduce((sum, group) => sum + group.columns.length, 0)))
    return (
        <>
            <div className="table-scroll data-table-scroll" tabIndex={0} role="region" aria-label={label}>
                <table className="data-table data-table-grid compare-table">
                    <caption className="data-table-caption">{caption}</caption>
                    <colgroup><col className="compare-col-quantity" /><col className="compare-col-unit" /></colgroup>
                    {groups.map((group) => <colgroup key={group.key} className="compare-colgroup">{group.columns.map((column) => <col key={column.key} />)}</colgroup>)}
                    <thead>
                        <tr>
                            <th scope="col" colSpan={2} className="compare-corner">Version</th>
                            {groups.map((group) => <th key={group.key} scope="colgroup" colSpan={group.columns.length} className="compare-group">{group.header}</th>)}
                        </tr>
                        <tr>
                            <th scope="col">Quantity</th>
                            <th scope="col" className="compare-unit-col">Unit</th>
                            {columns.map((column, index) => (
                                <th key={column.key} scope="col" className={groupStarts.has(index) ? "compare-record compare-group-start" : "compare-record"}>{column.header}</th>
                            ))}
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
                                        {differs && <span className="compare-differs"><span aria-hidden="true">≠</span> Not the same in every record</span>}
                                    </th>
                                    <td className="compare-unit-col t-preserve-case">{row.unit}</td>
                                    {row.cells.map((cell, index) => (
                                        <td key={columns[index].key} className={groupStarts.has(index) ? "compare-cell compare-group-start" : "compare-cell"}>
                                            <CompareCellView cell={cell} to={columns[index].to} />
                                        </td>
                                    ))}
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
            {hasSettings && <p className="compare-legend"><span aria-hidden="true">≠</span> a setting that is not the same in every record of a version; not a verdict.</p>}
        </>
    )
}
