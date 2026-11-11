import type { ConformerProjection } from "../api/speciesEntryApi"
import type { OrderEcho } from "../api/listEnvelope"
import { orderWords } from "./paging"
import type { RelativeEnergies, RelativeEnergyMember } from "../api/relativeEnergyApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { lotLabel, type LevelOfTheory } from "../api/scientificSchemas"
import { KJ_MOL_PER_HARTREE } from "./energyUnits"
import { softwareLabel, toolReleaseLabel } from "./provenanceFormat"
import { calculationTypeCounts, conformerDisplayNumber } from "./conformerEvidence"

type ObservationCalculation = {
    calculation_ref: string
    type: string
    level_of_theory?: LevelOfTheory | null
    software_release?: { software?: string | null; version?: string | null } | null
    workflow_tool_release?: { workflow_tool?: string | null; version?: string | null } | null
    review?: { status?: unknown } | null
}
/** The observations of a conformer group, from the conformer search or from the group's own read. */
export type ObservationSource = {
    observations?: readonly {
        conformer_observation: { conformer_observation_ref: string }
        calculations?: readonly ObservationCalculation[] | null
    }[] | null
}

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
    return pictureGeometryFromLinks(conformer.geometries ?? [])
}

/** The same choice over any list of geometry links: the first final geometry, else the first listed. */
export function pictureGeometryFromLinks(links: readonly { geometry: { geometry_ref: string; role?: unknown } }[]): string | null {
    const final = links.find((link) => link.geometry.role === "final")
    return (final ?? links[0])?.geometry.geometry_ref ?? null
}

// ---------------------------------------------------------------------------
// Programs. The archive compares by level only, so a difference is shown only when both values came from one program.
// ---------------------------------------------------------------------------

/** Calculation ref to the program (name and version) that ran it; null where the calculation names none. */
export type ProgramIndex = ReadonlyMap<string, string | null>

export function programIndex(
    conformers: readonly ConformerProjection[],
    records: readonly SpeciesCalculationEnergyRecord[],
): ProgramIndex {
    const index = new Map<string, string | null>()
    for (const conformer of conformers) {
        for (const observation of conformer.observations ?? []) {
            for (const calculation of observation.calculations ?? []) {
                index.set(calculation.calculation_ref, softwareLabel(calculation.software_release))
            }
        }
    }
    for (const record of records) {
        const ref = record.calculation.calculation_ref
        if (index.get(ref) == null) index.set(ref, softwareLabel(record.software_release))
    }
    return index
}

/** The distinct programs behind a value's calculations, or null when there are none or any one names no program. */
function programsOf(refs: readonly string[], index: ProgramIndex): string[] | null {
    if (refs.length === 0) return null
    const programs = new Set<string>()
    for (const ref of refs) {
        const program = index.get(ref)
        if (!program) return null
        programs.add(program)
    }
    return [...programs].sort()
}

const joinWords = (parts: readonly string[]) => parts.join(" and ")

// ---------------------------------------------------------------------------
// Levels of theory the energies are available at.
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// What a card says about its energy.
// ---------------------------------------------------------------------------

export type CardEnergy =
    | { kind: "pending" }
    | { kind: "unread" }
    | { kind: "reference"; calculationRefs: string[]; program: string | null }
    | { kind: "value"; kjMol: number; calculationRefs: string[]; program: string }
    | { kind: "disagree"; values: { hartree: number; determinationRef: string }[] }
    | { kind: "note"; text: string }

/** Every reason code the archive can give for a shape with no usable value, in plain words. */
const REASON_WORDS: Readonly<Record<string, string>> = {
    no_structure_determination: "No value at this level.",
    no_value_at_requested_level: "No value at this level.",
    no_determination_supplies_requested_kind: "No stored value of this kind (electronic energy) is recorded for this shape.",
    recipe_not_established: "The recipe behind its value is not established, so it is not compared.",
    recipe_differs_from_request: "Its value was computed with a different recipe, so it is not compared.",
    not_eligible: "This shape is not eligible for comparison.",
    certificate_not_supported: "No stored frequency calculation shows it is a minimum, so it is listed without a number.",
    certificate_not_on_energy_surface: "The check that it is a minimum was not made on this energy surface, so it is listed without a number.",
    contested_certificate: "Stored checks on whether it is a minimum disagree, so it is listed without a number.",
    repeat_values_disagree: "Records disagree.",
    multiple_recipes_at_level: "More than one recipe at this level; not compared.",
    group_below_review_floor: "Its review status is below the review floor in use.",
    group_terminal_review_status: "This shape is rejected or deprecated.",
    determination_below_review_floor: "Its value is held out by the review floor in use.",
    determination_terminal_review_status: "Its value comes from a rejected or deprecated record.",
}

