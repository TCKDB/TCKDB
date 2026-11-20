import type { IrcPoints, PathSearchPoints, ScanCoordinate, ScanPoints } from "../api/calculationPathsApi"
import { KJ_MOL_PER_HARTREE } from "./energyUnits"
import { hartreeText, relativeKjText } from "./energyProfile"

/**
 * The points of a path-shaped calculation (path search, IRC, scan) as one figure and one table read them: a stored
 * coordinate on x, an energy relative to a named reference on y, and the markers the producer set. Nothing is
 * interpolated or borrowed from another calculation; a value the read does not carry stays null.
 */

export type PathKind = "path_search" | "irc" | "scan"

/** How the y values were obtained, which the axis title states. */
export type EnergyMode = "stored" | "from-hartree" | "none"

/** Marker style; shape carries identity, colour only reinforces it. */
export type PathEmphasis = "guess" | "climbing" | "guess-climbing" | "saddle" | null

export type PathPoint = {
    index: number
    /** The coordinate as stored, or null when the read has none for this point. */
    coordinate: number | null
    /** The drawn x: the coordinate, or the point index when the profile has no complete coordinate. */
    x: number
    /** The drawn y in kJ/mol, or null when this point has no energy. */
    kj: number | null
    hartree: number | null
    rmsGradient: number | null
    labels: string[]
    emphasis: PathEmphasis
    leg: "forward" | "reverse" | null
    geometryRef: string | null
    natoms: number | null
}

export type PathProfile = {
    kind: PathKind
    /** Drawing order: by x, then by index. */
    points: PathPoint[]
    /** True when every point has a stored coordinate and x is that coordinate. */
    hasCoordinate: boolean
    xTitle: string
    /** Column heading for the coordinate in the table. */
    xColumn: string
    /** Where a point sits, in words: "3.759 along the path". */
    xPhrase: string
    xDecimals: number
    energyMode: EnergyMode
    yTitle: string
    /** Points that have no energy while others do; they are listed in the table but not drawn. */
    undrawnCount: number
    /** False when this kind of calculation stores no gradient per point, so there is no column to show. */
    hasGradients: boolean
}

/** Hartree within which a stored energy is taken to be the stored reference. */
const REFERENCE_MATCH_HARTREE = 1e-8

export const NOT_DEPOSITED = "not deposited"

type RawPoint = {
    index: number
    coordinate: number | null
    hartree: number | null
    storedKj: number | null
    rmsGradient: number | null
    guess: boolean
    climbing: boolean
    saddle: boolean
    leg: "forward" | "reverse" | null
    geometryRef: string | null
    natoms: number | null
    /** Where the point is drawn when that differs from its stored coordinate (a reverse IRC leg reported unsigned). */
    drawnAt?: number
}

type AxisWords = {
    /** "point 0", "the saddle point": what zero is when the read leaves the choice to the page. */
    anchorWords: (point: RawPoint) => string
    anchor: (points: RawPoint[]) => RawPoint | undefined
    emptyTail: string
}

function finite(value: number | null | undefined): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null
}

function energyAxis(points: RawPoint[], reference: number | null, words: AxisWords): { mode: EnergyMode; kj: (number | null)[]; title: string } {
    if (points.some((point) => point.storedKj !== null)) {
        let title = "kJ/mol, relative energy as deposited (reference not recorded)"
        if (reference !== null) {
            const match = points.find((point) => point.hartree !== null && Math.abs(point.hartree - reference) < REFERENCE_MATCH_HARTREE)
            title = match
                ? `kJ/mol relative to ${words.anchorWords(match)}`
                : `kJ/mol relative to ${hartreeText(reference)} hartree, the stored reference`
        }
        return { mode: "stored", kj: points.map((point) => point.storedKj), title }
    }
    const preferred = words.anchor(points)
    const anchor = preferred?.hartree != null ? preferred : points.find((point) => point.hartree !== null)
    if (anchor && anchor.hartree !== null) {
        const zero = anchor.hartree
        return {
            mode: "from-hartree",
            kj: points.map((point) => (point.hartree === null ? null : (point.hartree - zero) * KJ_MOL_PER_HARTREE)),
            title: `kJ/mol relative to ${words.anchorWords(anchor)}, from the stored electronic energies`,
        }
    }
    const named = preferred ? words.anchorWords(preferred) : "the first point"
    return { mode: "none", kj: points.map(() => null), title: `kJ/mol relative to ${named}: ${words.emptyTail}` }
}

function emphasisOf(point: RawPoint): PathEmphasis {
    if (point.guess && point.climbing) return "guess-climbing"
    if (point.guess) return "guess"
    if (point.climbing) return "climbing"
    if (point.saddle) return "saddle"
    return null
}

