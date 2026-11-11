import { describe, expect, it } from "vitest"
import { MIN_SECTIONS_FOR_LIST } from "./components/TableOfContents"

/**
 * Every page that renders `PageShell` declares whether it has a contents list. A page whose section count is
 * fixed below MIN_SECTIONS_FOR_LIST says "none" (the side column stays, without a list); every other page lists
 * its sections. A new PageShell page fails here until it is added with an explicit entry.
 */
const DECLARED: Record<string, "list" | "none"> = {
    "./pages/BrowsePage.tsx": "none",
    "./pages/FrequencyScaleFactorPage.tsx": "none",
    "./components/LoadingPage.tsx": "none",
    "./components/RecordPlaceholder.tsx": "none",
    "./pages/GuidePage.tsx": "list",
    "./pages/CalculationDetailPage.tsx": "list",
    "./pages/CompositeSchemePage.tsx": "list",
    "./pages/ConformerGroupPage.tsx": "list",
    "./pages/ConformerObservationPage.tsx": "list",
    "./pages/CorrectionSchemePage.tsx": "list",
    "./pages/GeometryDetailPage.tsx": "list",
    "./pages/LevelOfTheoryPage.tsx": "list",
    "./pages/MethodsIndexPage.tsx": "list",
    "./pages/NetworkEntryPage.tsx": "list",
    "./pages/ReactionComparePage.tsx": "list",
    "./pages/ReactionEntryPage.tsx": "list",
    "./pages/ReactionOverviewPage.tsx": "list",
    "./pages/SpeciesComparePage.tsx": "list",
    "./pages/SpeciesEntryPage.tsx": "list",
    "./pages/SpeciesOverviewPage.tsx": "list",
    "./pages/TransitionStateEntryPage.tsx": "list",
}

const SOURCES = Object.fromEntries(
    Object.entries(import.meta.glob("./{pages,components}/**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>)
        .filter(([path]) => !path.endsWith(".test.tsx"))
        .map(([path, source]) => [path, source.replace(/\/\*[\s\S]*?\*\//g, "")]),
)
const SHELL_PAGES = Object.entries(SOURCES).filter(([path, source]) => /<PageShell\b/.test(source) && !path.endsWith("/PageShell.tsx"))

function declared(source: string): "list" | "none" {
    return /<PageShell\b[^>]*\bcontents="none"/.test(source) ? "none" : "list"
}

describe("every page that renders PageShell declares its contents column", () => {
    it("finds the pages (the scan is not vacuous)", () => {
        expect(SHELL_PAGES.length).toBeGreaterThanOrEqual(10)
    })

    it("lists exactly the pages that use PageShell", () => {
        expect(SHELL_PAGES.map(([path]) => path).sort()).toEqual(Object.keys(DECLARED).sort())
    })

    it.each(Object.entries(DECLARED))("%s declares %s", (path, choice) => {
        expect(declared(SOURCES[path])).toBe(choice)
    })

    it('a page declaring "none" renders fewer section headings than a list needs', () => {
        for (const [path, choice] of Object.entries(DECLARED)) {
            if (choice !== "none") continue
            const headings = (SOURCES[path].match(/<SectionHeading\b/g) ?? []).length
            expect(headings, path).toBeLessThan(MIN_SECTIONS_FOR_LIST)
        }
    })
})
