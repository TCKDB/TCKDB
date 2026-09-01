import { describe, expect, it } from "vitest"
import { identityFromCalculationOwner, identityFromGeometry } from "./recordIdentity"

describe("identityFromGeometry", () => {
    it("resolves a known species_entry owner, carrying the served formula", () => {
        const result = identityFromGeometry({
            kind: "species_entry",
            species_entry: {
                species_ref: "spc_1", species_entry_ref: "spe_1", formula: "CH3",
                canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", charge: 0, multiplicity: 2,
            },
        })
        expect(result).toMatchObject({ kind: "species_entry", formula: "CH3", canonicalSmiles: "[CH3]" })
    })

    it("resolves a known transition_state_entry owner without ever inventing SMILES/InChIKey fields", () => {
        const result = identityFromGeometry({
            kind: "transition_state_entry",
            transition_state_entry: {
                transition_state_ref: "ts_1", transition_state_entry_ref: "tse_1",
                formula: null, unmapped_smiles: null, charge: 0, multiplicity: 2,
            },
        })
        expect(result.kind).toBe("transition_state_entry")
        expect(result).not.toHaveProperty("canonicalSmiles")
        expect(result).not.toHaveProperty("inchiKey")
    })

    it("resolves ambiguous_owners into the ambiguous case, never picking one owner", () => {
        const result = identityFromGeometry({
            kind: null,
            species_entry: null,
            transition_state_entry: null,
            ambiguous_owners: [{ kind: "species_entry", ref: "spe_a" }, { kind: "species_entry", ref: "spe_b" }],
        })
        expect(result).toEqual({
            kind: "ambiguous",
            owners: [{ kind: "species_entry", ref: "spe_a" }, { kind: "species_entry", ref: "spe_b" }],
        })
    })

    it("resolves kind:null with no ambiguous owners as absent, not ambiguous", () => {
        const result = identityFromGeometry({ kind: null, ambiguous_owners: [] })
        expect(result).toEqual({ kind: "absent" })
    })

    it("resolves a missing identity block as absent", () => {
        expect(identityFromGeometry(null)).toEqual({ kind: "absent" })
        expect(identityFromGeometry(undefined)).toEqual({ kind: "absent" })
    })
})

describe("identityFromCalculationOwner", () => {
    it("resolves a species_entry owner and never fabricates a formula the owner summary does not carry", () => {
        const result = identityFromCalculationOwner({
            kind: "species_entry",
            species_entry: {
                species_ref: "spc_1", species_entry_ref: "spe_1",
                canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", charge: 0, multiplicity: 2,
            },
        })
        expect(result).toMatchObject({ kind: "species_entry", formula: null, canonicalSmiles: "[CH3]" })
    })

    it("resolves a transition_state_entry owner", () => {
        const result = identityFromCalculationOwner({
            kind: "transition_state_entry",
            transition_state_entry: {
                transition_state_ref: "ts_1", transition_state_entry_ref: "tse_1", charge: 0, multiplicity: 1,
            },
        })
        expect(result.kind).toBe("transition_state_entry")
    })
})
