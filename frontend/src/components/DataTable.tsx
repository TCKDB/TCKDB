import { useMemo, useRef, useState, type ReactNode } from "react"
import "../data-table.css"
import { OrderNote } from "./OrderNote"

export type DataColumn<R> = {
    key: string
    header: ReactNode
    /** The header's words for the order note. */
    name: string
    cell: (row: R) => ReactNode
    /** Present when the reader may sort by this column; null sorts last in either direction. */
    sortValue?: (row: R) => string | number | null
    /** The first column labels its row. */
    rowHeader?: boolean
}

type Sort = { key: string; direction: "ascending" | "descending" }

function compareValues(a: string | number, b: string | number): number {
    return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "en")
}

/**
 * A table of records. It opens in the archive's order, which `order` words; a reader who sorts is told the table is
 * sorted by them and that this is not a recommendation. Sortable headers are buttons: Enter or Space sorts, a second
 * press reverses, a third restores the archive's order. Nothing here picks, highlights or ranks a row.
 */
export function DataTable<R>({ caption, label, rows, rowKey, columns, order }: {
    caption: string
    label: string
    rows: readonly R[]
    rowKey: (row: R) => string
    columns: readonly DataColumn<R>[]
    order?: string
}) {
    const [sort, setSort] = useState<Sort | null>(null)
    const buttons = useRef(new Map<string, HTMLButtonElement>())
    const sortedBy = columns.find((column) => column.key === sort?.key)
    const shown = useMemo(() => {
        if (!sort || !sortedBy?.sortValue) return rows
        const valueOf = sortedBy.sortValue
        const factor = sort.direction === "ascending" ? 1 : -1
        return rows
            .map((row, index) => ({ row, index, value: valueOf(row) }))
            .sort((a, b) => {
                if (a.value === null || b.value === null) return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1
                return factor * compareValues(a.value, b.value) || a.index - b.index
            })
            .map((entry) => entry.row)
    }, [rows, sort, sortedBy])

    function press(key: string) {
        setSort((current) => {
            if (current?.key !== key) return { key, direction: "ascending" }
            return current.direction === "ascending" ? { key, direction: "descending" } : null
        })
    }

    /** Back to the archive's order, with focus returned to the column the reader had sorted. */
    function reset() {
        const key = sort?.key
        setSort(null)
        if (key) buttons.current.get(key)?.focus()
    }

    return (
        <>
            <div role="status" className="data-table-order">
                {sort && sortedBy
                    ? <p className="order-note">Sorted by you: {sortedBy.name}, {sort.direction}. This is not a recommendation.{" "}
                        <button type="button" className="data-table-reset" onClick={reset}>Back to the archive's order</button></p>
                    : <OrderNote order={order} />}
            </div>
            <div className="table-scroll data-table-scroll" tabIndex={0} role="region" aria-label={label}>
                <table className="data-table data-table-grid">
                    <caption className="data-table-caption">{caption}</caption>
                    <thead>
                        <tr>
                            {columns.map((column) => (
                                <th
                                    key={column.key}
                                    scope="col"
                                    aria-sort={column.sortValue ? (sort?.key === column.key ? sort.direction : "none") : undefined}
                                >
                                    {column.sortValue
                                        ? <button
                                            type="button"
                                            className="data-table-sort"
                                            ref={(node) => { if (node) buttons.current.set(column.key, node); else buttons.current.delete(column.key) }}
                                            onClick={() => press(column.key)}
                                        >
                                            {column.header}{" "}<span className="data-table-sr">sort</span>
                                            <span className="data-table-arrow" aria-hidden="true">{sort?.key === column.key ? (sort.direction === "ascending" ? "↑" : "↓") : "↕"}</span>
                                        </button>
                                        : column.header}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {shown.map((row) => (
                            <tr key={rowKey(row)}>
                                {columns.map((column) => (column.rowHeader
                                    ? <th key={column.key} scope="row">{column.cell(row)}</th>
                                    : <td key={column.key}>{column.cell(row)}</td>))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    )
}
