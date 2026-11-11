import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { CompareCell, CompareRow } from "../domain/speciesCompare"
import { CompareTable } from "./CompareTable"

const value = (text: string): CompareCell => ({ kind: "value", value: { label: "x", kind: "number", value: text, unit: "kJ/mol" } })
const absent = (words: string): CompareCell => ({ kind: "absent", words })
const COLUMNS = ["a", "b", "c"].map((key) => ({ key, header: <span>record {key}</span>, to: `/r/${key}` }))

function setup(rows: CompareRow[]) {
    render(<MemoryRouter><CompareTable caption="Compared" label="Compared table" columns={COLUMNS} rows={rows} /></MemoryRouter>)
}

afterEach(cleanup)

describe("CompareTable", () => {
    it("has one column per record, a unit column, and row headers for the quantities", () => {
        setup([{ key: "h", label: "Enthalpy", unit: "kJ/mol", cells: [value("1.00"), value("1.00"), value("2.00")] }])
        expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Quantity", "Unit", "record a", "record b", "record c"])
        const row = screen.getByRole("row", { name: /Enthalpy/ })
        expect(within(row).getByRole("rowheader")).toHaveTextContent("Enthalpy")
        expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("kJ/mol")
    })

    it("marks only the cell no other recorded cell shares, with text and a mark, and explains it in a legend", () => {
        setup([{ key: "h", label: "Enthalpy", unit: "kJ/mol", cells: [value("1.00"), value("1.00"), value("2.00")] }])
        const cells = within(screen.getByRole("row", { name: /Enthalpy/ })).getAllByRole("cell")
        expect(cells[1].className).not.toContain("differs")
        expect(cells[2].className).not.toContain("differs")
        expect(cells[3].className).toContain("compare-cell--differs")
        expect(cells[3]).toHaveTextContent("≠")
        expect(cells[3]).toHaveTextContent("differs from the others in this row")
        expect(screen.getByText(/differs from the others in this row; not a verdict\./, { selector: ".compare-legend" })).toBeVisible()
    })

    it("marks nothing when the recorded cells agree or only one is recorded, and says why a cell is empty", () => {
        setup([
            { key: "h", label: "Enthalpy", unit: "kJ/mol", cells: [value("1.00"), value("1.00"), absent("Not recorded")] },
            { key: "s", label: "Entropy", unit: "J/mol·K", cells: [value("5.00"), absent("Outside its range"), absent("Not recorded")] },
        ])
        expect(document.querySelectorAll(".compare-cell--differs")).toHaveLength(0)
        expect(screen.getByText("Outside its range")).toBeVisible()
        expect(screen.getAllByText("Not recorded")).toHaveLength(2)
    })

    it("links each value to its record and never orders, highlights or names a best value", () => {
        setup([{ key: "h", label: "Enthalpy", unit: "kJ/mol", cells: [value("3.00"), value("1.00"), value("2.00")] }])
        const row = screen.getByRole("row", { name: /Enthalpy/ })
        expect(within(row).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/r/a", "/r/b", "/r/c"])
        expect(within(row).getAllByRole("cell").slice(1).map((cell) => cell.textContent?.slice(0, 4))).toEqual(["3.00", "1.00", "2.00"])
        expect(document.body.textContent).not.toMatch(/\b(best|top|recommended|winner|preferred|lowest|highest)\b/i)
    })
})
