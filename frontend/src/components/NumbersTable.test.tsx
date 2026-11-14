import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { ThermoRecord } from "../api/thermoApi"
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
        expect([...document.querySelectorAll(".value-source")].map((source) => source.textContent?.match(/Thermo record( \d)?/)?.[0])).toEqual(["Thermo record", "Thermo record", "Thermo record"].map((text, index) => `${text} ${index + 1}`))
    })

    it("leaves the only record of its kind unnumbered", () => {
        setup(1)
        expect(document.querySelector(".value-source")?.textContent).toMatch(/^Source Thermo record(?! \d)/)
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
