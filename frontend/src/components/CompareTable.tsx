import type { ReactNode } from "react"
import "../data-table.css"
import { differingCells, type CompareCell, type CompareRow } from "../domain/speciesCompare"
import { ValueLink } from "./ValueLink"

export type CompareTableColumn = {
    key: string
    header: ReactNode
    /** Where each value of this column is evidenced. */
    to: string | null
}

function CellView({ cell, to, differs }: { cell: CompareCell; to: string | null; differs: boolean }) {
    if (cell.kind === "absent") return <span className="compare-absent">{cell.words}</span>
    const { value } = cell
    const body = value.kind === "pointGroup" || value.kind === "code"
        ? <code className="t-preserve-case">{value.value}</code>
        : value.kind === "text"
            ? <>{value.value}</>
            : <ValueLink value={value.exponent ? <>{value.value} × 10<sup>{value.exponent}</sup></> : value.value} to={to} />
    return (
        <>
            {body}
            {differs && <span className="compare-differs"><span aria-hidden="true">≠</span><span className="data-table-sr"> differs from the others in this row</span></span>}
        </>
    )
}

/**
 * One column per record, one row per quantity, with the unit in its own column. A cell is marked when it differs from
 * the others in its row; the legend says that is not a verdict. No cell is ever called best, and nothing is sorted by value.
 */
export function CompareTable({ caption, label, columns, rows }: {
    caption: string
    label: string
    columns: readonly CompareTableColumn[]
    rows: readonly CompareRow[]
}) {
    return (
        <>
            <div className="table-scroll data-table-scroll" tabIndex={0} role="region" aria-label={label}>
                <table className="data-table data-table-grid compare-table">
                    <caption className="data-table-caption">{caption}</caption>
                    <thead>
                        <tr>
                            <th scope="col">Quantity</th>
                            <th scope="col">Unit</th>
                            {columns.map((column) => <th key={column.key} scope="col" className="compare-record">{column.header}</th>)}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
                            const differs = differingCells(row)
                            return (
                                <tr key={row.key}>
                                    <th scope="row">{row.label}</th>
                                    <td className="t-preserve-case">{row.unit}</td>
                                    {row.cells.map((cell, index) => (
                                        <td key={columns[index].key} className={differs[index] ? "compare-cell compare-cell--differs" : "compare-cell"}>
                                            <CellView cell={cell} to={columns[index].to} differs={differs[index]} />
                                        </td>
                                    ))}
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
            <p className="compare-legend"><span aria-hidden="true">≠</span> differs from the others in this row; not a verdict.</p>
        </>
    )
}
