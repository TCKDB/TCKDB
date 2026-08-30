import { describe, expect, it } from "vitest"
import { ANGSTROM_TO_BOHR, BOHR_RADIUS_ANGSTROM, angstromToBohr, atomicNumberForSymbol, buildXyzBlock } from "./geometryXyz"

describe("buildXyzBlock", () => {
    it("pins the exact XYZ text built from atom rows — catches a swapped field or a dropped atom", () => {
        // Deliberately non-symmetric x/y/z per atom so a field swap
        // (e.g. writing atom.y where atom.z belongs) changes the output.
        const block = buildXyzBlock([
            { atom_index: 1, element: "C", x: 1, y: 2, z: 3 },
            { atom_index: 2, element: "O", x: -4, y: 5.5, z: 0 },
        ])
        expect(block).toBe("2\n\nC 1 2 3\nO -4 5.5 0")
    })

    it("counts atoms into the header line from the same array it lists — catches a hardcoded or off-by-one count", () => {
        const block = buildXyzBlock([
            { atom_index: 1, element: "N", x: 0, y: 0, z: 0 },
            { atom_index: 2, element: "N", x: 0, y: 0, z: 1 },
            { atom_index: 3, element: "N", x: 0, y: 0, z: 2 },
        ])
        expect(block.split("\n")[0]).toBe("3")
    })

    it("returns an empty atom list as a zero-count header with no trailing atom lines", () => {
        expect(buildXyzBlock([])).toBe("0\n\n")
    })
})

describe("angstromToBohr", () => {
    it("multiplies by the CODATA 2018 Å→bohr factor (reciprocal of the 0.529177210903 Å Bohr radius) — not divides, not a different constant", () => {
        // Pinned against the exact NIST CODATA 2018 value so a mutation
        // that inverts the factor (multiply becomes divide) or swaps in a
        // different constant (e.g. an older CODATA year) is observable:
        // both would still produce "a number that looks plausible" but
        // not this one.
        expect(BOHR_RADIUS_ANGSTROM).toBe(0.529177210903)
        expect(ANGSTROM_TO_BOHR).toBeCloseTo(1.8897261246, 10)
        expect(angstromToBohr(1)).toBeCloseTo(1.8897261246, 9)
    })

    it("converts a realistic bond-length value consistently with the factor, and 0 stays 0", () => {
        // 1.09 Å is a typical C-H bond length — chosen so a wrong-axis or
        // no-op mutation (returning the input unchanged) is distinguishable
        // from a correctly converted value, not just "some number".
        expect(angstromToBohr(1.09)).toBeCloseTo(1.09 * ANGSTROM_TO_BOHR, 12)
        expect(angstromToBohr(0)).toBe(0)
    })

    it("negative coordinates convert with the same factor, not a sign flip", () => {
        expect(angstromToBohr(-0.63)).toBeCloseTo(-0.63 * ANGSTROM_TO_BOHR, 12)
    })
})

describe("atomicNumberForSymbol", () => {
    it("maps common symbols to their correct atomic number, not an off-by-one", () => {
        expect(atomicNumberForSymbol("H")).toBe(1)
        expect(atomicNumberForSymbol("C")).toBe(6)
        expect(atomicNumberForSymbol("O")).toBe(8)
        expect(atomicNumberForSymbol("Cl")).toBe(17)
    })

    it("returns null — never 0 — for a symbol it does not recognise", () => {
        // `null` is checked with `toBeNull`, not a falsy check, so a
        // mutation that returns `0` for an unrecognised symbol (0 is not
        // a valid atomic number, but is falsy like null) is caught.
        expect(atomicNumberForSymbol("Xx")).toBeNull()
        expect(atomicNumberForSymbol("")).toBeNull()
    })

    it("is case-sensitive, matching this archive's own symbol casing", () => {
        expect(atomicNumberForSymbol("na")).toBeNull()
        expect(atomicNumberForSymbol("NA")).toBeNull()
        expect(atomicNumberForSymbol("Na")).toBe(11)
    })
})
