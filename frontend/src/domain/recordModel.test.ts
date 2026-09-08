import { describe, expect, it } from "vitest"
import { classifyIdentifier, resultPath } from "./recordModel"

// A syntactically valid public-reference body: exactly 26 lowercase base32
// characters (a-z, 2-7), matching `PUBLIC_REF_BODY_LEN` in
// `app/services/public_refs.py` -- every prefix in this file's fixtures
// uses this same body, since the classifier's ref-shape check does not
// care about its content, only its shape.
const REF_BODY = "aaaaaaaaaaaaaaaaaaaaaaaaaa"

describe("classifyIdentifier", () => {
    it.each([
        ["H2O", "formula"], ["Cl2", "formula"], ["Ca", "formula"], ["Cr", "formula"],
        ["spc_abcde234567abcde234567abcd", "species-ref"], ["spe_bcdef234567bcdef234567abcd", "species-entry-ref"],
        ["InChI=1S/H2O/h1H2", "inchi"], ["XLYOFNOQVPJJNP-UHFFFAOYSA-N", "inchi-key"],
    ])("classifies %s", (value, kind) => {
        const result = classifyIdentifier(value)
        expect(result.valid && result.identifier.kind).toBe(kind)
    })

    // The owner-reported gap this module exists to close: `rxn_`/`rxe_`/
    // `tse_` (at least) must be recognised, not misread as a structure
    // query. Extended to every prefix `App.tsx` has a route for --
    // `routedPublicRefPrefixes` in `recordModel.ts` is the authoritative
    // list, this table exercises all of it so a future prefix added there
    // without a matching route entry (or vice versa) shows up as a gap.
    it.each([
        ["rxn", "/reactions"], ["rxe", "/reaction-entries"], ["tse", "/transition-state-entries"],
        ["cg", "/conformer-groups"], ["co", "/conformer-observations"], ["calc", "/calculations"], ["geom", "/geometries"],
    ])("classifies a recognized %s_ reference as a direct navigation, never a structure query", (prefix, routePrefix) => {
        const value = `${prefix}_${REF_BODY}`
        const result = classifyIdentifier(value)
        expect(result.valid).toBe(true)
        if (!result.valid) return
        expect(result.identifier.kind).toBe("record-ref")
        expect(result.identifier.kind === "record-ref" && result.identifier.path).toBe(`${routePrefix}/${value}`)
    })

    // A syntactically valid reference to a record type this frontend has
    // no page for (e.g. `thm_` -- thermo) is real (the backend would
    // resolve it), but must say so plainly -- never silently fall through
    // to a structure search, which is the exact defect this module exists
    // to fix for the routed prefixes above.
    it.each(["thm", "kin", "sm", "trn", "lot", "sub", "ts"])(
        "gives a clear, non-structure-search message for a recognized-but-unrouted %s_ reference",
        (prefix) => {
            const value = `${prefix}_${REF_BODY}`
            const result = classifyIdentifier(value)
            expect(result.valid).toBe(false)
            if (result.valid) return
            expect(result.message).toContain("does not have a page for that record type yet")
            expect(result.message).not.toMatch(/SMILES/i)
        },
    )

    // A ref-shaped string (right body length/alphabet) whose prefix is not
    // one the archive mints at all -- still must not be sent to structure
    // search as though it were a SMILES string.
    it("gives a clear message for a ref-shaped string with an unrecognized prefix, not a structure search", () => {
        const result = classifyIdentifier(`zzz_${REF_BODY}`)
        expect(result.valid).toBe(false)
        if (result.valid) return
        expect(result.message).toContain("not a reference prefix this archive recognizes")
    })

    it("explains empty and unsupported whitespace input", () => {
        expect(classifyIdentifier("").valid).toBe(false)
        expect(classifyIdentifier("ethyl alcohol").valid).toBe(false)
    })

    it("rejects malformed element tokens and malformed public references", () => {
        expect(classifyIdentifier("Xx2").valid).toBe(false)
        expect(classifyIdentifier("H02").valid).toBe(false)
        expect(classifyIdentifier("spc_not-a-ref").valid).toBe(false)
        const uppercase = classifyIdentifier("spc_ABCde234567abcde234567abcd")
        const zero = classifyIdentifier("spe_abcde034567abcde234567abcd")
        expect(uppercase.valid).toBe(false)
        expect(zero.valid).toBe(false)
        if (!uppercase.valid) expect(uppercase.message).toContain("26 lowercase base32 characters (a-z, 2-7)")
        if (!zero.valid) expect(zero.message).toContain("26 lowercase base32 characters (a-z, 2-7)")
    })

    it("requires a deterministic prefix for formula/SMILES ambiguity", () => {
        expect(classifyIdentifier("CCO").valid).toBe(false)
        expect(classifyIdentifier("Cl").valid).toBe(false)
        expect(classifyIdentifier("formula:Cl").valid).toBe(true)
        expect(classifyIdentifier("smiles:CCO").valid).toBe(true)
    })

    it("routes a match to its stable species-entry record", () => {
        expect(resultPath({ speciesRef: "spc_abcde234567abcde234567abcd", entryRef: "spe_bcdef234567bcdef234567abcd" }))
            .toBe("/species-entries/spe_bcdef234567bcdef234567abcd")
        expect(resultPath({ speciesRef: "spc_abcde234567abcde234567abcd" })).toBe("/species/spc_abcde234567abcde234567abcd")
    })
})
