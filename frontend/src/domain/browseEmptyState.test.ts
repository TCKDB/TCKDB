import { describe, expect, it } from "vitest"
import { archiveEmptyMessage, filteredEmptyMessage, pagedPastEndMessage } from "./browseEmptyState"

/**
 * Direct unit coverage for the "reaction" kind's own empty-state copy (PR
 * 4b) -- the three functions this module exports were previously only
 * exercised indirectly, through rendered text in `BrowsePage.test.tsx`.
 * Each assertion pins the archive-fact framing (never "none exist", the
 * house rule against asserting from absence) and, for `archiveEmptyMessage`
 * specifically, that "reaction" gets its OWN sentence rather than silently
 * reusing another kind's copy.
 */
describe("browseEmptyState: the \"reaction\" kind", () => {
    it("archiveEmptyMessage names reaction entries, not a generic or borrowed noun", () => {
        const message = archiveEmptyMessage("reaction")
        expect(message).toBe("No reaction entries have been deposited in this archive yet.")
    })

    it("archiveEmptyMessage for 'reaction' is a DIFFERENT string than for 'species'/'vdw'/'transition_state'", () => {
        const reaction = archiveEmptyMessage("reaction")
        expect(reaction).not.toBe(archiveEmptyMessage("species"))
        expect(reaction).not.toBe(archiveEmptyMessage("vdw"))
        expect(reaction).not.toBe(archiveEmptyMessage("transition_state"))
    })

    it("filteredEmptyMessage names reaction entries and reads as a NARROWING, not an archive fact", () => {
        const message = filteredEmptyMessage("reaction")
        expect(message).toBe("No reaction entries match these filters. Clear or widen them to see more of the archive.")
        // Distinct from the archive-empty wording -- the same string here
        // would collapse the two failure reasons `BrowsePage` deliberately
        // keeps apart.
        expect(message).not.toBe(archiveEmptyMessage("reaction"))
    })

    it("pagedPastEndMessage names reaction entries and reads as neither absence nor narrowing", () => {
        const message = pagedPastEndMessage("reaction")
        expect(message).toBe("That is past the end of the reaction entries this listing has. Go back to see the rest of the archive.")
        expect(message).not.toBe(archiveEmptyMessage("reaction"))
        expect(message).not.toBe(filteredEmptyMessage("reaction"))
    })
})
