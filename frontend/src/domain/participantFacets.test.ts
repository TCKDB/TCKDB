import { describe, expect, it } from "vitest"
import { participantChips, publicStateLabel } from "./participantFacets"

describe("participantChips", () => {
    it("reads the structured stereo facets, leaving out the default kind and ground state", () => {
        expect(participantChips({ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "Z", stereo_descriptor: "Z" })).toEqual(["Z isomer"])
        expect(participantChips({ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "R", stereo_descriptor: "R" })).toEqual(["R enantiomer"])
        expect(participantChips({ species_entry_kind: "minimum", electronic_state_kind: "excited", isotope_key: "[2H]C" })).toEqual(["excited state", "isotopologue [2H]C"])
    })

    it("gives no chips for an achiral ground-state entry, or when the facets were not served", () => {
        expect(participantChips({ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_descriptor: null })).toEqual([])
        expect(participantChips({})).toEqual([])
        expect(participantChips(null)).toEqual([])
    })

    it("ignores anything that is not a structured facet: a typed R in a state label cannot become 'R enantiomer'", () => {
        const withTyped = { species_entry_kind: "minimum", electronic_state_kind: "ground", electronic_state_label: "R", term_symbol: "R", species_entry_label: "R" }
        expect(participantChips(withTyped)).toEqual([])
    })
})

describe("publicStateLabel", () => {
    const participant = (smiles: string, facets: Record<string, string | null> = {}, stoichiometry = 1) => ({
        canonical_smiles: smiles, stoichiometry, species_entry_kind: "minimum", electronic_state_kind: "ground", ...facets,
    })

    it("tells the Z and E diazene states apart, from structured facets", () => {
        const z = publicStateLabel({ participants: [participant("N=N", { stereo_label: "Z", stereo_descriptor: "Z" }), participant("[H][H]")], state_label: "ignored" })
        const e = publicStateLabel({ participants: [participant("N=N", { stereo_label: "E", stereo_descriptor: "E" }), participant("[H][H]")], state_label: "ignored" })
        expect(z).toBe("N=N (Z isomer) + [H][H]")
        expect(e).toBe("N=N (E isomer) + [H][H]")
    })

    it("never uses the served label, and says so when truncated", () => {
        expect(publicStateLabel({
            participants: [participant("NN"), participant("[H][H]", {}, 2)],
            participants_truncated: true,
            state_label: "NN (R DEPOSITOR_SENTINEL_x) + ...",
        })).toBe("NN + 2 [H][H] + ...")
        expect(publicStateLabel({ participants: [], state_label: "NN" })).toBe("")
    })
})
