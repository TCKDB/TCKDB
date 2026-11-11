import { describe, expect, it } from "vitest"
import { isValidNameQuery, looksLikeName, nameQueryKey } from "./nameQuery"

describe("name or exact identifier", () => {
    const table: [string, boolean][] = [
        ["water", true], ["Water", true], ["DMSO", true], ["acetic acid", true], ["Hydrogen Peroxide", true],
        ["CO", false], ["NO", false], ["HCl", false], ["C2H6", false], ["[CH3]", false], ["[C", false], ["[OH", false],
        ["CCO", false], ["CC(=O)O", false], ["InChI=1S/H2O/h1H2", false], ["InChIKey", false], ["rx", false], ["sp", false],
        ["spc_abc", false], ["smiles:water", false], ["Hünig’s base", true],
        ["benzo[a]pyrene", true], ["Bicyclo[2.2.1]heptane", true], ["dibenz[a,h]anthracene", true], ["C[C", false], ["[CH3],[OH", false],
    ]
    it.each(table)("%s -> name: %s", (text, isName) => {
        expect(looksLikeName(text)).toBe(isName)
    })
})

describe("name query rule", () => {
    it("accepts typographic apostrophes and combining marks", () => {
        expect(isValidNameQuery("Hünig’s base")).toBe(true)
        expect(isValidNameQuery("Hünig`s base")).toBe(true)
        expect(nameQueryKey("Hünig’s  Base")).toBe("hünig's base")
        expect(nameQueryKey("Straße")).toBe("strasse")
    })
    it("rejects the wrong length, characters and letterless text", () => {
        for (const text of ["w", "", "w".repeat(65), "a<b", "a?", "--", " . "]) expect(isValidNameQuery(text), text).toBe(false)
    })
})
