import type { ConformerProjection } from "../api/speciesEntryApi"
import type { RelativeEnergies, RelativeEnergyMember } from "../api/relativeEnergyApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { lotLabel, type LevelOfTheory } from "../api/scientificSchemas"
import { KJ_MOL_PER_HARTREE } from "./energyUnits"
import { softwareLabel } from "./provenanceFormat"
import { calculationTypeCounts, conformerDisplayNumber } from "./conformerEvidence"

type ObservationCalculation = NonNullable<NonNullable<NonNullable<ConformerProjection["observations"]>[number]["calculations"]>[number]>

const COUNT_WORDS: Record<string, [string, string]> = {
    opt: ["geometry optimisation", "geometry optimisations"],
    freq: ["frequency", "frequencies"],
    sp: ["single-point energy", "single-point energies"],
    scan: ["scan", "scans"],
    irc: ["IRC", "IRCs"],
}

const countWord = (type: string, count: number): string => {
    const words = COUNT_WORDS[type] ?? [type.replaceAll("_", " "), `${type.replaceAll("_", " ")} calculations`]
    return `${count} ${count === 1 ? words[0] : words[1]}`
}

/** "Found by 4 calculations: 2 geometry optimisations, 1 frequency, 1 single-point energy." from the group's own counts. */
export function shapeCountsSentence(conformer: ConformerProjection): string {
    const total = conformer.evidence_summary.calculation_count
    if (total === 0) return "No calculations are recorded for this shape."
    const parts = calculationTypeCounts(conformer).map(({ type, count }) => countWord(type, count))
    const head = `Found by ${total} ${total === 1 ? "calculation" : "calculations"}`
    return parts.length > 0 ? `${head}: ${parts.join(", ")}.` : `${head}.`
}

/** The group's label when a depositor wrote it (anything but the archive's own `conformer_N` numbering); shown only as depositor text. */
export function depositorWrittenLabel(conformer: ConformerProjection): string | null {
    const label = conformer.conformer_group.label
    return label !== null && label.trim() !== "" && conformerDisplayNumber(conformer) === null ? label : null
}

/** The geometry the picture is drawn from: the first final geometry in the group's own order, else the first listed. */
export function pictureGeometryRef(conformer: ConformerProjection): string | null {
    const links = conformer.geometries ?? []
    const final = links.find((link) => link.geometry.role === "final")
    return (final ?? links[0])?.geometry.geometry_ref ?? null
}

export type LevelChoice = { ref: string; label: string; programs: string[] }

/** Levels of theory the page's shapes carry energies at, in the order first met; each with the programs seen at it. */
export function levelChoices(
    conformers: readonly ConformerProjection[],
    energyRecords: readonly SpeciesCalculationEnergyRecord[],
): LevelChoice[] {
    const byRef = new Map<string, { label: string; programs: Set<string> }>()
    const add = (level: LevelOfTheory | null | undefined, program: string | null) => {
        if (!level?.level_of_theory_ref) return
        const entry = byRef.get(level.level_of_theory_ref) ?? { label: lotLabel(level), programs: new Set<string>() }
        if (program) entry.programs.add(program)
        byRef.set(level.level_of_theory_ref, entry)
    }
    for (const conformer of conformers) {
        for (const observation of conformer.observations ?? []) {
            for (const calculation of observation.calculations ?? []) {
                if (calculation.type === "sp" || calculation.type === "opt") add(calculation.level_of_theory, softwareLabel(calculation.software_release))
            }
        }
    }
    for (const record of energyRecords) add(record.level_of_theory, softwareLabel(record.software_release))
    return [...byRef].map(([ref, { label, programs }]) => ({ ref, label, programs: [...programs] }))
}

export type CardEnergy =
    | { kind: "pending" }
    | { kind: "unread" }
    | { kind: "reference" }
    | { kind: "value"; kjMol: number; calculationRefs: string[] }
    | { kind: "absent"; reason: string | null }
    | { kind: "disagree"; valuesHartree: number[] }

const reasonWords = (code: string | null | undefined): string | null => (code ? code.replaceAll("_", " ") : null)

/** One card's energy in the answer the archive gave: a value, nothing, or records that disagree. Never a guess. */
export function cardEnergy(energies: RelativeEnergies, groupRef: string): CardEnergy {
    const member: RelativeEnergyMember | undefined = energies.members.find((candidate) => candidate.conformer_ref === groupRef)
    if (!member) return { kind: "absent", reason: null }
    if (member.status === "reference") return { kind: "reference" }
    if (member.status === "unresolved") {
        return { kind: "disagree", valuesHartree: member.alternates.map((alternate) => alternate.value_hartree) }
    }
    if (member.status === "present" && member.relative_energy_kj_mol != null) {
        return { kind: "value", kjMol: member.relative_energy_kj_mol, calculationRefs: member.energy_calculation_refs }
    }
    return { kind: "absent", reason: reasonWords(member.absent_reason) }
}

