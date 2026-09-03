import { describe, expect, it } from "vitest"
import type { StatmechRecord } from "../api/statmechApi"
import type { ThermoRecord } from "../api/thermoApi"
import { groupByFingerprint, statmechRecordFingerprint, thermoRecordFingerprint } from "./identicalRecordGroups"

describe("groupByFingerprint", () => {
    it("buckets records sharing a fingerprint together, preserving first-seen order both across and within groups", () => {
        const records = ["a1", "b1", "a2", "c1", "a3", "b2"]
        const groups = groupByFingerprint(records, (record) => record[0])
        expect(groups.map((group) => group.records)).toEqual([
            ["a1", "a2", "a3"],
            ["b1", "b2"],
            ["c1"],
        ])
    })

    it("returns a record whose fingerprint no other record shares as its own one-record group -- never a different shape", () => {
        const groups = groupByFingerprint(["only"], (record) => record)
        expect(groups).toEqual([{ key: "only", records: ["only"] }])
    })

    it("never drops a record and never reorders records within a group", () => {
        const records = [{ id: 1 }, { id: 2 }, { id: 3 }]
        const groups = groupByFingerprint(records, () => "same")
        expect(groups).toHaveLength(1)
        expect(groups[0].records.map((r) => r.id)).toEqual([1, 2, 3])
    })
})

/**
 * Minimal, type-satisfying thermo record -- every field this file's tests
 * don't vary is filled with a fixed, arbitrary value so mutating ONE field
 * at a time is what changes the fingerprint, never an incidental gap.
 */
function thermoRecord(overrides: Partial<ThermoRecord> = {}): ThermoRecord {
    return {
        thermo_ref: "thm_x",
        scientific_origin: "computed",
        model_kind: "nasa",
        review: { status: "not_reviewed" },
        h298_kj_mol: 62.84,
        s298_j_mol_k: 218.80,
        h298_uncertainty_kj_mol: null,
        s298_uncertainty_j_mol_k: null,
        nasa: {
            t_low: 100, t_mid: 1000, t_high: 3000,
            low_temperature_coefficients: [1, 2, 3, 4, 5, 6, 7],
            high_temperature_coefficients: [8, 9, 10, 11, 12, 13, 14],
        },
        nasa9: null,
        wilhoit: null,
        points: null,
        temperature_coverage: {
            requested_min_k: null, requested_max_k: null,
            record_min_k: 100, record_max_k: 3000,
            covers_requested_range: true, extrapolation_distance_k: 0,
        },
        provenance: {
            primary_calculation: { calculation_type: "sp", geometry_validation_status: "not_present", scf_stability_status: "not_present" },
            software_release: null,
        },
        ...overrides,
    } as ThermoRecord
}

