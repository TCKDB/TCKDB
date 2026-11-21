// Source guard: a literature title or DOI is read only through `literatureCitation.ts`. Any other access
// (`literature.title`, `lit?.doi`, `source_literature.title`) can put depositor-typed text on a public page.

import { describe, expect, it } from "vitest"

const sources = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx"], {
    query: "?raw",
    import: "default",
    eager: true,
}) as Record<string, string>

const ACCESS = /\b\w*(?:literature|lit)\w*\??\.(?:title|doi)\b/i

describe("literature title and DOI access", () => {
    it("scans real source files (not vacuous)", () => {
        expect(Object.keys(sources).length).toBeGreaterThan(50)
        expect(ACCESS.test("record.literature?.title")).toBe(true)
        expect(ACCESS.test("scheme.source_literature.title")).toBe(true)
        expect(ACCESS.test("record.literature?.doi")).toBe(true)
    })

    it("happens only in literatureCitation.ts", () => {
        const offenders = Object.entries(sources)
            .filter(([path]) => !path.endsWith("/literatureCitation.ts"))
            .filter(([, text]) => ACCESS.test(text))
            .map(([path]) => path)
        expect(offenders).toEqual([])
    })
})
