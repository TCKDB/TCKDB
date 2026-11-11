import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const here = dirname(fileURLToPath(import.meta.url))

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
]

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
        const tags = pageShellTags(readFileSync(join(here, file), "utf8"))
        expect(tags.length).toBeGreaterThan(0)
        for (const tag of tags) expect(tag).toMatch(/\bside=\{/)
    })

    it("finds a PageShell that forgets side=", () => {
        expect(pageShellTags("<PageShell identity={<a />}>x</PageShell>")[0]).not.toMatch(/\bside=\{/)
    })
})
