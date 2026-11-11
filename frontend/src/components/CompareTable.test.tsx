import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { CompareCell, CompareRow } from "../domain/speciesCompare"
import { rankingWords, textOf } from "../test/rankingWords"
import { CompareTable } from "./CompareTable"

const number = (text: string, exponent?: string): CompareCell => ({ kind: "value", value: { label: "x", kind: "number", value: text, unit: "kJ/mol", exponent } })
const word = (text: string): CompareCell => ({ kind: "value", value: { label: "x", kind: "text", value: text, unit: null } })
const group = (value: string): CompareCell => ({ kind: "value", value: { label: "Point group", kind: "pointGroup", value, unit: null } })
const absent = (words: string): CompareCell => ({ kind: "absent", words })
const key = (name: string) => ({ key: name, header: <span>record {name}</span>, to: `/r/${name}` })
const ONE = [{ key: "g", header: <span>version one</span>, columns: ["a", "b", "c", "d"].map(key) }]
const TWO = [{ key: "g1", header: <span>version one</span>, columns: ["a", "b"].map(key) }, { key: "g2", header: <span>version two</span>, columns: ["c", "d"].map(key) }]
const setting = (label: string, cells: CompareCell[]): CompareRow => ({ key: label, label, unit: null, compare: "exact", cells })
const measured = (label: string, cells: CompareCell[]): CompareRow => ({ key: label, label, unit: "kJ/mol", compare: "continuous", cells })

function setup(rows: CompareRow[], groups = ONE) {
    render(<MemoryRouter><CompareTable caption="Compared" label="Compared table" groups={groups} rows={rows} /></MemoryRouter>)
}

afterEach(cleanup)

describe("CompareTable", () => {
    it("has one column per record under a version header, a unit column, and row headers for the quantities", () => {
        setup([measured("Enthalpy", [number("1.00"), number("1.00"), number("2.00"), number("2.00")])])
        expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Version", "version one", "Quantity", "Unit", "record a", "record b", "record c", "record d"])
        expect(screen.getByRole("columnheader", { name: "version one" })).toHaveAttribute("colspan", "4")
        const row = screen.getByRole("row", { name: /Enthalpy/ })
        expect(within(row).getByRole("rowheader")).toHaveTextContent("Enthalpy")
        expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("kJ/mol")
    })

    it("marks a setting at row level, in words, when the recorded values are not all equal, and shows the legend", () => {
        setup([setting("Treatment", [word("A"), word("A"), word("B"), absent("Not recorded")])])
        const header = within(screen.getByRole("row", { name: /Treatment/ })).getByRole("rowheader")
        expect(header).toHaveTextContent("≠ Not the same in every record")
        expect(document.querySelectorAll(".compare-cell--differs")).toHaveLength(0)
        expect(screen.getByText(/a setting that is not the same in every record of a version; not a verdict\./, { selector: ".compare-legend" })).toBeVisible()
    })

    it("does not mark a setting that agrees, nor a split between versions, and gives a 2-vs-2 split one row mark", () => {
        setup([
            setting("Agrees", [word("A"), word("A"), word("A"), absent("Not recorded")]),
            setting("Per version", [word("A"), word("A"), word("B"), word("B")]),
            setting("Split", [word("A"), word("B"), word("A"), word("B")]),
        ], TWO)
        expect(within(screen.getByRole("row", { name: /Agrees/ })).getByRole("rowheader")).not.toHaveTextContent("Not the same")
        expect(within(screen.getByRole("row", { name: /Per version/ })).getByRole("rowheader")).not.toHaveTextContent("Not the same")
        expect(within(screen.getByRole("row", { name: /Split/ })).getByRole("rowheader")).toHaveTextContent("Not the same in every record")
    })

    it("never marks a measured number, however much the values differ, and shows no legend without a setting row", () => {
        setup([measured("Enthalpy", [number("1.00"), number("90.00"), number("2.00"), number("3.00")])])
        expect(screen.queryByText(/not the same/i)).not.toBeInTheDocument()
        expect(document.querySelector(".compare-legend")).toBeNull()
    })

    it("says why a cell is empty, shows exponents and a subscripted point group, and links each number to its record", () => {
        setup([
            measured("Small", [number("1.23", "−3"), absent("Outside its range"), absent("Not recorded"), number("2.00")]),
            setting("Point group", [group("D3h"), group("C2v"), absent("Not shown"), absent("Not recorded")]),
        ])
        const small = screen.getByRole("row", { name: /Small/ })
        expect(within(small).getAllByRole("cell")[1]).toHaveTextContent("1.23 × 10−3")
        expect(small.querySelector("sup")).toHaveTextContent("−3")
        expect(within(small).getByText("Outside its range")).toBeVisible()
        expect(within(small).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["/r/a", "/r/d"])
        const point = screen.getByRole("row", { name: /Point group/ })
        expect(point.querySelectorAll("sub")).toHaveLength(2)
        expect(within(point).getByText("Not shown")).toBeVisible()
    })

    it("sets a heavier border between version groups and keeps the records in the order given", () => {
        setup([measured("Enthalpy", [number("3.00"), number("1.00"), number("2.00"), number("4.00")])], TWO)
        const cells = within(screen.getByRole("row", { name: /Enthalpy/ })).getAllByRole("cell")
        expect(cells.map((cell) => cell.className.includes("compare-group-start"))).toEqual([false, false, false, true, false])
        expect(cells.slice(1).map((cell) => cell.textContent)).toEqual(["3.00", "1.00", "2.00", "4.00"])
        expect(screen.getAllByRole("columnheader", { name: /version (one|two)/ })).toHaveLength(2)
    })

    it("uses no ranking word, and the guard does catch one", () => {
        setup([measured("Enthalpy", [number("3.00"), number("1.00"), number("2.00"), number("4.00")]), setting("Rotor", [word("Symmetric top"), word("Asymmetric top"), word("Spherical top"), word("Symmetric top")])])
        expect(rankingWords(textOf(document.body))).toEqual([])
        const planted = document.createElement("p")
        planted.textContent = "Best"
        document.body.append(planted)
        expect(rankingWords(textOf(document.body))).toEqual(["Best"])
    })
})
