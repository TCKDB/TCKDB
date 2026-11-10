import { describe, expect, it } from "vitest"
import { layoutPoster, type PosterAtom } from "./molPosterLayout"

const METHANE: PosterAtom[] = [
    { element: "C", x: 0, y: 0, z: 0 },
    { element: "H", x: 0.63, y: 0.63, z: 0.63 },
    { element: "H", x: -0.63, y: -0.63, z: 0.63 },
    { element: "H", x: -0.63, y: 0.63, z: -0.63 },
    { element: "H", x: 0.63, y: -0.63, z: -0.63 },
]

describe("layoutPoster", () => {
    it("gives the same layout for the same coordinates", () => {
        expect(layoutPoster(METHANE, 240)).toEqual(layoutPoster(METHANE, 240))
    })

    it("infers one bond per C-H pair and none between the hydrogens", () => {
        const { bonds } = layoutPoster(METHANE, 240)
        expect(bonds).toHaveLength(4)
        expect(bonds.every((bond) => bond.a === 0 || bond.b === 0)).toBe(true)
    })

    it("keeps every atom inside the square", () => {
        const { spheres } = layoutPoster(METHANE, 240)
        expect(spheres).toHaveLength(5)
        for (const sphere of spheres) {
            expect(sphere.cx - sphere.r).toBeGreaterThanOrEqual(0)
            expect(sphere.cx + sphere.r).toBeLessThanOrEqual(240)
            expect(sphere.cy - sphere.r).toBeGreaterThanOrEqual(0)
            expect(sphere.cy + sphere.r).toBeLessThanOrEqual(240)
        }
    })

    it("handles one atom and no atoms", () => {
        expect(layoutPoster([], 240)).toEqual({ spheres: [], bonds: [] })
        const single = layoutPoster([{ element: "O", x: 1, y: 2, z: 3 }], 240)
        expect(single.spheres[0].cx).toBeCloseTo(120)
        expect(single.bonds).toEqual([])
    })

    it("does not depend on where the molecule sits in space", () => {
        const moved = METHANE.map((atom) => ({ ...atom, x: atom.x + 10, y: atom.y - 4, z: atom.z + 7 }))
        const a = layoutPoster(METHANE, 240).spheres.map((s) => [Math.round(s.cx), Math.round(s.cy)])
        const b = layoutPoster(moved, 240).spheres.map((s) => [Math.round(s.cx), Math.round(s.cy)])
        expect(b).toEqual(a)
    })
})