describe("thermoRecordFingerprint", () => {
    it("gives two records the SAME fingerprint when every scientific field matches, even though ref/review/provenance differ", () => {
        const a = thermoRecord({ thermo_ref: "thm_a", review: { status: "approved" }, provenance: { software_release: { software_release_ref: "srel_1", software: "Arkane" } } })
        const b = thermoRecord({ thermo_ref: "thm_b", review: { status: "not_reviewed" }, provenance: { software_release: null } })
        expect(thermoRecordFingerprint(a)).toBe(thermoRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when scientific_origin differs -- a computed and an experimental record that share a number are not the same record", () => {
        const computed = thermoRecord({ scientific_origin: "computed" })
        const experimental = thermoRecord({ scientific_origin: "experimental" })
        expect(thermoRecordFingerprint(computed)).not.toBe(thermoRecordFingerprint(experimental))
    })

    it("gives two records a DIFFERENT fingerprint when a single NASA-7 coefficient differs", () => {
        const a = thermoRecord()
        const b = thermoRecord({
            nasa: { ...a.nasa!, low_temperature_coefficients: [1, 2, 3, 4, 5, 6, 999] },
        })
        expect(thermoRecordFingerprint(a)).not.toBe(thermoRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when H298 differs", () => {
        const a = thermoRecord({ h298_kj_mol: 62.84 })
        const b = thermoRecord({ h298_kj_mol: 62.85 })
        expect(thermoRecordFingerprint(a)).not.toBe(thermoRecordFingerprint(b))
    })

    it("ignores the REQUEST-scoped temperature-coverage fields -- only the record's own range participates", () => {
        const a = thermoRecord({ temperature_coverage: { requested_min_k: null, requested_max_k: null, record_min_k: 100, record_max_k: 3000, covers_requested_range: true, extrapolation_distance_k: 0 } })
        const b = thermoRecord({ temperature_coverage: { requested_min_k: 200, requested_max_k: 2000, record_min_k: 100, record_max_k: 3000, covers_requested_range: false, extrapolation_distance_k: 50 } })
        expect(thermoRecordFingerprint(a)).toBe(thermoRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when the level of theory differs, even though every other scientific field (H298/S298/NASA-7) matches -- two records reporting the same numbers at different LoTs are not the same record", () => {
        const a = thermoRecord({ provenance: { primary_calculation: null, software_release: null, level_of_theory: { method: "b3lyp", basis: "def2tzvp" } } })
        const b = thermoRecord({ provenance: { primary_calculation: null, software_release: null, level_of_theory: { method: "wb97xd", basis: "def2tzvp" } } })
        expect(thermoRecordFingerprint(a)).not.toBe(thermoRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when the level of theory differs only in dispersion treatment, even with the same display text", () => {
        const a = thermoRecord({ provenance: { primary_calculation: null, software_release: null, level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", dispersion: null } } })
        const b = thermoRecord({ provenance: { primary_calculation: null, software_release: null, level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", dispersion: "d3bj" } } })
        expect(thermoRecordFingerprint(a)).not.toBe(thermoRecordFingerprint(b))
    })

    it("gives two records the SAME fingerprint when the level of theory matches, even though the rest of provenance (calc refs, software) differs", () => {
        const a = thermoRecord({
            provenance: {
                primary_calculation: { calculation_type: "sp", geometry_validation_status: "not_present", scf_stability_status: "not_present", calculation_ref: "calc_a" },
                software_release: { software_release_ref: "srel_1", software: "Arkane" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
            },
        })
        const b = thermoRecord({
            provenance: {
                primary_calculation: { calculation_type: "sp", geometry_validation_status: "not_present", scf_stability_status: "not_present", calculation_ref: "calc_b" },
                software_release: null,
                level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
            },
        })
        expect(thermoRecordFingerprint(a)).toBe(thermoRecordFingerprint(b))
    })
})

/**
 * Minimal, type-satisfying statmech record -- same discipline as
 * `thermoRecord` above.
 */
function statmechRecord(overrides: Partial<StatmechRecord["statmech"]> = {}, recordOverrides: Partial<StatmechRecord> = {}): StatmechRecord {
    return {
        statmech: {
            statmech_ref: "sm_x",
            scientific_origin: "computed",
            statmech_treatment: "rrho",
            rigid_rotor_kind: "asymmetric_top",
            point_group: "D2h",
            external_symmetry: 4,
            is_linear: false,
            uses_projected_frequencies: null,
            optical_isomers: 1,
            rotational_constant_a_cm1: null,
            rotational_constant_b_cm1: null,
            rotational_constant_c_cm1: null,
            frequency_scale_factor_value: 0.999,
            created_at: "2026-07-21T12:14:32.845900",
            review: { status: "not_reviewed" },
            ...overrides,
        },
        evidence_summary: {
            source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
            has_sp_calculation: true, has_rotor_scans: false, torsion_count: 0,
            has_frequency_scale_factor: true, has_conformer_context: true,
        },
        available_sections: {
            has_source_calculations: true, has_torsions: false, has_electronic_levels: false,
            has_frequencies: true, has_conformers: true, has_review: true,
        },
        frequency_scale_factor: {
            frequency_scale_factor_ref: "fsf_1", value: 0.999, scale_kind: "fundamental",
            level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
        },
        software_release: null,
        ...recordOverrides,
    } as StatmechRecord
}

describe("statmechRecordFingerprint", () => {
    it("gives two records the SAME fingerprint when every scientific field matches, even though ref/created_at/record-software differ", () => {
        const a = statmechRecord({ statmech_ref: "sm_a", created_at: "2026-01-01T00:00:00" }, { software_release: { software: "Arkane" } as never })
        const b = statmechRecord({ statmech_ref: "sm_b", created_at: "2026-02-02T00:00:00" }, { software_release: null })
        expect(statmechRecordFingerprint(a)).toBe(statmechRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when point_group differs", () => {
        const a = statmechRecord({ point_group: "D2h" })
        const b = statmechRecord({ point_group: "C2v" })
        expect(statmechRecordFingerprint(a)).not.toBe(statmechRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when external_symmetry differs", () => {
        const a = statmechRecord({ external_symmetry: 4 })
        const b = statmechRecord({ external_symmetry: 2 })
        expect(statmechRecordFingerprint(a)).not.toBe(statmechRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when the scale factor's own value differs, even if the core scalar matches", () => {
        const a = statmechRecord({}, { frequency_scale_factor: { frequency_scale_factor_ref: "fsf_1", value: 0.999, scale_kind: "fundamental", level_of_theory: { method: "b3lyp", basis: "def2tzvp" } } as never })
        const b = statmechRecord({}, { frequency_scale_factor: { frequency_scale_factor_ref: "fsf_2", value: 0.998, scale_kind: "fundamental", level_of_theory: { method: "b3lyp", basis: "def2tzvp" } } as never })
        expect(statmechRecordFingerprint(a)).not.toBe(statmechRecordFingerprint(b))
    })

    it("gives two records a DIFFERENT fingerprint when the scale factor's level of theory differs, even with the same display text", () => {
        const a = statmechRecord({}, { frequency_scale_factor: { frequency_scale_factor_ref: "fsf_1", value: 0.999, scale_kind: "fundamental", level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", dispersion: null } } as never })
        const b = statmechRecord({}, { frequency_scale_factor: { frequency_scale_factor_ref: "fsf_1", value: 0.999, scale_kind: "fundamental", level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", dispersion: "d3bj" } } as never })
        expect(statmechRecordFingerprint(a)).not.toBe(statmechRecordFingerprint(b))
    })

    it("ignores record-level software_release and workflow_tool_release -- provenance never decides identity", () => {
        const a = statmechRecord({}, { software_release: { software_release_ref: "srel_1", software: "Arkane", version: null } as never })
        const b = statmechRecord({}, { software_release: null })
        expect(statmechRecordFingerprint(a)).toBe(statmechRecordFingerprint(b))
    })
})