/** The sign is always written, so a value below the reference reads as such. */
export function formatRelativeKj(kjMol: number): string {
    const rounded = Math.abs(kjMol) < 0.005 ? 0 : kjMol
    return `${rounded > 0 ? "+" : rounded < 0 ? "−" : ""}${Math.abs(rounded).toFixed(2)}`
}

/** Sentence for an answer that is not a list of differences. Null when the usual cards say it all. */
export function outcomeNote(energies: RelativeEnergies): string | null {
    switch (energies.outcome) {
        case "reference_unavailable":
            return "The reference shape has no energy at this level, so no differences can be shown. Choose another reference shape."
        case "multiple_recipes_at_level":
            return "More than one recipe was used at this level. The archive does not choose between them, so no differences are shown."
        case "no_members":
            return "No shapes were compared."
        default:
            return null
    }
}

export type ShapeCalculation = {
    ref: string
    type: string
    levelRef: string | null
    levelLabel: string | null
    program: string | null
}

export type ShapeEnergy = ShapeCalculation & { hartree: number | null; review: string | null }

export type ShapeRow = {
    observationRef: string
    /** Optimisation, frequency and other calculations that found this geometry, in deposit order. */
    foundBy: ShapeCalculation[]
    /** Every single-point energy computed on this geometry. */
    energies: ShapeEnergy[]
}

const describeCalculation = (calculation: ObservationCalculation): ShapeCalculation => {
    const level = calculation.level_of_theory
    const release = calculation.software_release
    return {
        ref: calculation.calculation_ref,
        type: calculation.type,
        levelRef: level?.level_of_theory_ref ?? null,
        levelLabel: level ? lotLabel(level) : null,
        program: softwareLabel(release),
    }
}

/**
 * One row per observation of the shape. Energies are joined by calculation ref from the species-calculations read; a
 * record that names this observation but is missing from the group's own list is added. Nothing is picked or merged.
 */
export function shapeRows(conformer: ConformerProjection, energyRecords: readonly SpeciesCalculationEnergyRecord[]): ShapeRow[] {
    const byCalculation = new Map(energyRecords.map((record) => [record.calculation.calculation_ref, record]))
    return (conformer.observations ?? []).map((observation) => {
        const observationRef = observation.conformer_observation.conformer_observation_ref
        const calculations = observation.calculations ?? []
        const sp = new Map<string, ShapeEnergy>()
        for (const calculation of calculations.filter((candidate) => candidate.type === "sp")) {
            const record = byCalculation.get(calculation.calculation_ref)
            const own = describeCalculation(calculation)
            const fromRecord = record ? recordCalculation(record) : null
            sp.set(calculation.calculation_ref, {
                ...own,
                levelRef: own.levelRef ?? fromRecord?.levelRef ?? null,
                levelLabel: own.levelLabel ?? fromRecord?.levelLabel ?? null,
                program: own.program ?? fromRecord?.program ?? null,
                hartree: record?.energy?.energy_hartree ?? null,
                review: reviewOf(record),
            })
        }
        for (const record of energyRecords) {
            if (record.conformer?.conformer_observation_ref !== observationRef || sp.has(record.calculation.calculation_ref)) continue
            sp.set(record.calculation.calculation_ref, {
                ...recordCalculation(record),
                hartree: record.energy?.energy_hartree ?? null,
                review: reviewOf(record),
            })
        }
        return {
            observationRef,
            foundBy: calculations.filter((calculation) => calculation.type !== "sp").map(describeCalculation),
            energies: [...sp.values()],
        }
    })
}

const recordCalculation = (record: SpeciesCalculationEnergyRecord): ShapeCalculation => ({
    ref: record.calculation.calculation_ref,
    type: record.calculation.calculation_type,
    levelRef: record.level_of_theory?.level_of_theory_ref ?? null,
    levelLabel: record.level_of_theory ? lotLabel(record.level_of_theory) : null,
    program: softwareLabel(record.software_release),
})

const reviewOf = (record: SpeciesCalculationEnergyRecord | undefined): string | null => {
    const review = record?.calculation.review as { status?: unknown } | undefined
    return typeof review?.status === "string" ? review.status : null
}

export type SpreadLine = { levelLabel: string; program: string; count: number; kjMol: number }

/** For each level and program with two or more energies in the rows: the largest difference between them. Other pairs are never compared. */
export function spreadLines(rows: readonly ShapeRow[]): SpreadLine[] {
    const groups = new Map<string, { levelLabel: string; program: string; values: number[] }>()
    for (const energy of rows.flatMap((row) => row.energies)) {
        if (energy.hartree === null || !energy.levelRef || !energy.program) continue
        const key = `${energy.levelRef}\n${energy.program}`
        const group = groups.get(key) ?? { levelLabel: energy.levelLabel ?? energy.levelRef, program: energy.program, values: [] }
        group.values.push(energy.hartree)
        groups.set(key, group)
    }
    return [...groups.values()]
        .filter((group) => group.values.length >= 2)
        .map((group) => ({
            levelLabel: group.levelLabel,
            program: group.program,
            count: group.values.length,
            kjMol: (Math.max(...group.values) - Math.min(...group.values)) * KJ_MOL_PER_HARTREE,
        }))
}