function assemble(kind: PathKind, raw: RawPoint[], labelsOf: (point: RawPoint, ordered: RawPoint[]) => string[], axis: {
    reference: number | null
    words: AxisWords
    coordinateTitle: string
    indexTitle: string
    xColumn: string
    /** `{x}` is replaced by the coordinate. */
    xPhrase: string
    xDecimals: number
    hasGradients?: boolean
}): PathProfile {
    const hasCoordinate = raw.length > 0 && raw.every((point) => point.coordinate !== null)
    const at = (point: RawPoint) => point.drawnAt ?? (point.coordinate as number)
    const ordered = [...raw].sort((a, b) => (hasCoordinate ? at(a) - at(b) : 0) || a.index - b.index)
    const energy = energyAxis(ordered, axis.reference, axis.words)
    const points: PathPoint[] = ordered.map((point, position) => ({
        index: point.index,
        coordinate: point.coordinate,
        x: hasCoordinate ? at(point) : point.index,
        kj: energy.kj[position],
        hartree: point.hartree,
        rmsGradient: point.rmsGradient,
        labels: labelsOf(point, ordered),
        emphasis: emphasisOf(point),
        leg: point.leg,
        geometryRef: point.geometryRef,
        natoms: point.natoms,
    }))
    const drawn = points.filter((point) => point.kj !== null).length
    return {
        kind,
        points,
        hasCoordinate,
        xTitle: hasCoordinate ? axis.coordinateTitle : axis.indexTitle,
        xColumn: axis.xColumn,
        xPhrase: axis.xPhrase,
        xDecimals: axis.xDecimals,
        energyMode: energy.mode,
        yTitle: energy.title,
        undrawnCount: energy.mode === "none" ? 0 : points.length - drawn,
        hasGradients: axis.hasGradients ?? true,
    }
}

/** A path search's points: x is the stored distance along the path, zero is point 0 unless the read names a reference. */
export function pathSearchProfile({ summary, points }: PathSearchPoints): PathProfile {
    const raw: RawPoint[] = points.map((point) => ({
        index: point.point_index,
        coordinate: finite(point.path_coordinate),
        hartree: finite(point.electronic_energy_hartree),
        storedKj: finite(point.relative_energy_kj_mol),
        rmsGradient: finite(point.rms_gradient),
        guess: point.is_ts_guess === true,
        climbing: point.is_climbing_image === true,
        saddle: false,
        leg: null,
        geometryRef: point.geometry_link?.geometry_ref ?? point.geometry_ref ?? null,
        natoms: point.geometry_link?.natoms ?? null,
    }))
    const indices = raw.map((point) => point.index)
    const first = indices.length > 1 ? Math.min(...indices) : null
    const last = indices.length > 1 ? Math.max(...indices) : null
    return assemble("path_search", raw, (point) => [
        ...(point.index === first ? ["start"] : []),
        ...(point.index === last ? ["end"] : []),
        ...(point.guess ? ["picked guess"] : []),
        ...(point.climbing ? ["climbing image"] : []),
    ], {
        reference: finite(summary.zero_energy_reference_hartree),
        words: {
            anchorWords: (point) => `point ${point.index}`,
            anchor: (ordered) => ordered.find((point) => point.index === first) ?? ordered[0],
            emptyTail: "not deposited for this path",
        },
        coordinateTitle: "distance along the path, as deposited",
        indexTitle: "point number (no distance along the path was deposited)",
        xColumn: "Along the path",
        xPhrase: "{x} along the path",
        xDecimals: 3,
    })
}

function legOf(direction: string | null | undefined): "forward" | "reverse" | null {
    return direction === "forward" || direction === "reverse" ? direction : null
}

/**
 * An IRC's points: x is the reaction coordinate as the program reported it, zero is the saddle point unless the read
 * names another reference. A reverse leg reported with positive coordinates is drawn to the left of the saddle point,
 * and the axis title says so; the table keeps the stored value.
 */
