import { describe, expect, it } from "vitest"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { entryLede } from "./entryLede"
import type { LevelOfTheory } from "../api/scientificSchemas"
import type { ThermoRecord } from "../api/thermoApi"
import { isOneLevelThroughout, levelParts, numberColumns, numbersOrderSentence, pointGroupParts, thermoRow, type NumberValue } from "./entryNumbers"
import { THERMO_MODEL_KIND_WORDS } from "./thermoModelKind"
import { orderWords } from "./paging"

const sort = (...keys: [string, string, string][]) => ({ keys: keys.map(([key, direction, meaning]) => ({ key, direction, meaning })) })
const reviewThenNewest = sort(["review_rank", "asc", "review status"], ["created_at", "desc", "newest deposited"], ["id", "desc", "unique tie-breaker"])

describe("pointGroupParts", () => {
    it("subscripts the whole suffix", () => {
        expect(pointGroupParts("D3h")).toEqual({ symbol: "D", subscript: "3h" })
        expect(pointGroupParts("C2v")).toEqual({ symbol: "C", subscript: "2v" })
        expect(pointGroupParts("Cinfv")).toEqual({ symbol: "C", subscript: "∞v" })
        expect(pointGroupParts("C∞v")).toEqual({ symbol: "C", subscript: "∞v" })
        expect(pointGroupParts("Td")).toEqual({ symbol: "T", subscript: "d" })
        expect(pointGroupParts("D*h")).toEqual({ symbol: "D", subscript: "∞h" })
        expect(pointGroupParts("Dinfh")).toEqual({ symbol: "D", subscript: "∞h" })
    })

    it("replaces the infinity marker only at the start of the subscript", () => {
        // `publicPointGroup` never lets such a string through; the split itself still must not rewrite it.
        expect(pointGroupParts("C2*v")).toEqual({ symbol: "C", subscript: "2*v" })
        expect(pointGroupParts("C2infv")).toEqual({ symbol: "C", subscript: "2infv" })
        expect(pointGroupParts("C**v")).toEqual({ symbol: "C", subscript: "∞*v" })
    })
})

describe("orderWords and numbersOrderSentence", () => {
    it("words the keys without the tie-breaker", () => {
        expect(orderWords(reviewThenNewest)).toBe("approved first, then newest deposited first")
        expect(orderWords(null)).toBeNull()
    })

    it("says identical orders once and names only the lists given", () => {
        expect(numbersOrderSentence([{ kind: "thermo", sort: reviewThenNewest }, { kind: "transport", sort: reviewThenNewest }]))
            .toBe("Thermochemistry, then transport; approved first, then newest deposited first.")
        expect(numbersOrderSentence([{ kind: "statmech", sort: reviewThenNewest }]))
            .toBe("Statistical mechanics: approved first, then newest deposited first.")
    })

    it("spells out lists whose orders differ or are not stated", () => {
        expect(numbersOrderSentence([{ kind: "thermo", sort: reviewThenNewest }, { kind: "statmech", sort: null }]))
            .toBe("Thermochemistry, then statistical mechanics. Thermochemistry: approved first, then newest deposited first. Statistical mechanics: the archive did not state an order.")
    })
})

describe("entryLede", () => {
    const entry = (multiplicity: number, state = "ground") => ({
        charge: -1, multiplicity, electronic_state_kind: state, species_entry_kind: "minimum",
    }) as unknown as SpeciesEntryProjection

    it("words a recorded multiplicity, falling back to the number, never to 'not recorded'", () => {
        expect(entryLede(entry(3))).toContain("Charge −1, triplet, ground state")
        expect(entryLede(entry(9))).toContain("multiplicity 9")
        expect(entryLede(entry(9, "doubly_excited"))).toContain("doubly excited")
        expect(entryLede(entry(9))).not.toContain("not recorded")
    })
})

describe("thermo model kinds", () => {
    it("are keyed on the values the read API sends, not on the database enum", () => {
        // `thermo.py` sends `nasa` for a NASA 7 record and `points` for a tabulated one; `EntryThermoSection` picks its model block by the same values.
        expect(Object.keys(THERMO_MODEL_KIND_WORDS)).toEqual(["nasa", "nasa9", "wilhoit", "points", "scalar"])
        expect(THERMO_MODEL_KIND_WORDS).not.toHaveProperty("nasa7")
        expect(THERMO_MODEL_KIND_WORDS).not.toHaveProperty("tabulated")
    })
})