function reasonWords(code: string | null | undefined): string {
    if (!code) return "No value at this level."
    return REASON_WORDS[code] ?? "No value is shown for a reason this page does not word."
}

const REFERENCE_NONE = "Has a value at this level; the reference has none, so no difference is shown."

function memberOf(energies: RelativeEnergies, ref: string): RelativeEnergyMember | undefined {
    return energies.members.find((candidate) => candidate.conformer_ref === ref)
}

/** One card's energy in the answer the archive gave. A difference is shown only when it and the reference share one known program. */
export function cardEnergy(energies: RelativeEnergies, groupRef: string, programs: ProgramIndex): CardEnergy {
    const member = memberOf(energies, groupRef)
    if (!member) return { kind: "note", text: "No value at this level." }
    const reference = memberOf(energies, energies.reference.conformer_ref)
    const own = programsOf(member.energy_calculation_refs, programs)
    if (member.status === "reference") {
        return { kind: "reference", calculationRefs: member.energy_calculation_refs, program: own?.length === 1 ? own[0] : null }
    }
    if (member.status === "unresolved" && member.absent_reason === "repeat_values_disagree") {
        // The archive names these values by determination, an internal record with no page of its own: shown as plain refs.
        return {
            kind: "disagree",
            values: member.alternates.map((alternate) => ({ hartree: alternate.value_hartree, determinationRef: alternate.determination_ref })),
        }
    }
    if (member.status === "present") {
        if (member.relative_energy_kj_mol == null) {
            return { kind: "note", text: member.detail.includes("reference_unavailable") ? REFERENCE_NONE : "Has a value at this level, but no difference is shown." }
        }
        const base = reference ? programsOf(reference.energy_calculation_refs, programs) : null
        if (own?.length === 1 && base?.length === 1 && own[0] === base[0]) {
            return { kind: "value", kjMol: member.relative_energy_kj_mol, calculationRefs: member.energy_calculation_refs, program: own[0] }
        }
        if (own && base) {
            return { kind: "note", text: `Computed with ${joinWords(own)}; the reference with ${joinWords(base)}. Energies from different programs are not compared.` }
        }
        return { kind: "note", text: "The program of this value or of the reference is not known to this page, so no difference is shown. Energies from different programs are not compared." }
    }
    return { kind: "note", text: reasonWords(member.absent_reason) }
}

/** The one program behind the reference value, or null when it is unknown or there are several. */
export function referenceProgram(energies: RelativeEnergies, programs: ProgramIndex): string | null {
    const reference = memberOf(energies, energies.reference.conformer_ref)
    const known = reference ? programsOf(reference.energy_calculation_refs, programs) : null
    return known?.length === 1 ? known[0] : null
}

/** The sign is always written, so a value below the reference reads as such. */
export function formatRelativeKj(kjMol: number): string {
    const rounded = Math.abs(kjMol) < 0.005 ? 0 : kjMol
    return `${rounded > 0 ? "+" : rounded < 0 ? "−" : ""}${Math.abs(rounded).toFixed(2)}`
}