export function ircProfile({ summary, points }: IrcPoints): PathProfile {
    const reverseUnsigned = points.some((point) => point.direction === "reverse" && (point.reaction_coordinate ?? 0) > 0)
        && points.some((point) => point.direction === "forward")
    const raw: RawPoint[] = points.map((point) => {
        const coordinate = finite(point.reaction_coordinate)
        const leg = legOf(point.direction)
        return {
            index: point.point_index,
            coordinate,
            hartree: finite(point.electronic_energy_hartree),
            storedKj: finite(point.relative_energy_kj_mol),
            rmsGradient: finite(point.rms_gradient),
            guess: false,
            climbing: false,
            saddle: point.is_ts,
            leg,
            geometryRef: point.geometry_link?.geometry_ref ?? point.geometry_ref ?? null,
            natoms: point.geometry_link?.natoms ?? null,
            ...(reverseUnsigned && leg === "reverse" && coordinate !== null ? { drawnAt: -coordinate } : {}),
        }
    })
    const profile = assemble("irc", raw, () => [], {
        reference: finite(summary.zero_energy_reference_hartree),
        words: {
            anchorWords: (point) => (point.saddle ? "the saddle point" : `point ${point.index}`),
            anchor: (ordered) => ordered.find((point) => point.saddle),
            emptyTail: "not deposited for this IRC",
        },
        coordinateTitle: reverseUnsigned
            ? "reaction coordinate, as the program reported it (reverse leg drawn to the left)"
            : "reaction coordinate, as the program reported it",
        indexTitle: "point number (no reaction coordinate was deposited)",
        xColumn: "Reaction coordinate",
        xPhrase: "reaction coordinate {x}",
        xDecimals: 3,
    })
    // Ends are named by leg: the drawn extremes of each leg, never the saddle point itself.
    const reverse = profile.points.filter((point) => point.leg === "reverse")
    const forward = profile.points.filter((point) => point.leg === "forward")
    const reverseEnd = reverse.length > 0 ? reverse.reduce((a, b) => (b.x < a.x ? b : a)) : null
    const forwardEnd = forward.length > 0 ? forward.reduce((a, b) => (b.x > a.x ? b : a)) : null
    for (const point of profile.points) {
        if (point.emphasis === "saddle") point.labels = ["saddle point"]
        else if (point === reverseEnd) point.labels = ["reverse end"]
        else if (point === forwardEnd) point.labels = ["forward end"]
    }
    return profile
}

const UNIT_WORDS: Readonly<Record<string, { axis: string; short: string }>> = {
    degree: { axis: "degrees", short: "°" },
    angstrom: { axis: "Å", short: " Å" },
}

/** "dihedral 4-1-2-3", from the scanned coordinate's kind and atoms. */
export function scanCoordinateWords(coordinate: ScanCoordinate): string {
    return `${coordinate.coordinate_kind.replaceAll("_", " ")} ${coordinate.atom_indices.join("-")}`
}

/** A scan's points: x is the scanned coordinate in its stored unit for a 1-D scan, zero is the stored reference. */
export function scanProfile({ summary, coordinates, points }: ScanPoints): PathProfile {
    const coordinate = summary.dimension === 1 && coordinates.length === 1 ? coordinates[0] : null
    const unit = coordinate?.value_unit && Object.hasOwn(UNIT_WORDS, coordinate.value_unit) ? UNIT_WORDS[coordinate.value_unit] : null
    const raw: RawPoint[] = points.map((point) => {
        const value = coordinate ? point.coordinate_values.find((entry) => entry.coordinate_index === coordinate.coordinate_index) : undefined
        return {
            index: point.point_index,
            coordinate: coordinate ? finite(value?.coordinate_value) : null,
            hartree: finite(point.electronic_energy_hartree),
            storedKj: finite(point.relative_energy_kj_mol),
            rmsGradient: null,
            guess: false,
            climbing: false,
            saddle: false,
            leg: null,
            geometryRef: point.geometry_link?.geometry_ref ?? point.geometry_ref ?? null,
            natoms: point.geometry_link?.natoms ?? null,
        }
    })
    const indices = raw.map((point) => point.index)
    const first = indices.length > 1 ? Math.min(...indices) : null
    const last = indices.length > 1 ? Math.max(...indices) : null
    const name = coordinate ? scanCoordinateWords(coordinate) : "scanned coordinate"
    return assemble("scan", raw, (point) => [
        ...(point.index === first ? ["start"] : []),
        ...(point.index === last ? ["end"] : []),
    ], {
        reference: finite(summary.zero_energy_reference_hartree),
        words: {
            anchorWords: (point) => `point ${point.index}`,
            anchor: (ordered) => ordered.find((point) => point.index === first) ?? ordered[0],
            emptyTail: "not deposited for this scan",
        },
        coordinateTitle: `${name}, ${unit ? unit.axis : "unit not recorded"}`,
        indexTitle: summary.dimension > 1 ? `point number (a ${summary.dimension}-D scan has no single coordinate axis)` : "point number (no coordinate value was deposited)",
        xColumn: coordinate ? `${name[0].toUpperCase()}${name.slice(1)}` : "Coordinate",
        xPhrase: `${name} {x}${unit ? unit.short : ""}`,
        xDecimals: 1,
        hasGradients: false,
    })
}

/** Ticks for one axis: round steps, at most about five, padded to whole steps around `low`..`high`. */
export type AxisScale = { min: number; max: number; ticks: number[] }

function niceStep(raw: number): number {
    const power = 10 ** Math.floor(Math.log10(raw))
    const fraction = raw / power
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power
}

