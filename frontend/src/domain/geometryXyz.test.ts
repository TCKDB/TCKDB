import { describe, expect, it } from "vitest"
import { buildXyzBlock } from "./geometryXyz"

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
