import { describe, expect, it } from "vitest"
import type { ThermoRecord } from "../api/thermoApi"
import { cpRows, groupByVersion, rowDiffers, sortNumber, thermoValueRows, valueRows, type CompareCell, type CompareColumn } from "./speciesCompare"
import { statmechRow, thermoRow } from "./entryNumbers"

function record(ref: string, points: [number, number | null][], h298: number | null = 10, range?: [number, number], extra: Record<string, unknown> = {}): ThermoRecord {
    return {
        thermo_ref: ref, scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        h298_kj_mol: h298, s298_j_mol_k: 200, enthalpy_reference_kind: "formation_298k", phase: "gas", reference_pressure_bar: 1,
        points: points.map(([temperature_k, cp_j_mol_k]) => ({ temperature_k, cp_j_mol_k })),
        temperature_coverage: range ? { record_min_k: range[0], record_max_k: range[1], covers_requested_range: true, extrapolation_distance_k: 0 } : null,
        ...extra,
    } as unknown as ThermoRecord
}

function column(thermo: ThermoRecord, entryRef: string | null = null): CompareColumn {
    const row = thermoRow(thermo)
    return { key: row.ref, kind: "thermo", ref: row.ref, entryRef, row, thermo }
}

const words = (row: { cells: CompareCell[] }) => row.cells.map((cell) => (cell.kind === "value" ? cell.value.value : cell.words))
const text = (value: string): CompareCell => ({ kind: "value", value: { label: "x", kind: "text", value, unit: null } })
const exact = (cells: CompareCell[]) => ({ key: "k", label: "k", unit: null, compare: "exact" as const, cells })

describe("compare rows", () => {
    it("lists enthalpy, entropy, phase and pressure rows, a record without a value saying Not recorded", () => {
        const rows = thermoValueRows([column(record("a", [])), column(record("b", [], null))])
        expect(rows.map((row) => row.label)).toEqual(["Enthalpy of formation, 298 K", "Entropy, 298 K", "Phase", "Reference pressure"])
        expect(rows[0].cells[1]).toEqual({ kind: "absent", words: "Not recorded" })
        expect(rows.map((row) => row.compare)).toEqual(["continuous", "continuous", "exact", "exact"])
    })

    it("adds the temperature range covered as a measured row, never a setting", () => {
        const [range] = thermoValueRows([column(record("a", [], 1, [300, 1000])), column(record("b", [], 1, [300, 500]))]).filter((row) => row.label === "Temperature range covered")
        expect(range.compare).toBe("continuous")
        expect(words(range)).toEqual(["300–1000", "300–500"])
    })

    it("shows heat capacity only where two records have a point, and never borrows or interpolates", () => {
        const rows = cpRows([
            column(record("a", [[300, 30], [400, 35], [500, 40]], 1, [300, 500])),
            column(record("b", [[300, 31], [500, 41]], 1, [300, 500])),
            column(record("c", [[300, 32], [350, 33]], 1, [300, 400])),
            column(record("d", [[200, 28]], 1)),
        ])
        expect(rows.map((row) => row.label)).toEqual(["Heat capacity at 300 K", "Heat capacity at 500 K"])
        expect(words(rows[0])).toEqual(["30", "31", "32", "Outside its range"])
        expect(words(rows[1])).toEqual(["40", "41", "Outside its range", "Outside its range"])
        expect(rows.every((row) => row.compare === "continuous")).toBe(true)
    })

    it("says Not recorded for a temperature inside a record's range that it has no point at", () => {
        const rows = cpRows([
            column(record("a", [[300, 30], [400, 35], [500, 40]])),
            column(record("b", [[300, 31], [500, 41]])),
            column(record("c", [[300, 32], [400, null], [500, 42]])),
            column(record("d", [[300, 33], [400, 36], [500, 43]])),
        ])
        expect(words(rows.find((row) => row.label === "Heat capacity at 400 K") as never)).toEqual(["35", "Not recorded", "Not recorded", "36"])
    })

    it("ignores a point outside the record's stated range, for the cell and for the two-record threshold", () => {
        const rows = cpRows([
            column(record("a", [[300, 30], [2000, 70]], 1, [300, 1000])),
            column(record("b", [[300, 31], [2000, 71]], 1, [300, 1000])),
        ])
        expect(rows.map((row) => row.label)).toEqual(["Heat capacity at 300 K"])
        expect(cpRows([column(record("a", [[300, 30], [2000, 70]], 1, [300, 1000])), column(record("b", [[300, 31]], 1, [300, 1000])), column(record("c", [[2000, 71]], 1, [300, 3000]))]).map((row) => row.label))
            .toEqual(["Heat capacity at 300 K"])
    })

    it("drops rows nobody records", () => {
        expect(valueRows([column(record("a", []))], ["Treatment"]).map((row) => row.label)).not.toContain("Treatment")
    })

    it("sets statmech settings as exact rows and rotational constants as measured ones", () => {
        const row = statmechRow({
            statmech: { statmech_ref: "stm", statmech_treatment: "rrho", point_group: "D3h", external_symmetry: 6, rotational_constant_a_cm1: 9.5, review: { status: "approved" } },
        } as never)
        const rows = valueRows([{ key: "s", kind: "statmech", ref: "stm", entryRef: null, row, thermo: null }])
        expect(Object.fromEntries(rows.map((r) => [r.label, r.compare]))).toMatchObject({ Treatment: "exact", "Point group": "exact", "External symmetry number": "exact", "Rotational constant A": "continuous" })
    })
})

