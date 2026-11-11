import { describe, expect, it } from "vitest"
import type { ThermoRecord } from "../api/thermoApi"
import { cpRows, differingCells, thermoValueRows, valueRows, type CompareColumn } from "./speciesCompare"
import { thermoRow } from "./entryNumbers"

function record(ref: string, points: [number, number | null][], h298: number | null = 10, range?: [number, number]): ThermoRecord {
    return {
        thermo_ref: ref, scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        h298_kj_mol: h298, s298_j_mol_k: 200, enthalpy_reference_kind: "formation_298k",
        points: points.map(([temperature_k, cp_j_mol_k]) => ({ temperature_k, cp_j_mol_k })),
        temperature_coverage: range ? { record_min_k: range[0], record_max_k: range[1], covers_requested_range: true, extrapolation_distance_k: 0 } : null,
    } as unknown as ThermoRecord
}

function column(thermo: ThermoRecord): CompareColumn {
    const row = thermoRow(thermo)
    return { key: row.ref, kind: "thermo", ref: row.ref, entryRef: null, row, thermo }
}

const words = (row: { cells: ({ kind: "value"; value: { value: string | null } } | { kind: "absent"; words: string })[] }) =>
    row.cells.map((cell) => (cell.kind === "value" ? cell.value.value : cell.words))

describe("compare rows", () => {
    it("lists enthalpy and entropy rows, a record without a value saying Not recorded", () => {
        const rows = thermoValueRows([column(record("a", [])), column(record("b", [], null))])
        expect(rows.map((row) => row.label)).toEqual(["Enthalpy of formation, 298 K", "Entropy, 298 K"])
        expect(rows[0].cells[1]).toEqual({ kind: "absent", words: "Not recorded" })
    })

    it("shows heat capacity only where two records have a point, and never borrows or interpolates", () => {
        const rows = cpRows([
            column(record("a", [[300, 30], [400, 35], [500, 40]], 1, [300, 500])),
            column(record("b", [[300, 31], [500, 41]], 1, [300, 500])),
            column(record("c", [[300, 32], [350, 33]], 1, [300, 400])),
            column(record("d", [[200, 28]], 1)),
        ])
        expect(rows.map((row) => row.label)).toEqual(["Heat capacity at 300 K", "Heat capacity at 500 K"])
        expect(words(rows[0])).toEqual(["30.00", "31.00", "32.00", "Outside its range"])
        expect(words(rows[1])).toEqual(["40.00", "41.00", "Outside its range", "Outside its range"])
    })

    it("says Not recorded for a temperature inside a record's range that it has no point at", () => {
        const rows = cpRows([
            column(record("a", [[300, 30], [400, 35], [500, 40]])),
            column(record("b", [[300, 31], [500, 41]])),
            column(record("c", [[300, 32], [400, null], [500, 42]])),
            column(record("d", [[300, 33], [400, 36], [500, 43]])),
        ])
        const at400 = rows.find((row) => row.label === "Heat capacity at 400 K")
        expect(at400 && words(at400)).toEqual(["35.00", "Not recorded", "Not recorded", "36.00"])
    })

    it("drops rows nobody records and marks differing cells only", () => {
        expect(valueRows([column(record("a", []))], ["Phase"]).map((row) => row.label)).not.toContain("Phase")
        const [row] = thermoValueRows([column(record("a", [], 1)), column(record("b", [], 1)), column(record("c", [], 2))])
        expect(differingCells(row)).toEqual([false, false, true])
        expect(differingCells({ ...row, cells: [row.cells[0], row.cells[0]] })).toEqual([false, false])
    })
})