export function axisScale(values: readonly number[], minSpan: number, pad = 0.05, divisions = 4): AxisScale {
    if (values.length === 0) return { min: 0, max: 1, ticks: [0, 1] }
    const low = Math.min(...values)
    const high = Math.max(...values)
    const span = Math.max(high - low, minSpan)
    const extra = (span - (high - low)) / 2
    const step = niceStep(span / divisions)
    const min = Math.floor((low - extra - span * pad) / step) * step
    const max = Math.ceil((high + extra + span * pad) / step) * step
    const ticks: number[] = []
    for (let tick = min; tick <= max + step / 1000; tick += step) ticks.push(Number(tick.toFixed(6)))
    return { min, max, ticks }
}

/** Smallest y span drawn, so a flat stretch of a few kJ/mol is not drawn as a cliff. */
export const MIN_ENERGY_SPAN_KJ_MOL = 5

/** The y axis: energies when there are any, otherwise a single zero line. */
export function energyScale(profile: PathProfile): AxisScale {
    const values = profile.points.flatMap((point) => (point.kj === null ? [] : [point.kj]))
    if (values.length === 0) return { min: -1, max: 1, ticks: [0] }
    return axisScale([...values, 0], MIN_ENERGY_SPAN_KJ_MOL)
}

/** The x axis over the drawn coordinate; a single point gets a unit span around it. */
export function coordinateScale(profile: PathProfile): AxisScale {
    const values = profile.points.map((point) => point.x)
    return axisScale(values, profile.hasCoordinate ? 1e-6 : 1, 0, 6)
}

export function coordinateText(profile: PathProfile, value: number | null): string {
    return value === null ? NOT_DEPOSITED : value.toFixed(profile.xDecimals).replace("-", "−")
}

export function energyText(kj: number | null): string {
    return kj === null ? "energy not deposited" : `${relativeKjText(kj)} kJ/mol`
}

/** The words a point's direct label and tooltip lead with: "8 · picked guess", "saddle point · 0.0". */
export function directLabel(profile: PathProfile, point: PathPoint): string {
    const parts = profile.kind === "irc" ? [point.labels.join(", ")] : [String(point.index), point.labels.join(", ")]
    if (point.kj !== null) parts.push(relativeKjText(point.kj))
    return parts.join(" · ")
}

/** One sentence naming a point, its coordinate and its energy, for a tooltip and the hit target's accessible name. */
export function pointSentence(profile: PathProfile, point: PathPoint): string {
    const head = `Point ${point.index}${point.leg ? ` · ${point.leg}` : ""}${point.labels.length > 0 ? ` (${point.labels.join(", ")})` : ""}`
    return `${head}: ${whereText(profile, point)}, ${energyText(point.kj)}`
}

/** "3.759 along the path", "reaction coordinate 1.342". */
export function whereText(profile: PathProfile, point: PathPoint): string {
    return profile.hasCoordinate ? profile.xPhrase.replace("{x}", coordinateText(profile, point.coordinate)) : "no coordinate deposited"
}

/** The sentence under a path search's picture: what is drawn, what is missing, and what the guess is. */
export function pathSearchCaption(profile: PathProfile): string {
    const count = profile.points.length
    const noun = count === 1 ? "point" : "points"
    const xs = profile.points.map((point) => point.x)
    const where = profile.hasCoordinate
        ? count > 1
            ? `${count} ${noun} placed by their stored distance along the path, ${coordinateText(profile, Math.min(...xs))} to ${coordinateText(profile, Math.max(...xs))}; no unit is stored for this distance.`
            : `${count} ${noun} at its stored distance along the path.`
        : `${count} ${noun} placed by point number; no distance along the path was deposited.`
    const energy = profile.energyMode === "none"
        ? " No energies were deposited for this path, so the points sit on the zero line and the energy axis is empty."
        : ""
    const guess = profile.points.some((point) => point.emphasis === "guess" || point.emphasis === "guess-climbing")
        ? " The filled point is the picked guess: the algorithm's pick, not a checked saddle point."
        : ""
    const climbing = profile.points.some((point) => point.emphasis === "climbing" || point.emphasis === "guess-climbing")
        ? " The diamond is the climbing image."
        : ""
    return `${where}${energy}${guess}${climbing}`
}

/** The point the strip and the stepper open on: the picked guess or saddle point, otherwise the first drawn. */
export function initialPointIndex(profile: PathProfile): number | null {
    const marked = profile.points.find((point) => point.emphasis === "guess" || point.emphasis === "guess-climbing" || point.emphasis === "saddle")
    return (marked ?? profile.points[0])?.index ?? null
}

/** A short marker word for a strip card: "guess", "climbing image", "saddle point", or none. */
export function markerWord(point: PathPoint): string | null {
    if (point.emphasis === "guess" || point.emphasis === "guess-climbing") return "guess"
    if (point.emphasis === "climbing") return "climbing image"
    if (point.emphasis === "saddle") return "saddle point"
    return null
}