/** A hartree value with a true minus sign, like the other energies on the page. */
export function formatHartree(value: number): string {
    return value.toFixed(6).replace("-", "−")
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

// ---------------------------------------------------------------------------
// Layer 2: the calculations of one shape.
// ---------------------------------------------------------------------------

export type ShapeCalculation = {
    ref: string
    type: string
    levelRef: string | null
    levelLabel: string | null
    program: string | null
    workflowTool: string | null
    review: string | null
}

export type EnergyKind = "sp" | "opt"
export type ShapeEnergy = ShapeCalculation & { kind: EnergyKind; hartree: number | null; review: string | null }

export type ShapeRow = {
    observationRef: string
    /** Optimisation, frequency and other calculations that found this geometry, in deposit order. */
    foundBy: ShapeCalculation[]
    /** Every single-point energy and every optimisation's final energy computed on this geometry, in deposit order. */
    energies: ShapeEnergy[]
}

const describeCalculation = (calculation: ObservationCalculation): ShapeCalculation => {
    const level = calculation.level_of_theory
    return {
        ref: calculation.calculation_ref,
        type: calculation.type,
        levelRef: level?.level_of_theory_ref ?? null,
        levelLabel: level ? lotLabel(level) : null,
        program: softwareLabel(calculation.software_release),
        workflowTool: toolReleaseLabel(calculation.workflow_tool_release),
        review: typeof calculation.review?.status === "string" ? calculation.review.status : null,
    }
}

const recordCalculation = (record: SpeciesCalculationEnergyRecord): ShapeCalculation => ({
    ref: record.calculation.calculation_ref,
    type: record.calculation.calculation_type,
    levelRef: record.level_of_theory?.level_of_theory_ref ?? null,
    levelLabel: record.level_of_theory ? lotLabel(record.level_of_theory) : null,
    program: softwareLabel(record.software_release),
    workflowTool: null,
    review: null,
})

const reviewOf = (record: SpeciesCalculationEnergyRecord | undefined): string | null => {
    const review = record?.calculation.review as { status?: unknown } | undefined
    return typeof review?.status === "string" ? review.status : null
}

/**
 * One row per observation of the shape. Energies are joined by calculation ref from the species-calculations reads; a
 * record that names this observation but is missing from the group's own list is added. Nothing is picked or merged.
 */
export function shapeRows(
    conformer: ObservationSource,
    spRecords: readonly SpeciesCalculationEnergyRecord[],
    optRecords: readonly SpeciesCalculationEnergyRecord[],
): ShapeRow[] {
    const byCalculation = new Map([...spRecords, ...optRecords].map((record) => [record.calculation.calculation_ref, record]))
    return (conformer.observations ?? []).map((observation) => {
        const observationRef = observation.conformer_observation.conformer_observation_ref
        const calculations = observation.calculations ?? []
        const energies = new Map<string, ShapeEnergy>()
        for (const calculation of calculations.filter((candidate) => candidate.type === "sp" || candidate.type === "opt")) {
            const record = byCalculation.get(calculation.calculation_ref)
            const own = describeCalculation(calculation)
            const fromRecord = record ? recordCalculation(record) : null
            energies.set(calculation.calculation_ref, {
                ...own,
                kind: calculation.type as EnergyKind,
                levelRef: own.levelRef ?? fromRecord?.levelRef ?? null,
                levelLabel: own.levelLabel ?? fromRecord?.levelLabel ?? null,
                program: own.program ?? fromRecord?.program ?? null,
                hartree: record?.energy?.energy_hartree ?? null,
                review: reviewOf(record),
            })
        }
        for (const record of [...spRecords, ...optRecords]) {
            if (record.conformer?.conformer_observation_ref !== observationRef || energies.has(record.calculation.calculation_ref)) continue
            energies.set(record.calculation.calculation_ref, {
                ...recordCalculation(record),
                kind: record.calculation.calculation_type === "opt" ? "opt" : "sp",
                hartree: record.energy?.energy_hartree ?? null,
                review: reviewOf(record),
            })
        }
        return {
            observationRef,
            foundBy: calculations.filter((calculation) => calculation.type !== "sp").map(describeCalculation),
            energies: [...energies.values()],
        }
    })
}

export type SpreadLine = { kind: EnergyKind; levelLabel: string; program: string; count: number; kjMol: number }

/** For each kind of energy, level and program with two or more values in the rows: the largest difference. Other pairs are never compared. */
export function spreadLines(rows: readonly ShapeRow[]): SpreadLine[] {
    const groups = new Map<string, { kind: EnergyKind; levelLabel: string; program: string; values: number[] }>()
    for (const energy of rows.flatMap((row) => row.energies)) {
        if (energy.hartree === null || !energy.levelRef || !energy.program) continue
        const key = `${energy.kind}\n${energy.levelRef}\n${energy.program}`
        const group = groups.get(key) ?? { kind: energy.kind, levelLabel: energy.levelLabel ?? energy.levelRef, program: energy.program, values: [] }
        group.values.push(energy.hartree)
        groups.set(key, group)
    }
    return [...groups.values()]
        .filter((group) => group.values.length >= 2)
        .map((group) => ({
            kind: group.kind,
            levelLabel: group.levelLabel,
            program: group.program,
            count: group.values.length,
            kjMol: (Math.max(...group.values) - Math.min(...group.values)) * KJ_MOL_PER_HARTREE,
        }))
}

const CARD_ORDER = "The cards within a page are listed by conformer number, then label, then reference."

/** One statement of both orders: the order the pages follow, and the card order inside a page. */
export function shapeOrderSentence(sort: OrderEcho | null | undefined): string {
    const words = orderWords(sort)
    const lead = !words
        ? "Pages follow the archive's display order."
        : sort?.requested_by
            ? `Pages follow the order requested (${sort.requested_by}): ${words}.`
            : `Pages follow the archive's display order: ${words}.`
    return `${lead} ${CARD_ORDER}`
}
