import { describe, expect, it } from "vitest"
import type { PathSearchPoint, PathSearchPoints, PathSearchSummary } from "../api/calculationPathsApi"
import { KJ_MOL_PER_HARTREE } from "./energyUnits"
import { axisScale, coordinateScale, directLabel, energyScale, pathSearchCaption, pathSearchProfile, pointSentence } from "./pathProfile"

const SUMMARY: PathSearchSummary = {
    method: "gsm",
    is_double_ended: true,
    converged: null,
    n_points: 4,
    selected_ts_point_index: 2,
    climbing_image_index: null,
    source_endpoint_count: 2,
    zero_energy_reference_hartree: null,
    stored_point_count: 4,
    ts_guess_count: 1,
    climbing_image_count: 0,
}

function point(index: number, overrides: Partial<PathSearchPoint> = {}): PathSearchPoint {
    return {
        point_index: index,
        path_coordinate: index * 0.5,
        electronic_energy_hartree: null,
        relative_energy_kj_mol: null,
        rms_gradient: null,
        is_ts_guess: false,
        is_climbing_image: false,
        geometry_ref: `geom_${index}`,
        geometry_link: { geometry_ref: `geom_${index}`, natoms: 9 },
        ...overrides,
    }
}

function read(points: PathSearchPoint[], summary: Partial<PathSearchSummary> = {}): PathSearchPoints {
    return { summary: { ...SUMMARY, ...summary }, points }
}

describe("pathSearchProfile without energies", () => {
    const profile = pathSearchProfile(read([point(0), point(1), point(2, { is_ts_guess: true }), point(3)]))

    it("keeps every point at its stored coordinate with no energy", () => {
        expect(profile.energyMode).toBe("none")
        expect(profile.points.map((p) => p.x)).toEqual([0, 0.5, 1, 1.5])
        expect(profile.points.every((p) => p.kj === null)).toBe(true)
        expect(profile.undrawnCount).toBe(0)
    })

    it("says on the axis and in the caption that energies were not deposited", () => {
        expect(profile.yTitle).toBe("kJ/mol relative to point 0: not deposited for this path")
        expect(pathSearchCaption(profile)).toContain("No energies were deposited for this path")
        expect(pathSearchCaption(profile)).toContain("not a checked saddle point")
    })

    it("draws a single zero line, never an invented range", () => {
        expect(energyScale(profile).ticks).toEqual([0])
    })

    it("labels start, end and the picked guess", () => {
        expect(profile.points.map((p) => p.labels)).toEqual([["start"], [], ["picked guess"], ["end"]])
        expect(directLabel(profile, profile.points[2])).toBe("2 · picked guess")
        expect(pointSentence(profile, profile.points[2])).toBe("Point 2 (picked guess): 1.000 along the path, energy not deposited")
    })

    it("says no unit is stored when the read records none", () => {
        expect(profile.xTitle).toBe("distance along the path, as deposited")
        expect(profile.xColumn).toBe("Along the path")
        expect(pathSearchCaption(profile)).toContain("0.000 to 1.500; no unit is stored for this distance.")
    })
})

describe("pathSearchProfile with a recorded unit", () => {
    const profile = pathSearchProfile(read([point(0), point(1), point(2, { path_coordinate: 3.759, is_ts_guess: true })], { path_coordinate_unit: "angstrom" }))

    it("names the unit on the axis, the column, the caption and the point", () => {
        expect(profile.xTitle).toBe("distance along the path, Å")
        expect(profile.xColumn).toBe("Along the path (Å)")
        expect(pathSearchCaption(profile)).toContain("placed by their stored distance along the path, 0.00 Å to 3.76 Å.")
        expect(pointSentence(profile, profile.points[2])).toBe("Point 2 (end, picked guess): 3.76 Å along the path, energy not deposited")
    })

    it("words a normalised arc length as a fraction of the way", () => {
        const fraction = pathSearchProfile(read([point(0, { path_coordinate: 0 }), point(1, { path_coordinate: 0.4 }), point(2, { path_coordinate: 1 })], { path_coordinate_unit: "fraction" }))
        expect(fraction.xTitle).toBe("distance along the path, fraction of the path")
        expect(pointSentence(fraction, fraction.points[1])).toBe("Point 1: 0.40 of the way along the path, energy not deposited")
        expect(pathSearchCaption(fraction)).toContain("0.00 to 1.00, as a fraction of the path.")
    })
})

describe("markers", () => {
    it("fills the guess, makes the climbing image a diamond, and marks a point that is both", () => {
        const profile = pathSearchProfile(read([
            point(0),
            point(1, { is_ts_guess: true }),
            point(2, { is_climbing_image: true }),
            point(3, { is_ts_guess: true, is_climbing_image: true }),
        ]))
        expect(profile.points.map((p) => p.emphasis)).toEqual([null, "guess", "climbing", "guess-climbing"])
        expect(profile.points[3].labels).toEqual(["end", "picked guess", "climbing image"])
        expect(pathSearchCaption(profile)).toContain("The diamond is the climbing image.")
    })

    it("does not call a lone point both start and end", () => {
        const profile = pathSearchProfile(read([point(0, { path_coordinate: null, is_ts_guess: true })]))
        expect(profile.points[0].labels).toEqual(["picked guess"])
        expect(profile.hasCoordinate).toBe(false)
        expect(profile.xTitle).toContain("no distance along the path was deposited")
    })
})

describe("energies", () => {
    it("uses stored relative energies and names the point the stored reference belongs to", () => {
        const profile = pathSearchProfile(read([
            point(0, { electronic_energy_hartree: -1.0, relative_energy_kj_mol: 0 }),
            point(1, { electronic_energy_hartree: -0.99, relative_energy_kj_mol: 26.25 }),
        ], { zero_energy_reference_hartree: -1.0 }))
        expect(profile.energyMode).toBe("stored")
        expect(profile.yTitle).toBe("kJ/mol relative to point 0")
        expect(profile.points.map((p) => p.kj)).toEqual([0, 26.25])
    })

    it("says the reference is not recorded when the read gives none", () => {
        const profile = pathSearchProfile(read([point(0, { relative_energy_kj_mol: 3 }), point(1, { relative_energy_kj_mol: 5 })]))
        expect(profile.yTitle).toBe("kJ/mol, relative energy as deposited (reference not recorded)")
    })

    it("computes kJ/mol from electronic energies relative to point 0 when no relative energy is stored", () => {
        const profile = pathSearchProfile(read([
            point(0, { electronic_energy_hartree: -2.0 }),
            point(1, { electronic_energy_hartree: -1.99 }),
            point(2),
        ]))
        expect(profile.energyMode).toBe("from-hartree")
        expect(profile.yTitle).toBe("kJ/mol relative to point 0, from the stored electronic energies")
        expect(profile.points[1].kj).toBeCloseTo(0.01 * KJ_MOL_PER_HARTREE, 6)
        expect(profile.points[2].kj).toBeNull()
        expect(profile.undrawnCount).toBe(1)
    })
})

describe("axis scales", () => {
    it("rounds to whole steps and spans every value", () => {
        expect(axisScale([-162.6, 0, -180], 5)).toEqual({ min: -200, max: 50, ticks: [-200, -150, -100, -50, 0, 50] })
    })

    it("gives a lone index a unit span", () => {
        const profile = pathSearchProfile(read([point(0, { path_coordinate: null })]))
        const scale = coordinateScale(profile)
        expect(scale.max - scale.min).toBeGreaterThan(0)
    })
})
