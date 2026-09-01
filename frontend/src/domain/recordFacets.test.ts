import { describe, expect, it } from "vitest"
import { facetChips } from "./recordFacets"

describe("facetChips", () => {
    it("renders the R-enantiomer entry as three chips, not a bare 'R' heading", () => {
        // spc_n7c5snosejeow4z2vr4aivmv34: the record measured against the
        // live archive whose heading rendered as a bare "R". Its
        // `species_entry_label` really is `"R"` on the wire (the backend's
        // own compact discriminator, see the module docstring) -- this
        // fixture does not need to fake that away, because `facetChips`
        // never reads `species_entry_label` in the first place.
        const chips = facetChips({
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            stereo_label: "R",
        })
        expect(chips).toEqual(["minimum", "ground state", "R enantiomer"])
    })

    it("omits a chip for every unset axis -- no placeholder, no 'none' pill", () => {
        // One axis set (term symbol, folded into the state chip), one axis
        // left unset (stereochemistry) -- the stereo axis must contribute
        // nothing, not a chip that says "none" or "achiral".
        const chips = facetChips({
            species_entry_kind: "minimum",
            electronic_state_kind: "excited",
            term_symbol: "T1",
            stereo_label: null,
        })
        expect(chips).toEqual(["minimum", "excited state · T1"])
        expect(chips.some((chip) => /stereo/i.test(chip))).toBe(false)
        expect(chips.some((chip) => chip === "R enantiomer" || chip === "S enantiomer")).toBe(false)
    })

    it("labels R and S as enantiomers, E and Z as isomers", () => {
        expect(facetChips({ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "S" }))
            .toContain("S enantiomer")
        expect(facetChips({ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "Z" }))
            .toContain("Z isomer")
    })

    it("folds electronic_state_label and term_symbol into the state chip when set", () => {
        const chips = facetChips({
            species_entry_kind: "minimum",
            electronic_state_kind: "excited",
            electronic_state_label: "T1",
        })
        expect(chips).toContain("excited state · T1")
    })

    it("adds an isotopologue chip only when isotope_key is set", () => {
        const withIsotope = facetChips({
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            isotope_key: "13C1",
        })
        expect(withIsotope).toContain("isotopologue 13C1")

        const withoutIsotope = facetChips({ species_entry_kind: "minimum", electronic_state_kind: "ground" })
        expect(withoutIsotope.some((chip) => chip.startsWith("isotopologue"))).toBe(false)
    })

    it("renders a plain ground-state minimum as exactly two chips", () => {
        expect(facetChips({ species_entry_kind: "minimum", electronic_state_kind: "ground" }))
            .toEqual(["minimum", "ground state"])
    })

    it("names a vdw_complex in words, not as a raw enum token", () => {
        expect(facetChips({ species_entry_kind: "vdw_complex", electronic_state_kind: "ground" })[0])
            .toBe("van der Waals complex")
    })
})

describe("facetChips: includeState (grouped-card redundancy)", () => {
    it("drops the bare state phrase when includeState is false", () => {
        const chips = facetChips(
            { species_entry_kind: "minimum", electronic_state_kind: "ground" },
            { includeState: false },
        )
        expect(chips).toEqual(["minimum"])
        expect(chips.some((chip) => /ground/i.test(chip))).toBe(false)
    })

    it("still surfaces electronic_state_label/term_symbol when includeState is false -- those are not established by a group heading that only names the bare state", () => {
        const chips = facetChips(
            { species_entry_kind: "minimum", electronic_state_kind: "excited", term_symbol: "T1" },
            { includeState: false },
        )
        expect(chips).toEqual(["minimum", "T1"])
    })

    it("still surfaces stereochemistry and isotopologue when includeState is false", () => {
        const chips = facetChips(
            { species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "R", isotope_key: "13C1" },
            { includeState: false },
        )
        expect(chips).toEqual(["minimum", "R enantiomer", "isotopologue 13C1"])
    })

    it("defaults to includeState: true when the option is omitted entirely", () => {
        expect(facetChips({ species_entry_kind: "minimum", electronic_state_kind: "ground" }))
            .toEqual(["minimum", "ground state"])
    })
})