const lot = (ref: string, method = "b3lyp"): LevelOfTheory => ({ method, basis: "def2tzvp", level_of_theory_ref: ref }) as LevelOfTheory
const stages = (geometry: LevelOfTheory, frequency: LevelOfTheory, energy: LevelOfTheory) => [
    { role: "geometry", label: "b3lyp/def2tzvp", level: geometry },
    { role: "frequency", label: "b3lyp/def2tzvp", level: frequency },
    { role: "energy", label: "b3lyp/def2tzvp", level: energy },
]

describe("isOneLevelThroughout", () => {
    const one = stages(lot("lot_a"), lot("lot_a"), lot("lot_a"))

    it("collapses only when all three stages carry one level ref and the record names one program", () => {
        expect(isOneLevelThroughout({ levels: one, programs: ["Gaussian 16"], levelsFromProvenance: false })).toBe(true)
    })

    it("keeps the roles when the levels share a label but have different refs", () => {
        const split = stages(lot("lot_a"), lot("lot_a"), lot("lot_b"))
        expect(isOneLevelThroughout({ levels: split, programs: ["Gaussian 16"], levelsFromProvenance: false })).toBe(false)
        expect(levelParts(split).map((part) => part.roles)).toEqual([["geometry", "frequency"], ["energy"]])
    })

    it("keeps the roles when one level ref was run by two programs", () => {
        expect(isOneLevelThroughout({ levels: one, programs: ["Gaussian 16", "ORCA 5.0"], levelsFromProvenance: false })).toBe(false)
    })

    it("keeps the roles when a stage has no level ref", () => {
        const noRef = [one[0], one[1], { role: "energy", label: "b3lyp/def2tzvp", level: { method: "b3lyp", basis: "def2tzvp" } as LevelOfTheory }]
        expect(isOneLevelThroughout({ levels: noRef, programs: ["Gaussian 16"], levelsFromProvenance: false })).toBe(false)
        expect(isOneLevelThroughout({ levels: one.slice(0, 2), programs: ["Gaussian 16"], levelsFromProvenance: false })).toBe(false)
    })

    it("never collapses a level copied from provenance into all three stages", () => {
        const record = {
            thermo_ref: "thm_p", scientific_origin: "computed", review: { status: "not_reviewed" }, h298_kj_mol: 1,
            provenance: { level_of_theory: lot("lot_a"), software_release: { software: "Gaussian", version: "16" } },
        } as unknown as ThermoRecord
        const row = thermoRow(record)
        expect(row.levelsFromProvenance).toBe(true)
        expect(row.levels.map((level) => level.role)).toEqual(["geometry", "frequency", "energy"])
        expect(isOneLevelThroughout(row)).toBe(false)
    })
})

describe("numberColumns", () => {
    const value = (label: string, text: string | null, extra: Partial<NumberValue> = {}): NumberValue => ({ label, kind: "number", value: text, unit: "kJ/mol", ...extra })

    it("folds a field that is missing in a single record", () => {
        const columns = numberColumns([{ values: [value("H", "1"), value("S", null)] }])
        expect(columns.map((column) => [column.label, column.allAbsent])).toEqual([["H", false], ["S", true]])
    })

    it("keeps a field recorded in some records as a column", () => {
        const columns = numberColumns([{ values: [value("H", "1"), value("S", null)] }, { values: [value("H", "2"), value("S", "3")] }])
        expect(columns.map((column) => column.allAbsent)).toEqual([false, false])
    })

    it("folds a field no record carries, whether the label is missing or null", () => {
        const columns = numberColumns([{ values: [value("H", "1")] }, { values: [value("H", "2"), value("S", null)] }])
        expect(columns.find((column) => column.label === "S")?.allAbsent).toBe(true)
    })

    it("treats a value of 0 as recorded", () => {
        const columns = numberColumns([{ values: [value("H", "0")] }, { values: [value("H", null)] }])
        expect(columns[0].allAbsent).toBe(false)
    })

    it("does not fold a field kept back with its own wording, such as Not shown", () => {
        const kept = (text: string | null): NumberValue => ({ label: "Point group", kind: "pointGroup", value: text, unit: null, absent: "Not shown" })
        expect(numberColumns([{ values: [kept(null)] }, { values: [kept(null)] }])[0].allAbsent).toBe(false)
    })

    it("puts a unit in the header only when the records agree on one", () => {
        const agree = numberColumns([{ values: [value("H", "1")] }, { values: [value("H", "2")] }])[0]
        expect([agree.unit, agree.unitInCell]).toEqual(["kJ/mol", false])
        const differ = numberColumns([{ values: [value("H", "1")] }, { values: [value("H", "2", { unit: "kcal/mol" })] }])[0]
        expect([differ.unit, differ.unitInCell]).toEqual([null, true])
    })
})