describe("rowDiffers", () => {
    it("marks a setting that is not the same in every record, including a 2-vs-2 split", () => {
        expect(rowDiffers(exact([text("A"), text("A"), text("B"), text("B")]), [4])).toBe(true)
        expect(rowDiffers(exact([text("A"), text("A"), text("A"), { kind: "absent", words: "Not recorded" }]), [4])).toBe(false)
        expect(rowDiffers(exact([text("A"), { kind: "absent", words: "Not recorded" }]), [2])).toBe(false)
    })

    it("compares inside each version group only", () => {
        expect(rowDiffers(exact([text("A"), text("A"), text("B"), text("B")]), [2, 2])).toBe(false)
        expect(rowDiffers(exact([text("A"), text("B"), text("B"), text("B")]), [2, 2])).toBe(true)
    })

    it("never marks a measured row", () => {
        expect(rowDiffers({ ...exact([text("1"), text("2")]), compare: "continuous" }, [2])).toBe(false)
    })
})

describe("groupByVersion", () => {
    it("orders groups by the molecule's versions, unknown versions last, keeping archive order inside a group", () => {
        const columns = [column(record("a", []), "e2"), column(record("b", []), "e1"), column(record("c", []), null), column(record("d", []), "e2"), column(record("e", []), "e9")]
        const groups = groupByVersion(columns, ["e1", "e2"])
        expect(groups.map((group) => [group.key, group.columns.map((c) => c.ref)])).toEqual([["e1", ["b"]], ["e2", ["a", "d"]], ["none", ["c"]], ["e9", ["e"]]])
    })
})

describe("sortNumber", () => {
    it("reads the exponent, the minus signs and ignores an uncertainty", () => {
        const number = (value: string, exponent?: string): CompareCell => ({ kind: "value", value: { label: "x", kind: "number", value, unit: null, exponent } })
        expect(sortNumber(number("1.23", "−3"))).toBeCloseTo(0.00123)
        expect(sortNumber(number("−12.5 ± 0.5"))).toBe(-12.5)
        expect(sortNumber(number("4.5", "5"))).toBe(450000)
        expect(sortNumber({ kind: "absent", words: "Not recorded" })).toBeNull()
        expect(sortNumber(text("A"))).toBeNull()
    })
})
