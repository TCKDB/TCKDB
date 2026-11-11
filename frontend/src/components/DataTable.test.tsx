import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import { DataTable, type DataColumn } from "./DataTable"

type Row = { ref: string; value: number | null }
const ROWS: Row[] = [{ ref: "thm_b", value: 30 }, { ref: "thm_a", value: null }, { ref: "thm_c", value: -5 }]
const COLUMNS: DataColumn<Row>[] = [
    { key: "ref", name: "record", header: "Record", rowHeader: true, sortValue: (row) => row.ref, cell: (row) => row.ref },
    { key: "value", name: "value", header: "Value", sortValue: (row) => row.value, cell: (row) => row.value ?? "Not recorded" },
    { key: "plain", name: "note", header: "Note", cell: () => "x" },
]

function setup() {
    render(<MemoryRouter><DataTable caption="Records" label="Records table" rows={ROWS} rowKey={(row) => row.ref} columns={COLUMNS} order="Listed in deposit order." /></MemoryRouter>)
}
const order = () => within(screen.getByRole("region", { name: "Records table" })).getAllByRole("rowheader").map((cell) => cell.textContent)

afterEach(cleanup)

describe("DataTable", () => {
    it("is a real table with a caption, column headers and row headers, in the archive's order", () => {
        setup()
        expect(screen.getByRole("table", { name: "Records" })).toBeVisible()
        expect(screen.getAllByRole("columnheader")).toHaveLength(3)
        expect(order()).toEqual(["thm_b", "thm_a", "thm_c"])
        expect(screen.getByRole("status")).toHaveTextContent("Listed in deposit order. This order is not a recommendation.")
    })

    it("sorts from the keyboard: Tab to a sort button, Enter sorts, aria-sort changes and the note says the reader sorted", async () => {
        const user = userEvent.setup()
        setup()
        const header = () => screen.getAllByRole("columnheader")[1]
        expect(header()).toHaveAttribute("aria-sort", "none")
        await user.tab()
        await user.tab()
        await user.tab()
        await user.tab()
        expect(screen.getByRole("button", { name: /^Value sort/ })).toHaveFocus()
        await user.keyboard("{Enter}")
        expect(header()).toHaveAttribute("aria-sort", "ascending")
        expect(order()).toEqual(["thm_c", "thm_b", "thm_a"])
        expect(screen.getByRole("status")).toHaveTextContent("Sorted by you: value, ascending. This is not a recommendation.")
        await user.keyboard("{Enter}")
        expect(header()).toHaveAttribute("aria-sort", "descending")
        expect(order()).toEqual(["thm_b", "thm_c", "thm_a"])
        await user.keyboard("{Enter}")
        expect(header()).toHaveAttribute("aria-sort", "none")
        expect(order()).toEqual(["thm_b", "thm_a", "thm_c"])
        expect(screen.getByRole("status")).toHaveTextContent("Listed in deposit order.")
    })

    it("puts rows with no value last in either direction and offers no sort on a plain column", async () => {
        const user = userEvent.setup()
        setup()
        expect(screen.queryByRole("button", { name: /^Note/ })).not.toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: /^Value sort/ }))
        expect(order().at(-1)).toBe("thm_a")
        await user.click(screen.getByRole("button", { name: /^Value sort/ }))
        expect(order().at(-1)).toBe("thm_a")
    })

    it("restores the archive's order from the note's own button and binds no page-wide keys", async () => {
        const user = userEvent.setup()
        setup()
        await user.click(screen.getByRole("button", { name: /^Record sort/ }))
        expect(order()).toEqual(["thm_a", "thm_b", "thm_c"])
        await user.keyboard("s")
        expect(order()).toEqual(["thm_a", "thm_b", "thm_c"])
        await user.click(screen.getByRole("button", { name: "Back to the archive's order" }))
        expect(order()).toEqual(["thm_b", "thm_a", "thm_c"])
    })

    it("returns focus to the column's sort button after restoring the archive's order, and names the button by its visible words", async () => {
        const user = userEvent.setup()
        setup()
        const button = screen.getByRole("button", { name: "Value sort" })
        await user.click(button)
        await user.click(screen.getByRole("button", { name: "Back to the archive's order" }))
        expect(button).toHaveFocus()
    })

    it("makes the scroll box a labelled, keyboard-focusable region", () => {
        setup()
        expect(screen.getByRole("region", { name: "Records table" })).toHaveAttribute("tabindex", "0")
    })
})
