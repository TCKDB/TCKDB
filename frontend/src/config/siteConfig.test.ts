import { describe, expect, it } from "vitest"
import { parseHomeExample } from "./siteConfig"

const SPE = "spe_abcdefghijklmnopqrstuvwxyz"
const RXE = "rxe_234567abcdefghijklmnopqrst"

describe("parseHomeExample", () => {
    it("names no example when the setting is unset or empty", () => {
        expect(parseHomeExample(undefined)).toBeNull()
        expect(parseHomeExample("")).toBeNull()
        expect(parseHomeExample("   ")).toBeNull()
    })

    it.each([
        ["a molecule ref that is too short", "spe_abc"],
        ["a molecule ref one character too long", `${SPE}a`],
        ["a ref with characters outside base32", "spe_abcdefghijklmnopqrstuvwx10"],
        ["an uppercase ref", SPE.toUpperCase()],
        ["a species ref, which is not an entry", "spc_abcdefghijklmnopqrstuvwxyz"],
        ["a reaction ref, which is not an entry", "rxn_abcdefghijklmnopqrstuvwxyz"],
        ["a path", `/species-entries/${SPE}`],
        ["a ref with trailing text", `${SPE} extra`],
    ])("names no example for %s", (_name, value) => {
        expect(parseHomeExample(value)).toBeNull()
    })

    it("accepts a molecule entry ref and a reaction entry ref", () => {
        expect(parseHomeExample(SPE)).toEqual({ kind: "molecule", ref: SPE })
        expect(parseHomeExample(RXE)).toEqual({ kind: "reaction", ref: RXE })
    })

    it("ignores surrounding whitespace", () => {
        expect(parseHomeExample(`  ${SPE}\n`)).toEqual({ kind: "molecule", ref: SPE })
    })

    it("names no example for a value that is not text", () => {
        expect(parseHomeExample(42)).toBeNull()
        expect(parseHomeExample(null)).toBeNull()
    })
})
