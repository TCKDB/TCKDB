import type { StatmechRecord } from "../api/statmechApi"
import type { ThermoRecord } from "../api/thermoApi"

/**
 * Groups thermo/statmech records that report the SAME scientific content
 * under one card, without ever merging, averaging, or discarding a record
 * -- the archive's own "never reduced to one preferred value" rule stays
 * true of every record here; this only changes how many times identical
 * content is PRINTED. The review that asked for this named the live case
 * directly: seven ethene statmech records and seven ethene thermo records,
 * each byte-identical to its siblings in every scientific field, rendered
 * as fourteen full ~500-1500px cards -- 12,000px of scrolling to learn one
 * fact seven times over.
 *
 * `fingerprint` decides identity. It is deliberately narrow -- SCIENTIFIC
 * VALUES only (coefficients, H298/S298, point group, symmetry, the scale
 * factor and the level of theory it was derived at), never a record's own
 * ref, deposit date, review status, or PROVENANCE (which software/workflow
 * tool produced it). Two records can report the identical NASA-7
 * polynomial while one was computed with Arkane and the other's software
 * was never recorded -- that provenance difference is real and must stay
 * visible, listed per ref inside the group, never collapsed away by
 * grouping on it. See `thermoRecordFingerprint`/`statmechRecordFingerprint`
 * below for the exact field lists.
 */
export type IdenticalRecordGroup<T> = {
    /** Stable only within one `groupByFingerprint` call -- never persisted, never used as a React key across renders of a different input. */
    key: string
    /** First-seen order preserved, both between groups and within a group -- never re-sorted by value, ref, or date. */
    records: T[]
}

/**
 * Buckets `records` by `fingerprint(record)`. A record whose fingerprint no
 * other record shares comes back as its own one-record group -- callers
 * never special-case "no duplicates here" as a different return shape, they
 * just render a one-record group as a plain card and a multi-record group
 * as the "N records with identical values" card. Never drops a record,
 * never reorders across groups or within one (`Array.prototype.push`
 * preserves encounter order, and group order is first-seen order of each
 * new key).
 */
export function groupByFingerprint<T>(records: T[], fingerprint: (record: T) => string): IdenticalRecordGroup<T>[] {
    const order: string[] = []
    const byKey = new Map<string, T[]>()
    for (const record of records) {
        const key = fingerprint(record)
        const bucket = byKey.get(key)
        if (bucket) bucket.push(record)
        else { byKey.set(key, [record]); order.push(key) }
    }
    return order.map((key) => ({ key, records: byKey.get(key) as T[] }))
}

/**
 * Thermo identity fingerprint. Compares exactly:
 * `scientific_origin` (a computed value and an experimental one that
 * happen to share a number are NOT the same record), `model_kind`,
 * `h298_kj_mol` + its uncertainty, `s298_j_mol_k` + its uncertainty, the
 * full body of whichever model block the record actually carries
 * (`nasa`/`nasa9`/`wilhoit`/`points`, every field of it -- not just the
 * headline coefficients), and `temperature_coverage`'s two RECORD facts
 * (`record_min_k`/`record_max_k` -- never the request-scoped fields
 * alongside them, which describe the query, not the record, and are
 * identical across every record in one response regardless of content
 * anyway).
 *
 * Deliberately EXCLUDED: `thermo_ref`, `review`, `supersession`,
 * `evidence_completeness` (a diagnostic about the record, not a scientific
 * value of it), `group_additivity` (an estimation scheme's own provenance),
 * and all of `provenance` (level of theory, software, workflow tool,
 * calculation/statmech/conformer refs) -- provenance that differs across
 * otherwise-identical records is real and must stay visible per ref, never
 * used to split them into different groups or hidden by merging them into
 * one.
 */
export function thermoRecordFingerprint(record: ThermoRecord): string {
    return JSON.stringify({
        scientific_origin: record.scientific_origin,
        model_kind: record.model_kind,
        h298_kj_mol: record.h298_kj_mol ?? null,
        h298_uncertainty_kj_mol: record.h298_uncertainty_kj_mol ?? null,
        s298_j_mol_k: record.s298_j_mol_k ?? null,
        s298_uncertainty_j_mol_k: record.s298_uncertainty_j_mol_k ?? null,
        nasa: record.nasa ?? null,
        nasa9: record.nasa9 ?? null,
        wilhoit: record.wilhoit ?? null,
        points: record.points ?? null,
        record_min_k: record.temperature_coverage?.record_min_k ?? null,
        record_max_k: record.temperature_coverage?.record_max_k ?? null,
    })
}

/**
 * Statmech identity fingerprint. Compares exactly: `scientific_origin`,
 * `statmech_treatment`, `rigid_rotor_kind`, `point_group`,
 * `external_symmetry`, `is_linear`, `uses_projected_frequencies`,
 * `optical_isomers`, the three rotational constants, the core
 * `frequency_scale_factor_value` scalar, and -- from the scale factor's OWN
 * provenance block, because it describes the SCALE FACTOR's science, not
 * the record's -- its `value`, `scale_kind`, and level of theory.
 *
 * Deliberately EXCLUDED: `statmech_ref`, `created_at`, `note`, `review`,
 * `supersession`, `evidence_summary`/`available_sections` (diagnostics
 * about what evidence is loaded, not scientific values), every lazy
 * include-gated field (`source_calculations`/`torsions`/
 * `electronic_levels`/`frequencies`/`conformers`/`review_history` --
 * comparing on these would make grouping depend on which disclosures a
 * reader happened to open), and `record.software_release` /
 * `record.workflow_tool_release` / the scale factor's own `.software` --
 * the review's own worked example is exactly this: six records say
 * "Record software: not recorded" and one says "Arkane" while sharing
 * every other value byte-for-byte, and that difference must stay visible
 * per ref inside the group, never used to split or hide it.
 *
 * The scale factor's level of theory compares by its full method/basis/
 * dispersion shape, not just its ref or display string -- two LoT rows can
 * render the identical display text while differing only in dispersion
 * treatment (see `scientificSchemas.ts`'s `levelOfTheorySchema`), and a
 * fingerprint keyed on display text alone would silently merge those as
 * "the same LoT".
 */
export function statmechRecordFingerprint(record: StatmechRecord): string {
    const core = record.statmech
    const fsf = record.frequency_scale_factor
    const lot = fsf?.level_of_theory
    return JSON.stringify({
        scientific_origin: core.scientific_origin,
        statmech_treatment: core.statmech_treatment ?? null,
        rigid_rotor_kind: core.rigid_rotor_kind ?? null,
        point_group: core.point_group ?? null,
        external_symmetry: core.external_symmetry ?? null,
        is_linear: core.is_linear ?? null,
        uses_projected_frequencies: core.uses_projected_frequencies ?? null,
        optical_isomers: core.optical_isomers ?? null,
        rotational_constant_a_cm1: core.rotational_constant_a_cm1 ?? null,
        rotational_constant_b_cm1: core.rotational_constant_b_cm1 ?? null,
        rotational_constant_c_cm1: core.rotational_constant_c_cm1 ?? null,
        frequency_scale_factor_value: core.frequency_scale_factor_value ?? null,
        scale_factor: fsf ? { value: fsf.value, scale_kind: fsf.scale_kind } : null,
        scale_factor_level_of_theory: lot ? { method: lot.method, basis: lot.basis ?? null, dispersion: lot.dispersion ?? null } : null,
    })
}
