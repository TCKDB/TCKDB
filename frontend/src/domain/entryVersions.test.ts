import { describe, expect, it } from "vitest"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { versionRowName } from "./entryVersions"

const entry = (extra: Record<string, unknown> = {}) => ({
    species_entry_ref: "spe_a", species_entry_kind: "minimum", electronic_state_kind: "ground", isotope_key: null, stereo_label: null,
    ...extra,
}) as unknown as ScientificSpeciesEntrySummary

describe("versionRowName", () => {
    it("names a plain ground-state minimum by its position alone", () => {
        expect(versionRowName(entry(), 2)).toBe("Version 2")
    })

    it("keeps the facts of a version that is not a plain ground-state minimum", () => {
        expect(versionRowName(entry({ electronic_state_kind: "excited" }), 3)).toMatch(/^Version 3 · minimum · excited state/)
        expect(versionRowName(entry({ species_entry_kind: "vdw_complex" }), 1)).toMatch(/^Version 1 · /)
        expect(versionRowName(entry({ isotope_key: "2H" }), 1)).toMatch(/^Version 1 · /)
    })
})
