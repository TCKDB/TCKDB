import { describe, expect, it } from "vitest"
import type { EnergyProfileRecord } from "../api/reactionEnergyProfileApi"
import type { ReactionKineticsEntry } from "../api/reactionKineticsApi"
import { programLabel, deltaNames, deltaReason, groupDifference, groupKinetics, kineticsColumns, profilesOfGroup, reactionRows, PER_PRESSURE, PER_TERM, NEEDS_PRESSURE } from "./reactionCompare"

const lotA = { method: "wb97xd", basis: "def2tzvp", level_of_theory_ref: "lot_a" }
const lotB = { method: "ccsdt", basis: "ccpvtz", level_of_theory_ref: "lot_b" }
const gaussian = { software: "Gaussian", version: "16" }
const orca = { software: "Orca", version: "6.0" }

type Over = Record<string, unknown>
function entry(ref: string, over: Over = {}, levels: Over = { energy: lotA }, software: object | null = gaussian): ReactionKineticsEntry {
    return {
        entryRef: "rxe_one",
        entryPosition: 1,
        record: {
            kinetics_ref: ref, scientific_origin: "computed", model_kind: "modified_arrhenius", direction: "forward", review: { status: "not_reviewed" },
            parameters: { A: 1e10, A_units: "cm3_mol_s", n: 1, Ea_kj_mol: 40 }, is_third_body: false, uncertainty: {},
            temperature_coverage: { record_min_k: 300, record_max_k: 2000 },
            evidence_completeness: { score: 0, max: 9, checklist: {} },
            levels, provenance: { primary_software: software },
            ...over,
        },
    } as unknown as ReactionKineticsEntry
}

const groupsOf = (...entries: ReactionKineticsEntry[]) => groupKinetics(kineticsColumns(entries))
const row = (groups: ReturnType<typeof groupsOf>, key: string) => reactionRows(groups).find((candidate) => candidate.key === key)

function profile(level: object, program: string, versions: (string | null)[]): EnergyProfileRecord {
    return { transition_state_entry_ref: "tse_one", level_of_theory: level, program, versions } as unknown as EnergyProfileRecord
}

describe("groups", () => {
    it("separates one level run with two programs, and two versions of one program", () => {
        const groups = groupsOf(entry("a"), entry("b", {}, { energy: lotA }, orca), entry("c", {}, { energy: lotA }, { software: "Gaussian", version: "09" }))
        expect(groups).toHaveLength(3)
        expect(groupDifference(groups)).toBe("programs")
    })

    it("falls back to the declared level and says so", () => {
        const [group] = groupsOf(entry("a", {}, { energy: null, declared_energy: lotB }))
        expect(group.level?.level_of_theory_ref).toBe("lot_b")
        expect(group.levelDeclared).toBe(true)
    })

    it("names what the groups differ in", () => {
        expect(groupDifference(groupsOf(entry("a"), entry("b", {}, { energy: lotB })))).toBe("methods")
        expect(groupDifference(groupsOf(entry("a"), entry("b", {}, { energy: lotB }, orca)))).toBe("both")
        expect(groupDifference(groupsOf(entry("a")))).toBeNull()
    })
})

describe("profile matching", () => {
    const [group] = groupsOf(entry("a"))
    const right = profile(lotA, "Gaussian", ["16"])

    it("keeps only the profile with the group's level, program and version", () => {
        const others = [
            profile(lotB, "Gaussian", ["16"]),
            profile(lotA, "Orca", ["16"]),
            profile(lotA, "Gaussian", ["09"]),
            profile(lotA, "Gaussian", [null]),
        ]
        expect(profilesOfGroup(group, [...others, right])).toEqual([right])
        expect(profilesOfGroup(group, others)).toHaveLength(0)
    })

    it("matches by level ref when both have one, not by label", () => {
        const sameLabelOtherRef = profile({ ...lotA, level_of_theory_ref: "lot_other" }, "Gaussian", ["16"])
        expect(profilesOfGroup(group, [sameLabelOtherRef])).toHaveLength(0)
    })

    it("matches nothing for a group without a level or program", () => {
        const [noLevel] = groupsOf(entry("a", {}, { energy: null }))
        expect(profilesOfGroup(noLevel, [right])).toHaveLength(0)
        const [noProgram] = groupsOf(entry("a", {}, { energy: lotA }, null))
        expect(profilesOfGroup(noProgram, [right])).toHaveLength(0)
    })
})

