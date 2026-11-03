import { describe, expect, it } from "vitest"
import {
    channelHandle,
    opaqueHandle,
    publicChemistryKey,
    publicSmiles,
    publicSpeciesEntryLabel,
    publicStateLabel,
    publicTermSymbol,
} from "./publicText"

describe("publicSpeciesEntryLabel", () => {
    it("keeps the computed tokens the server joins into the label", () => {
        expect(publicSpeciesEntryLabel("E")).toBe("E")
        expect(publicSpeciesEntryLabel("R,S partial")).toBe("R,S partial")
        expect(publicSpeciesEntryLabel("? unresolved")).toBe("? unresolved")
        expect(publicSpeciesEntryLabel("Ra")).toBe("Ra")
        expect(publicSpeciesEntryLabel("excited 3Σg- [2H]C([2H])([2H])O")).toBe("excited 3Σg- [2H]C([2H])([2H])O")
    })

    it("drops a depositor-typed electronic state label or term symbol", () => {
        expect(publicSpeciesEntryLabel("excited DEPOSITOR_SENTINEL_state_label")).toBe("excited")
        expect(publicSpeciesEntryLabel("T1 DEPOSITOR_SENTINEL_term")).toBeNull()
        expect(publicSpeciesEntryLabel("this is not a label")).toBeNull()
    })

    it("returns null for nothing", () => {
        expect(publicSpeciesEntryLabel(null)).toBeNull()
        expect(publicSpeciesEntryLabel(undefined)).toBeNull()
        expect(publicSpeciesEntryLabel("")).toBeNull()
    })
})

describe("publicTermSymbol", () => {
    it("accepts a term symbol shape and rejects anything else", () => {
        expect(publicTermSymbol("3Σg-")).toBe("3Σg-")
        expect(publicTermSymbol("2P")).toBe("2P")
        expect(publicTermSymbol("3B1")).toBe("3B1")
        expect(publicTermSymbol("T1")).toBeNull()
        expect(publicTermSymbol("DEPOSITOR_SENTINEL_term")).toBeNull()
        expect(publicTermSymbol("hello world")).toBeNull()
        expect(publicTermSymbol(null)).toBeNull()
    })
})

describe("publicSmiles", () => {
    it("accepts SMILES and reaction SMILES", () => {
        expect(publicSmiles("C1=C[C]2C=CCC2C=C1>>C1=CC2=C(C=C1)CC=C2.[H]")).toContain(">>")
        expect(publicSmiles("[N-]=[NH2+]")).toBe("[N-]=[NH2+]")
        expect(publicSmiles("[C@@H](F)(Cl)Br")).toBe("[C@@H](F)(Cl)Br")
    })

    it("rejects text that is not SMILES, including text hidden in a bracket atom", () => {
        expect(publicSmiles("DEPOSITOR_SENTINEL_smiles")).toBeNull()
        expect(publicSmiles("[DEPOSITOR_SENTINEL]")).toBeNull()
        expect(publicSmiles("click here")).toBeNull()
        expect(publicSmiles("")).toBeNull()
    })
})

describe("publicChemistryKey", () => {
    it("prints an element or bond key and hides any other string", () => {
        expect(publicChemistryKey("C")).toBe("C")
        expect(publicChemistryKey("C-H")).toBe("C-H")
        expect(publicChemistryKey("N#N")).toBe("N#N")
        expect(publicChemistryKey("DEPOSITOR_SENTINEL_target")).toBe("unrecognised key")
    })
})

describe("publicStateLabel", () => {
    const participant = (smiles: string, label: string | null, stoichiometry = 1) => ({
        canonical_smiles: smiles, species_entry_label: label, stoichiometry,
    })

    it("rebuilds the label from participants with the typed parts removed", () => {
        const label = publicStateLabel({
            participants: [participant("N=N", "E excited DEPOSITOR_SENTINEL_x"), participant("[H][H]", null, 2)],
            participants_truncated: true,
            state_label: "N=N (E excited DEPOSITOR_SENTINEL_x) + 2 [H][H] + ...",
        })
        expect(label).toBe("N=N (E excited) + 2 [H][H] + ...")
    })

    it("keeps the served label when there are no participants to rebuild from", () => {
        expect(publicStateLabel({ participants: [], state_label: "NN" })).toBe("NN")
    })
})

describe("opaqueHandle", () => {
    it("is stable, carries none of the original characters, and tells keys apart", () => {
        expect(opaqueHandle("channel_1")).toBe(opaqueHandle("channel_1"))
        expect(opaqueHandle("channel_1")).not.toBe(opaqueHandle("channel_2"))
        expect(opaqueHandle("channel_1")).toMatch(/^k[0-9a-f]{8}$/)
        expect(channelHandle(null)).toBeUndefined()
        expect(channelHandle("channel_1")).toBe(opaqueHandle("channel_1"))
    })
})
