import { describe, expect, it } from "vitest"
import type { IrcPoint, ScanPoints } from "../api/calculationPathsApi"
import { directLabel, initialPointIndex, ircProfile, pointSentence, scanProfile } from "./pathProfile"

const IRC_SUMMARY = { direction: "both", zero_energy_reference_hartree: -117.786148505, forward_point_count: 2, reverse_point_count: 2, ts_point_count: 1 }

function ircPoint(index: number, direction: string | null, coordinate: number, kj: number, hartree: number): IrcPoint {
    return {
        point_index: index, direction, is_ts: direction === null, reaction_coordinate: coordinate,
        electronic_energy_hartree: hartree, relative_energy_kj_mol: kj, rms_gradient: 0.001, geometry_ref: `geom_${index}`,
    }
}

// The order the live read returns: reverse leg, forward leg, then the saddle point last.
const SIGNED: IrcPoint[] = [
    ircPoint(0, "reverse", -0.075, -0.98, -117.7865),
    ircPoint(1, "reverse", -3.127, -162.6, -117.848),
    ircPoint(2, "forward", 0.075, -1.0, -117.7865),
    ircPoint(3, "forward", 3.646, -180.0, -117.8547),
    ircPoint(4, null, 0, 0, -117.786148505),
]

describe("ircProfile", () => {
    const profile = ircProfile({ summary: IRC_SUMMARY, points: SIGNED })

    it("draws along the reaction coordinate, reverse leg first, saddle point in the middle", () => {
        expect(profile.points.map((p) => p.index)).toEqual([1, 0, 4, 2, 3])
        expect(profile.xTitle).toBe("reaction coordinate, as the program reported it")
        expect(profile.yTitle).toBe("kJ/mol relative to the saddle point")
    })

    it("fills the saddle point and names the ends by leg", () => {
        expect(profile.points.find((p) => p.index === 4)?.emphasis).toBe("saddle")
        expect(directLabel(profile, profile.points[2])).toBe("saddle point · 0.0")
        expect(directLabel(profile, profile.points[0])).toBe("reverse end · −162.6")
        expect(directLabel(profile, profile.points[4])).toBe("forward end · −180.0")
        expect(initialPointIndex(profile)).toBe(4)
        expect(pointSentence(profile, profile.points[3])).toBe("Point 2 · forward: reaction coordinate 0.075, −1.0 kJ/mol")
    })

    it("draws an unsigned reverse leg to the left and says so, keeping the stored value", () => {
        const unsigned = SIGNED.map((point) => (point.direction === "reverse" ? { ...point, reaction_coordinate: Math.abs(point.reaction_coordinate ?? 0) } : point))
        const drawn = ircProfile({ summary: IRC_SUMMARY, points: unsigned })
        expect(drawn.points.map((p) => p.index)).toEqual([1, 0, 4, 2, 3])
        expect(drawn.points[0].x).toBeCloseTo(-3.127)
        expect(drawn.points[0].coordinate).toBeCloseTo(3.127)
        expect(drawn.xTitle).toContain("reverse leg drawn to the left")
    })

    it("names the recorded unit on the axis, the column and the point", () => {
        const withUnit = ircProfile({ summary: { ...IRC_SUMMARY, reaction_coordinate_unit: "amu_half_bohr" }, points: SIGNED })
        expect(withUnit.xTitle).toBe("reaction coordinate, amu½·bohr")
        expect(withUnit.xColumn).toBe("Reaction coordinate (amu½·bohr)")
        expect(pointSentence(withUnit, withUnit.points[3])).toBe("Point 2 · forward: reaction coordinate 0.075 amu½·bohr, −1.0 kJ/mol")
    })

    it("words a step-number coordinate as a step", () => {
        const steps = ircProfile({ summary: { ...IRC_SUMMARY, reaction_coordinate_unit: "step" }, points: SIGNED.map((p) => ({ ...p, reaction_coordinate: p.point_index })) })
        expect(steps.xTitle).toBe("reaction coordinate, step number (reverse leg drawn to the left)")
        expect(pointSentence(steps, steps.points.find((p) => p.index === 3)!)).toBe("Point 3 · forward (forward end): step 3, −180.0 kJ/mol")
    })
})

const SCAN: ScanPoints = {
    summary: { dimension: 1, is_relaxed: true, zero_energy_reference_hartree: -117.310595319, coordinate_count: 1, point_count: 3 },
    coordinates: [{ coordinate_index: 1, coordinate_kind: "dihedral", atom_indices: [4, 1, 2, 3], start_value: 180, end_value: 196, step_count: 3, value_unit: "degree" }],
    points: [1, 2, 3].map((index) => ({
        point_index: index,
        electronic_energy_hartree: [-117.310595319, -117.310092229, -117.308606188][index - 1],
        relative_energy_kj_mol: [0, 1.32, 5.22][index - 1],
        geometry_ref: `geom_${index}`,
        coordinate_values: [{ coordinate_index: 1, coordinate_value: 180 + (index - 1) * 8, value_unit: "degree" }],
    })),
}

describe("scanProfile", () => {
    it("draws a 1-D scan along its coordinate in the stored unit, relative to point 1", () => {
        const profile = scanProfile(SCAN)
        expect(profile.xTitle).toBe("dihedral 4-1-2-3, degrees")
        expect(profile.yTitle).toBe("kJ/mol relative to point 1")
        expect(profile.points.map((p) => p.x)).toEqual([180, 188, 196])
        expect(profile.points.map((p) => p.labels)).toEqual([["start"], [], ["end"]])
        expect(profile.hasGradients).toBe(false)
        expect(pointSentence(profile, profile.points[1])).toBe("Point 2: dihedral 4-1-2-3 188.0°, +1.3 kJ/mol")
    })

    it("falls back to point numbers for a scan of more than one coordinate", () => {
        const profile = scanProfile({ ...SCAN, summary: { ...SCAN.summary, dimension: 2 } })
        expect(profile.hasCoordinate).toBe(false)
        expect(profile.xTitle).toBe("point number (a 2-D scan has no single coordinate axis)")
    })
})
