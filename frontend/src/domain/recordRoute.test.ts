import { describe, expect, it } from "vitest"
import {
    LINKABLE_RECORD_TYPES,
    recordRoute,
    recordTypeHasPage,
    recordTypeWords,
    resolveRecordLocation,
} from "./recordRoute"

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

/**
 * These two lists are the frontend's own record of a decision, and this
 * suite can only check them against each other.
 *
 * What the checks below DO prove: no type is in both lists, and the two
 * together come to the seventeen `SubmissionRecordType` members that
 * existed when this was written.
 *
 * What they do NOT prove, and what a reader should not assume: that the
 * backend enum still has exactly those seventeen. A new record type added
 * on the backend appears in neither list and nothing here notices -- its
 * rows would simply render unlinked, which is the safe direction but is a
 * silent decision rather than a made one. Reading the enum from
 * `backend/app/db/models/common.py` was tried and abandoned: vite refuses
 * to load a file from outside its root, and widening `server.fs.allow` to
 * the repo root would let the dev server (which binds 0.0.0.0) serve
 * backend source over the network. A test is not worth that. The guard
 * belongs on the backend side, where reading the frontend's list costs
 * nothing -- raised as its own task.
 *
 * `EXPECTED_UNLINKABLE` on its own is weak by construction: `recordRoute`
 * returns null for ANY unknown string, so those eight cases and the
 * "some_future_type" case exercise the same branch. They are kept because
 * naming the real types documents which ones were considered.
 */
const RECORD_TYPE_COUNT_WHEN_WRITTEN = 17

describe("the two lists are a coherent decision", () => {
    it("accounts for every record type that existed when this was written", () => {
        expect(EXPECTED_LINKABLE.length + EXPECTED_UNLINKABLE.length).toBe(
            RECORD_TYPE_COUNT_WHEN_WRITTEN,
        )
    })

    it("claims no record type is both linkable and not", () => {
        expect(EXPECTED_LINKABLE.filter((t) => EXPECTED_UNLINKABLE.includes(t))).toEqual([])
    })

    it("names each type once", () => {
        const all = [...EXPECTED_LINKABLE, ...EXPECTED_UNLINKABLE]
        expect(new Set(all).size).toBe(all.length)
    })
})

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


/**
 * #262: where a record can be SEEN, when it has no page of its own.
 *
 * `recordRoute` answers "is there a page for this type", and for 385 of the
 * review queue's 1,299 rows the answer is no. Six of those types are still
 * rendered -- inside their parent -- and the review row now carries which
 * parent. `resolveRecordLocation` is the decision that turns those two facts
 * into a link, or into an honest statement of why there isn't one.
 */
describe("where a record can be seen", () => {
    it("links the record's own page when it has one", () => {
        expect(resolveRecordLocation("species", "spc_1", null, null)).toEqual({
            kind: "record",
            href: "/species/spc_1",
            ref: "spc_1",
        })
    })

    it("prefers the record's own page over its container", () => {
        // A species entry belongs to a species, and the backend reports that.
        // Following the container here would send a curator to a DIFFERENT
        // record from the one they were asked to review -- the precedence in
        // `resolveRecordLocation` is the only thing preventing it.
        const location = resolveRecordLocation("species_entry", "spe_1", "species", "spc_1")
        expect(location.kind).toBe("record")
        expect(location).toMatchObject({ href: "/species-entries/spe_1" })
    })

    it.each([
        ["thermo", "thm_1", "species_entry", "spe_1", "/species-entries/spe_1"],
        ["statmech", "stm_1", "species_entry", "spe_1", "/species-entries/spe_1"],
        [
            "statmech",
            "stm_2",
            "transition_state_entry",
            "tse_1",
            "/transition-state-entries/tse_1",
        ],
        ["kinetics", "kin_1", "reaction_entry", "rxe_1", "/reaction-entries/rxe_1"],
        ["transition_state", "ts_1", "reaction_entry", "rxe_1", "/reaction-entries/rxe_1"],
        ["network_solve", "nsv_1", "network", "net_1", "/networks/net_1"],
    ])(
        "%s has no page, so it opens at its %s",
        (recordType, ref, containerType, containerRef, expectedHref) => {
            const location = resolveRecordLocation(
                recordType,
                ref,
                containerType,
                containerRef,
            )

            expect(location).toEqual({
                kind: "container",
                href: expectedHref,
                ref,
                containerType,
                containerRef,
            })
        },
    )

    it("locates a record that cannot be named at all", () => {
        // applied_energy_correction, 164 of the 385 and the only table in the
        // archive with no public ref (task #253). "a correction on spe_1" is
        // what a reviewer needs, and it does not wait on #253.
        expect(
            resolveRecordLocation(
                "applied_energy_correction",
                null,
                "species_entry",
                "spe_1",
            ),
        ).toEqual({
            kind: "container",
            href: "/species-entries/spe_1",
            ref: null,
            containerType: "species_entry",
            containerRef: "spe_1",
        })
    })

    it("distinguishes 'no page yet' from 'could not be named'", () => {
        // The two are different things to do about, and collapsing them is
        // what made eight identical lines tell a curator nothing.
        expect(resolveRecordLocation("artifact", "art_1", null, null).kind).toBe("no-page")
        expect(resolveRecordLocation("artifact", null, null, null).kind).toBe("unnamed")
    })

    it("refuses half a container pair", () => {
        // A ref with no type cannot be routed; a type with no ref names
        // nothing. Either would otherwise render as a link to nowhere.
        expect(resolveRecordLocation("thermo", "thm_1", null, "spe_1").kind).toBe("no-page")
        expect(resolveRecordLocation("thermo", "thm_1", "species_entry", null).kind).toBe(
            "no-page",
        )
    })

    it("does not follow a container whose own type has no page", () => {
        // transition_state_entry's owner is transition_state, which has no
        // route. Falling back to it would build `/undefined/ts_1`.
        expect(
            resolveRecordLocation("statmech", "stm_1", "transition_state", "ts_1").kind,
        ).toBe("no-page")
    })

    it("escapes a container ref that would otherwise reshape the path", () => {
        expect(
            resolveRecordLocation("thermo", "thm_1", "species_entry", "a/b"),
        ).toMatchObject({ href: "/species-entries/a%2Fb" })
    })

    it("words a container type for a reader without inventing one", () => {
        expect(recordTypeWords("species_entry")).toBe("species entry")
        expect(recordTypeWords("transition_state_entry")).toBe("transition state entry")
        expect(recordTypeWords("network")).toBe("network")
    })

    it("every container type the backend can send has a page", () => {
        // Written out from `_CONTAINER_COLUMNS` in
        // `backend/app/services/record_containers.py`. If the backend adds a
        // container type this frontend cannot route, rows of its children
        // silently drop back to "no page yet" -- the safe direction, but a
        // silent one, and this is where it becomes loud.
        //
        // transition_state is the one exception and is deliberate: only
        // transition_state_entry names it as a container, and that type has a
        // page of its own, so the fallback is never reached.
        const CONTAINER_TYPES_THE_BACKEND_SENDS = [
            "species",
            "species_entry",
            "conformer_group",
            "reaction",
            "reaction_entry",
            "transition_state_entry",
            "calculation",
            "network",
        ]
        for (const containerType of CONTAINER_TYPES_THE_BACKEND_SENDS) {
            expect(recordTypeHasPage(containerType), containerType).toBe(true)
        }
    })
})
