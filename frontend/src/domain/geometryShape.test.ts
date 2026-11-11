import { describe, expect, it } from "vitest"
import { conformerGroupName } from "./conformerEvidence"
import { elementCounts, producedByPhrase, shapeSummary } from "./geometryShape"

const atom = (atom_index: number, element: string, x: number, y: number, z: number, isotope_mass_number?: number) => (
    { atom_index, element, x, y, z, ...(isotope_mass_number === undefined ? {} : { isotope_mass_number }) }
)

describe("shapeSummary", () => {
    it("says nothing for no atoms", () => {
        expect(shapeSummary([])).toBeNull()
    })

    it("names one atom", () => {
        expect(shapeSummary([atom(1, "O", 0, 0, 0)])).toBe("1 atom, O.")
    })

    it("gives the two atoms and how far apart they are", () => {
        expect(shapeSummary([atom(1, "O", 0, 0, 0), atom(2, "H", 0, 0, 0.98)])).toBe("2 atoms, O and H, 0.98 Å apart.")
    })

    it("measures the distance in three dimensions", () => {
        expect(shapeSummary([atom(1, "C", 0, 0, 0), atom(2, "O", 0.6, 0.8, 1.2)])).toBe("2 atoms, C and O, 1.56 Å apart.")
    })

    it("gives a larger molecule's atoms per element in Hill order and the bonds drawn", () => {
        const methane = [
            atom(1, "C", 0, 0, 0), atom(2, "H", 0.63, 0.63, 0.63), atom(3, "H", -0.63, -0.63, 0.63),
            atom(4, "H", -0.63, 0.63, -0.63), atom(5, "H", 0.63, -0.63, -0.63),
        ]
        expect(shapeSummary(methane)).toBe("5 atoms: 1 C and 4 H, with 4 bonds drawn.")
    })

    it("lists three or more elements with commas and an 'and'", () => {
        expect(elementCounts([atom(1, "O", 0, 0, 0), atom(2, "C", 1, 0, 0), atom(3, "H", 2, 0, 0), atom(4, "H", 3, 0, 0)])).toBe("1 C, 2 H and 1 O")
    })

    it("writes an isotope with its mass number", () => {
        expect(shapeSummary([atom(1, "H", 0, 0, 0, 2), atom(2, "H", 0, 0, 0.74)])).toBe("2 atoms, ²H and H, 0.74 Å apart.")
    })

    it("says bonds are not drawn for a very large molecule", () => {
        const many = Array.from({ length: 401 }, (_, index) => atom(index + 1, "C", index * 1.5, 0, 0))
        expect(shapeSummary(many)).toBe("401 atoms: 401 C. Bonds are not drawn for molecules this large.")
    })
})

describe("producedByPhrase", () => {
    it("is null when nothing produced the geometry", () => {
        expect(producedByPhrase([])).toBeNull()
    })

    it("gives the role and the kind of the one producer", () => {
        expect(producedByPhrase([{ calculation_type: "opt", role: "final" }])).toBe("final, from an optimisation")
    })

    it("leaves the role out when none is recorded", () => {
        expect(producedByPhrase([{ calculation_type: "freq", role: null }])).toBe("from a frequency calculation")
    })

    it("counts the producers when there are several, and never picks one", () => {
        expect(producedByPhrase([
            { calculation_type: "opt", role: "final" }, { calculation_type: "opt", role: "coarse" },
        ])).toBe("from 2 calculations")
    })

    it("spells out a type this build has no wording for", () => {
        expect(producedByPhrase([{ calculation_type: "new_kind", role: "final" }])).toBe("final, from a new kind calculation")
    })
})

describe("conformerGroupName", () => {
    it("words the archive's own numbering", () => {
        expect(conformerGroupName("conformer_3")).toBe("Conformer Group 3")
    })

    it("never prints any other label", () => {
        expect(conformerGroupName("my favourite")).toBeNull()
        expect(conformerGroupName("conformer_3 extra")).toBeNull()
        expect(conformerGroupName("")).toBeNull()
        expect(conformerGroupName(null)).toBeNull()
    })
})