describe("difference", () => {
    const ea = (groups: ReturnType<typeof groupsOf>) => row(groups, "ea")?.deltas

    it("is formed for a group of exactly two matching records", () => {
        const groups = groupsOf(entry("a"), entry("b", { parameters: { A: 2e10, A_units: "cm3_mol_s", n: 1, Ea_kj_mol: 42.5 } }))
        const cell = ea(groups)?.[0]
        expect(cell?.kind === "value" && cell.value.value).toBe("+2.50")
        expect(deltaNames(groups[0])).toEqual(["b", "a"])
    })

    it("is not formed for one record or for three, and names nobody", () => {
        for (const entries of [[entry("a")], [entry("a"), entry("b"), entry("c")]]) {
            const groups = groupsOf(...entries)
            expect(ea(groups)?.[0]).toBeNull()
            expect(deltaNames(groups[0])).toBeNull()
        }
    })

    it.each([
        ["direction not stated", { direction: null }, /Direction is not stated/],
        ["different directions", { direction: "reverse" }, /different directions/],
        ["different model kinds", { model_kind: "arrhenius" }, /different model kinds/],
        ["different T0", { parameters: { A: 1e10, A_units: "cm3_mol_s", n: 1, Ea_kj_mol: 40, T0_k: 298 } }, /reference temperatures/],
        ["different units of A", { parameters: { A: 1e10, A_units: "m3_mol_s", n: 1, Ea_kj_mol: 40 } }, /different units/],
        ["different pressure contexts", { pressure_context: "high_p_limit" }, /pressure contexts/],
    ])("says why there is none for %s", (_name, over, reason) => {
        const groups = groupsOf(entry("a"), entry("b", over as Over))
        expect(deltaReason(groups[0])).toMatch(reason)
        for (const cell of ea(groups) ?? []) expect(cell?.kind === "absent" && cell.words).toMatch(reason)
    })

    it("is not formed for apparent rates at different pressures, and says so", () => {
        const at = (bar: number) => ({ pressure_context: "apparent_at_pressure", pressure_bar: bar })
        const groups = groupsOf(entry("a", at(1)), entry("b", at(10)))
        expect(deltaReason(groups[0])).toMatch(/different pressure contexts/)
        const cell = ea(groups)?.[0]
        expect(cell?.kind === "absent" && cell.words).toMatch(/pressure contexts/)
        expect(deltaReason(groupsOf(entry("a", at(1)), entry("b", at(1)))[0])).toBeNull()
    })

    it("gives a pair of multi-term records the single-term reason, not a missing-value one", () => {
        const multi = { model_kind: "multi_arrhenius", parameters: {}, multi_arrhenius: [{ entry_index: 0, A: 1e9, n: 0, Ea_kj_mol: 5, A_units: "cm3_mol_s" }] }
        const groups = groupsOf(entry("a", multi), entry("b", multi))
        expect(deltaReason(groups[0])).toBe("Differences are formed only for single-term records")
        const cell = ea(groups)?.[0]
        expect(cell?.kind === "absent" && cell.words).toBe("Differences are formed only for single-term records")
    })

    it("fills every row of a two-record group, never blank", () => {
        const groups = groupsOf(entry("a"), entry("b"))
        for (const candidate of reactionRows(groups)) expect(candidate.deltas?.[0], candidate.key).not.toBeNull()
    })
})

describe("program label", () => {
    it("says energy calculation only when every record cites its energy calculation", () => {
        const cited = { provenance: { primary_software: gaussian, ts_sp_calculation_ref: "calc_1" } }
        expect(programLabel(groupsOf(entry("a", cited), entry("b", cited))[0])).toBe("Program of the energy calculation")
        expect(programLabel(groupsOf(entry("a", cited), entry("b"))[0])).toBe("Program of the source calculation")
        expect(programLabel(groupsOf(entry("a"))[0])).toBe("Program of the source calculation")
    })
})

