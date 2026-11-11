import { describe, expect, it } from "vitest"

const SOURCES = import.meta.glob("./*Page.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>

/** Every record page. A new record page belongs in this list, so that it too shows "Where this record sits". */
const RECORD_PAGES = [
    "CalculationDetailPage.tsx",
    "ConformerGroupPage.tsx",
    "ConformerObservationPage.tsx",
    "GeometryDetailPage.tsx",
    "TransitionStateEntryPage.tsx",
    "ReactionOverviewPage.tsx",
    "ReactionEntryPage.tsx",
    "SpeciesEntryPage.tsx",
    "SpeciesOverviewPage.tsx",
    "LevelOfTheoryPage.tsx",
    "NetworkEntryPage.tsx",
]

/** `side=` must hold a relationship map; `side={null}` or `side={undefined}` does not count. */
const MAP_SIDE = /\bside=\{\s*<(RecordMap|RelationshipMap|SpeciesEntryRelationshipMap|SpeciesRelationshipMap)\b/

/** The props text of each `<PageShell ...>` opening tag, found by walking to the first `>` outside braces. */
function pageShellTags(source: string): string[] {
    const tags: string[] = []
    let from = source.indexOf("<PageShell")
    while (from !== -1) {
        let depth = 0
        let end = from
        for (; end < source.length; end += 1) {
            const char = source[end]
            if (char === "{") depth += 1
            else if (char === "}") depth -= 1
            else if (char === ">" && depth === 0 && source[end - 1] !== "=") break
        }
        tags.push(source.slice(from, end))
        from = source.indexOf("<PageShell", end)
    }
    return tags
}

describe("record pages", () => {
    it.each(RECORD_PAGES)("%s passes side= to every PageShell it renders", (file) => {
        expect(SOURCES[`./${file}`], `${file} is read`).toBeTypeOf("string")
        const tags = pageShellTags(SOURCES[`./${file}`])
        expect(tags.length).toBeGreaterThan(0)
        for (const tag of tags) expect(tag).toMatch(MAP_SIDE)
    })

    it("finds a PageShell that forgets side=", () => {
        expect(pageShellTags("<PageShell identity={<a />}>x</PageShell>")[0]).not.toMatch(MAP_SIDE)
        expect(pageShellTags("<PageShell side={null}>x</PageShell>")[0]).not.toMatch(MAP_SIDE)
        expect(pageShellTags("<PageShell side={undefined}>x</PageShell>")[0]).not.toMatch(MAP_SIDE)
        expect(pageShellTags("<PageShell side={<RecordMap nodes={[]} />}>x</PageShell>")[0]).toMatch(MAP_SIDE)
    })
})
