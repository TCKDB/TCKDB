import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { ThermoRecord } from "../api/thermoApi"
import type { LevelOfTheory } from "../api/scientificSchemas"
import { thermoRow } from "../domain/entryNumbers"
import { NumbersTable } from "./NumbersTable"

afterEach(cleanup)

function row(pressure: number) {
    return thermoRow({
        thermo_ref: "thm_a", scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        h298_kj_mol: 10, s298_j_mol_k: 200, enthalpy_reference_kind: "formation_298k", phase: "gas", reference_pressure_bar: pressure, points: [],
    } as unknown as ThermoRecord)
}

function setup(pressure: number) {
    render(
        <MemoryRouter>
            <NumbersTable rows={[row(pressure)]} caption="Values" label="Values table" groupLabels={new Map()} hrefs={() => ({ to: null, entryPath: null })} />
        </MemoryRouter>,
    )
}

describe("NumbersTable source lines", () => {
    function named(...refs: string[]) {
        return refs.map((ref) => ({ ...row(1), ref }))
    }

    it("names same-kind records by their number so their source links read differently", () => {
        render(
            <MemoryRouter>
                <NumbersTable rows={named("thm_a", "thm_b", "thm_c")} caption="Values" label="Values table" groupLabels={new Map()} hrefs={() => ({ to: null, entryPath: null })} />
            </MemoryRouter>,
        )
        expect([...document.querySelectorAll("tbody th[scope=row] .entry-numbers-record")].map((cell) => cell.textContent)).toEqual(["Thermo record 1", "Thermo record 2", "Thermo record 3"])
    })

    it("leaves the only record of its kind unnumbered", () => {
        setup(1)
        expect(document.querySelector("tbody th[scope=row] .entry-numbers-record")?.textContent).toBe("Thermo record")
    })
})

describe("NumbersTable rounding", () => {
    it("shows a reference pressure to 6 significant figures with the stored value as title and one note", () => {
        setup(1.0132500001)
        const shown = screen.getByText("1.01325")
        expect(shown).toHaveAttribute("title", "1.0132500001")
        expect(shown.tagName).toBe("DATA")
        expect(shown).toHaveAttribute("value", "1.0132500001")
        expect(screen.getAllByText("6 significant figures; hover for the stored value.")).toHaveLength(1)
    })

    it("has neither title nor note for a pressure that needs no rounding", () => {
        setup(1.01325)
        expect(screen.getByText("1.01325")).not.toHaveAttribute("title")
        expect(screen.queryByText(/6 significant figures/)).not.toBeInTheDocument()
    })
})

describe("NumbersTable levels and fold line", () => {
    const lot = (ref: string): LevelOfTheory => ({ method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: ref }) as LevelOfTheory
    const record = (extra: Record<string, unknown>) => thermoRow({
        thermo_ref: "thm_x", scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        h298_kj_mol: 10, s298_j_mol_k: null, enthalpy_reference_kind: "formation_298k", phase: "gas", points: [],
        ...extra,
    } as unknown as ThermoRecord)
    const show = (rows: ReturnType<typeof record>[], compact = false) => render(
        <MemoryRouter>
            <NumbersTable rows={rows} caption="Values" label="Values table" groupLabels={new Map()} hrefs={() => ({ to: null, entryPath: null })} compact={compact} />
        </MemoryRouter>,
    )
    const recorded = { levels: { geometry: lot("lot_a"), frequency: lot("lot_a"), energy: lot("lot_a"), energy_source: "opt" }, provenance: { software_release: { software: "Gaussian", version: "16" } } }

    it("prints a level without roles, once, when one ref and one program cover all three stages", () => {
        show([record(recorded)])
        expect(document.querySelector(".entry-numbers-source")).not.toHaveTextContent("(geometry")
        expect(screen.getByText("A level with no roles listed was used for geometry, frequency and energy.")).toBeVisible()
    })

    it("keeps the roles when the levels were copied from provenance, not recorded per stage", () => {
        show([record({ provenance: { level_of_theory: lot("lot_a"), software_release: { software: "Gaussian", version: "16" } } })])
        expect(document.querySelector(".entry-numbers-source")).toHaveTextContent("(geometry, frequency, energy)")
        expect(screen.queryByText(/A level with no roles listed/)).not.toBeInTheDocument()
    })

    it("keeps the roles when the same level ref ran under two programs", () => {
        show([record({ ...recorded, provenance: { software_release: { software: "Gaussian", version: "16" }, workflow_tool_release: { workflow_tool: "ARC", version: "1" } } })])
        expect(document.querySelector(".entry-numbers-source")).toHaveTextContent("(geometry, frequency, energy)")
    })

    it("says Not recorded in this record for one record, and in any of these records for several", () => {
        show([record(recorded)])
        expect(screen.getByText(/^Not recorded in this record: .*entropy, 298 K/)).toBeVisible()
        cleanup()
        show([record(recorded), { ...record(recorded), ref: "thm_y" }])
        expect(screen.getByText(/^Not recorded in any of these records: .*entropy, 298 K/)).toBeVisible()
    })

    it("scrolls each table inside its own focusable box, with the kind heading outside it", () => {
        show([record(recorded)])
        const box = screen.getByRole("region", { name: "Values table, thermochemistry" })
        expect(box).toHaveClass("entry-numbers-table-box")
        expect(box.querySelector("table")).not.toBeNull()
        expect(box.querySelector("h3, h4")).toBeNull()
        expect(screen.getByRole("heading", { level: 3, name: /Thermochemistry/ })).toBeVisible()
    })

    it("heads each kind one level below the section in the compact form", () => {
        show([record(recorded)], true)
        expect(screen.getByRole("heading", { level: 4, name: /Thermochemistry/ })).toBeVisible()
    })
})
