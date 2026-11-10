import { describe, expect, it } from "vitest"
import {
    channelHandle,
    opaqueHandle,
    publicChemistryKey,
    publicParameterValue,
    publicPointGroup,
    publicElectronicStateLabel,
    publicTermSymbol,
} from "./publicText"

describe("publicElectronicStateLabel", () => {
    it("accepts the label grammar the API enforces on write and rejects anything else", () => {
        for (const label of ["X", "A", "E", "Ã", "A″", "S1", "T1", "D0"]) expect(publicElectronicStateLabel(label)).toBe(label)
        for (const label of ["R", "S", "Z", "ground", "excited R,S", "DEPOSITOR_SENTINEL_label", "S100", "<b>", "", "  "]) {
            expect(publicElectronicStateLabel(label)).toBeNull()
        }
        expect(publicElectronicStateLabel(null)).toBeNull()
        expect(publicElectronicStateLabel(undefined)).toBeNull()
    })
})

describe("publicTermSymbol", () => {
    it("accepts the term-symbol grammar the API enforces on write and rejects anything else", () => {
        expect(publicTermSymbol("3Σg-")).toBe("3Σg-")
        expect(publicTermSymbol("²Π")).toBe("²Π")
        expect(publicTermSymbol("X ²Π")).toBe("X ²Π")
        expect(publicTermSymbol("2P")).toBe("2P")
        expect(publicTermSymbol("3B1")).toBe("3B1")
        expect(publicTermSymbol("T1")).toBeNull()
        expect(publicTermSymbol("DEPOSITOR_SENTINEL_term")).toBeNull()
        expect(publicTermSymbol("hello world")).toBeNull()
        expect(publicTermSymbol(null)).toBeNull()
    })
})

describe("publicChemistryKey", () => {
    it("prints an element or bond key and hides any other string", () => {
        expect(publicChemistryKey("C")).toBe("C")
        expect(publicChemistryKey("C-H")).toBe("C-H")
        expect(publicChemistryKey("N#N")).toBe("N#N")
        expect(publicChemistryKey("DEPOSITOR_SENTINEL_target")).toBe("unrecognised key")
        expect(publicChemistryKey("Fu-Ck")).toBe("unrecognised key")
        expect(publicChemistryKey("Xx")).toBe("unrecognised key")
        expect(publicChemistryKey("No-Ho-Es")).toBe("unrecognised key")
        expect(publicChemistryKey("C-Uu")).toBe("unrecognised key")
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

describe("publicParameterValue", () => {
    it("prints numbers and booleans, and says recorded for any other string", () => {
        expect(publicParameterValue("1e-8")).toBe("1e-8")
        expect(publicParameterValue("-0.5")).toBe("-0.5")
        expect(publicParameterValue("True")).toBe("True")
        expect(publicParameterValue("PISS")).toBe("recorded")
        expect(publicParameterValue(null)).toBe("recorded")
    })
})

describe("publicPointGroup", () => {
    it("accepts Schoenflies symbols and rejects any other string", () => {
        for (const group of ["C1", "Cs", "Ci", "C2v", "C3h", "D2d", "D6h", "S4", "Td", "Oh", "Ih", "Cinfv", "D*h", "C∞v"]) {
            expect(publicPointGroup(group)).toBe(group)
        }
        for (const junk of ["PISS", "C1 stinks", "Cx", "D1", "C", "DEPOSITOR_SENTINEL_point_group"]) {
            expect(publicPointGroup(junk)).toBeNull()
        }
    })
})
