import { describe, expect, it } from "vitest"
import { LINKABLE_RECORD_TYPES, recordRoute, recordTypeHasPage } from "./recordRoute"

/**
 * The defect this module exists to prevent is a link that 404s, and the
 * only way that happens is a prefix here disagreeing with a route in
 * `App.tsx`. So the central test below reads `App.tsx` and checks each
 * prefix against the routes actually declared there, rather than
 * restating the table -- a test that only repeated the mapping would pass
 * unchanged the day somebody renames `/reactions`.
 *
 * `import.meta.glob(..., "?raw")` is the technique `dead-css-class.test.ts`
 * already uses to read source as text under this vitest config.
 */
const APP_SOURCE = (
    import.meta.glob("../App.tsx", { query: "?raw", import: "default", eager: true }) as Record<
        string,
        string
    >
)["../App.tsx"]

/** Every `path="..."` declared in `App.tsx`, as written. */
const DECLARED_ROUTE_PATHS: string[] = Array.from(
    APP_SOURCE.matchAll(/<Route\s+path="([^"]+)"/g),
    (m) => m[1],
)

/**
 * The nine types the queue can link. Pinned as a set so that adding a
 * tenth is a deliberate edit here, not a silent widening -- each entry
 * carries a claim about another file that the test below then checks.
 */
const EXPECTED_LINKABLE = [
    "species",
    "species_entry",
    "conformer_group",
    "conformer_observation",
    "calculation",
    "reaction",
    "reaction_entry",
    "transition_state_entry",
    "network",
]

/**
 * `SubmissionRecordType` members with no page today. The module's own
 * docstring makes this claim; without it here, "returns null" tests would
 * be indistinguishable from "this type does not exist".
 */
const EXPECTED_UNLINKABLE = [
    "transition_state",
    "statmech",
    "thermo",
    "kinetics",
    "transport",
    "network_solve",
    "applied_energy_correction",
    "artifact",
]

describe("every link this module produces resolves to a real route", () => {
    it.each(EXPECTED_LINKABLE)("%s lands on a declared route", (recordType) => {
        const href = recordRoute(recordType, "ref_abc")
        expect(href).not.toBeNull()

        // The route a real click would match: the prefix plus one dynamic
        // segment. Compared against App.tsx's own text, so renaming a route
        // there without updating the table here fails HERE, not in a
        // curator's browser.
        const prefix = href!.slice(0, href!.lastIndexOf("/"))
        const matching = DECLARED_ROUTE_PATHS.filter(
            (path) =>
                path.startsWith(`${prefix}/:`) && !path.slice(prefix.length + 1).includes("/"),
        )
        expect(matching, `no <Route path="${prefix}/:..."> in App.tsx`).not.toHaveLength(0)
    })

    it("read enough of App.tsx to make that check meaningful", () => {
        // Guards the guard: if the glob or the regex ever returns nothing,
        // every it.each above passes vacuously against an empty list.
        expect(DECLARED_ROUTE_PATHS.length).toBeGreaterThan(15)
        expect(DECLARED_ROUTE_PATHS).toContain("/admin/curator-queue")
    })
})

describe("what gets a link and what does not", () => {
    it("links exactly the nine types with a page", () => {
        expect([...LINKABLE_RECORD_TYPES].sort()).toEqual([...EXPECTED_LINKABLE].sort())
    })

    it.each(EXPECTED_UNLINKABLE)("%s has no page, so it gets no link", (recordType) => {
        expect(recordRoute(recordType, "ref_abc")).toBeNull()
        expect(recordTypeHasPage(recordType)).toBe(false)
    })

    it("a linkable type with no public ref still gets no link", () => {
        // The backend answers null for a record it cannot name. A link to
        // `/species/null` is worse than plain text.
        expect(recordRoute("species", null)).toBeNull()
    })

    it("an empty ref is treated as no ref, not as a bare prefix", () => {
        expect(recordRoute("species", "")).toBeNull()
    })

    it("a type the frontend has never heard of gets no link", () => {
        expect(recordRoute("some_future_type", "ref_abc")).toBeNull()
    })
})

describe("the ref goes into the path safely", () => {
    it("builds the path from the ref it was given", () => {
        expect(recordRoute("species", "spc_vu7cuk4s37szxaudjpf355tqda")).toBe(
            "/species/spc_vu7cuk4s37szxaudjpf355tqda",
        )
        // Not the obvious pluralisation: `reaction` is served at /reactions,
        // and `species` is not `/speciess`.
        expect(recordRoute("reaction", "rxn_1")).toBe("/reactions/rxn_1")
        expect(recordRoute("conformer_observation", "cob_1")).toBe(
            "/conformer-observations/cob_1",
        )
    })

    it("escapes a ref that would otherwise reshape the path", () => {
        // A slash in a ref would add a segment and match some other route --
        // or nothing. Encoding keeps a malformed ref a 404 on its own page
        // rather than a link into an unrelated record.
        expect(recordRoute("species", "a/b")).toBe("/species/a%2Fb")
        expect(recordRoute("species", "a b")).toBe("/species/a%20b")
    })
})