describe("rows", () => {
    it.each([
        ["already applied", { value: 3, convention: "already_applied" }, undefined],
        ["not applied", { value: 3, convention: "not_applied" }, "k excludes reaction-path degeneracy g = 3"],
        ["unknown", { value: 3, convention: "unknown" }, "whether k includes reaction-path degeneracy g = 3 is not stated"],
        ["absent", null, undefined],
    ])("notes degeneracy in the k cell: %s", (_name, degeneracy, note) => {
        const cell = row(groupsOf(entry("a", { reaction_path_degeneracy: degeneracy }), entry("b")), "k-500")?.cells[0]
        expect(cell?.kind === "value" ? cell.note : "absent").toBe(note)
    })

    it("states direction, pressure context and degeneracy, with Not stated when absent", () => {
        const groups = groupsOf(entry("a", { direction: null, pressure_context: "high_p_limit", reaction_path_degeneracy: { value: 2, convention: "not_applied" } }))
        expect(row(groups, "direction")?.cells[0]).toEqual({ kind: "absent", words: "Not stated" })
        expect(row(groups, "pressure-context")?.cells[0]).toMatchObject({ kind: "value", value: { value: "High-pressure limit" } })
        expect(row(groups, "degeneracy")?.cells[0]).toMatchObject({ kind: "value", value: { value: "2" } })
        expect(row(groups, "degeneracy-in-k")?.cells[0]).toMatchObject({ value: { value: "Not included in the reported k" } })
    })

    it("says the k excludes degeneracy where it is not applied, without changing the number", () => {
        const plain = row(groupsOf(entry("a"), entry("b")), "k-500")?.cells[0]
        const [first, second] = [entry("a", { reaction_path_degeneracy: { value: 3, convention: "not_applied" } }), entry("b")]
        const cell = row(groupsOf(first, second), "k-500")?.cells[0]
        expect(cell).toMatchObject({ kind: "value", note: "k excludes reaction-path degeneracy g = 3" })
        expect(cell?.kind === "value" && cell.value.value).toBe(plain?.kind === "value" ? plain.value.value : "")
    })

    it("words the parameters of multi-term and pressure-dependent records, never Not recorded", () => {
        const multi = entry("m", { model_kind: "multi_arrhenius", parameters: {}, multi_arrhenius: [{ entry_index: 0, A: 1e9, n: 0, Ea_kj_mol: 5, A_units: "cm3_mol_s" }] })
        const plog = entry("p", { model_kind: "plog", parameters: {} })
        const groups = groupsOf(multi, plog)
        for (const key of ["a", "n", "ea"]) {
            expect(row(groups, key)?.cells[0]).toEqual({ kind: "absent", words: PER_TERM })
            expect(row(groups, key)?.cells[1]).toEqual({ kind: "absent", words: PER_PRESSURE })
        }
    })

    it("gives no k for pressure-dependent records, and says it needs a pressure", () => {
        const rows = reactionRows(groupsOf(entry("a"), entry("b"), entry("p", { model_kind: "plog", parameters: {} })))
        expect(rows.find((candidate) => candidate.key === "k-500")?.cells[2]).toEqual({ kind: "absent", words: NEEDS_PRESSURE })
    })

    it("offers a temperature only where two or more records have it inside their range", () => {
        const narrow = { temperature_coverage: { record_min_k: 300, record_max_k: 400 } }
        const one = reactionRows(groupsOf(entry("a"), entry("b", narrow))).map((candidate) => candidate.key)
        expect(one).toContain("k-300")
        expect(one).not.toContain("k-500")
        const two = reactionRows(groupsOf(entry("a"), entry("b", { temperature_coverage: { record_min_k: 450, record_max_k: 600 } }))).map((candidate) => candidate.key)
        expect(two).toContain("k-500")
        expect(two).not.toContain("k-300")
    })
})
