import { describe, expect, it } from "vitest"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { entryLede } from "./entryLede"
import { numbersOrderSentence, pointGroupParts } from "./entryNumbers"
import { THERMO_MODEL_KIND_WORDS } from "./thermoModelKind"
import { orderWords } from "./paging"

const sort = (...keys: [string, string, string][]) => ({ keys: keys.map(([key, direction, meaning]) => ({ key, direction, meaning })) })
const reviewThenNewest = sort(["review_rank", "asc", "review status"], ["created_at", "desc", "newest deposited"], ["id", "desc", "unique tie-breaker"])

describe("pointGroupParts", () => {
    it("subscripts the whole suffix", () => {
        expect(pointGroupParts("D3h")).toEqual({ symbol: "D", subscript: "3h" })
        expect(pointGroupParts("C2v")).toEqual({ symbol: "C", subscript: "2v" })
        expect(pointGroupParts("Cinfv")).toEqual({ symbol: "C", subscript: "∞v" })
        expect(pointGroupParts("C∞v")).toEqual({ symbol: "C", subscript: "∞v" })
        expect(pointGroupParts("Td")).toEqual({ symbol: "T", subscript: "d" })
    })
})

describe("orderWords and numbersOrderSentence", () => {
    it("words the keys without the tie-breaker", () => {
        expect(orderWords(reviewThenNewest)).toBe("approved first, then newest deposited first")
        expect(orderWords(null)).toBeNull()
    })

    it("says identical orders once and names only the lists given", () => {
        expect(numbersOrderSentence([{ kind: "thermo", sort: reviewThenNewest }, { kind: "transport", sort: reviewThenNewest }]))
            .toBe("Thermochemistry first, then transport; inside each, approved first, then newest deposited first.")
        expect(numbersOrderSentence([{ kind: "statmech", sort: reviewThenNewest }]))
            .toBe("Statistical mechanics: approved first, then newest deposited first.")
    })

    it("spells out lists whose orders differ or are not stated", () => {
        expect(numbersOrderSentence([{ kind: "thermo", sort: reviewThenNewest }, { kind: "statmech", sort: null }]))
            .toBe("Thermochemistry first, then statistical mechanics. Thermochemistry: approved first, then newest deposited first. Statistical mechanics: the archive did not state an order.")
    })
})

describe("entryLede", () => {
    const entry = (multiplicity: number, state = "ground") => ({
        charge: -1, multiplicity, electronic_state_kind: state, species_entry_kind: "minimum",
    }) as unknown as SpeciesEntryProjection

    it("words a recorded multiplicity, falling back to the number, never to 'not recorded'", () => {
        expect(entryLede(entry(3))).toContain("charge −1, triplet, ground state")
        expect(entryLede(entry(9))).toContain("multiplicity 9")
        expect(entryLede(entry(9, "doubly_excited"))).toContain("doubly excited")
        expect(entryLede(entry(9))).not.toContain("not recorded")
    })
})

describe("thermo model kinds", () => {
    it("are keyed on the values the read API sends, not on the database enum", () => {
        // `thermo.py` sends `nasa` for a NASA 7 record and `points` for a tabulated one; `EntryThermoSection` picks its model block by the same values.
        expect(Object.keys(THERMO_MODEL_KIND_WORDS)).toEqual(["nasa", "nasa9", "wilhoit", "points", "scalar"])
        expect(THERMO_MODEL_KIND_WORDS).not.toHaveProperty("nasa7")
        expect(THERMO_MODEL_KIND_WORDS).not.toHaveProperty("tabulated")
    })
})
