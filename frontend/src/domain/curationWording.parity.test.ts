import { describe, expect, it } from "vitest"
import vocabulary from "./apiVocabulary.json"
import {
    CHECK_SENTENCE,
    FINDING_AUTHORITY,
    FINDING_KIND,
    FINDING_SCOPE,
    FINDING_VERDICT,
    REFUSAL_SENTENCE,
    SOURCE_ROLE,
    WARNING_SENTENCE,
} from "./curationWording"

// `import.meta.glob` over `node:fs` (no Node types under src/). Each source must match exactly one file, and every
// extracted list must be non-empty, so a moved file or a changed pattern fails instead of passing with nothing to compare.
const GOLDEN = import.meta.glob("../../../backend/tests/api/golden/openapi.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const RUBRIC = import.meta.glob("../../../backend/app/services/reproducibility_rubric.py", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const FINDINGS = import.meta.glob("../../../backend/app/services/structure_evidence_finding.py", { query: "?raw", import: "default", eager: true }) as Record<string, string>

function only(files: Record<string, string>): string {
    expect(Object.keys(files)).toHaveLength(1)
    return Object.values(files)[0]
}

// The role enum appears under two generated names in the OpenAPI (one per direction); both must agree.
function enumOf(name: string): string[] {
    const schemas = JSON.parse(only(GOLDEN)).components.schemas as Record<string, { enum?: string[] }>
    const names = Object.keys(schemas).filter((key) => key === name || key.endsWith(`__${name}`) || key === `${name}-Output`)
    expect(names.length, `${name} in the golden OpenAPI`).toBeGreaterThan(0)
    const [first, ...rest] = names.map((key) => [...(schemas[key].enum ?? [])].sort())
    for (const other of rest) expect(other).toEqual(first)
    const values = first
    expect(values.length, `${name} enum in the golden OpenAPI`).toBeGreaterThan(1)
    return values
}

function matches(source: string, pattern: RegExp): string[] {
    return [...new Set([...source.matchAll(pattern)].map((m) => m[1]))].sort()
}

describe("curationWording parity with the backend", () => {
    it("words every finding token and no other", () => {
        expect(Object.keys(FINDING_KIND).sort()).toEqual(enumOf("StructureFindingKind"))
        expect(Object.keys(FINDING_VERDICT).sort()).toEqual(enumOf("StructureFindingVerdict"))
        expect(Object.keys(SOURCE_ROLE).sort()).toEqual(enumOf("StructureSourceRole"))
        expect(Object.keys(FINDING_SCOPE).sort()).toEqual(enumOf("StructureFindingScope"))
        expect(Object.keys(FINDING_AUTHORITY).sort()).toEqual(enumOf("StructureFindingAuthority"))
    })

    it("keeps the grade meanings aligned with the grade enum", () => {
        expect(Object.keys(vocabulary.reproducibilityGrade).sort()).toEqual(enumOf("ReproducibilityGrade"))
    })

    it("has a sentence for every rubric check and warning", () => {
        const source = only(RUBRIC)
        const names = [...new Set([...matches(source, /_check\(\s*"([a-z_]+)"/g), ...matches(source, /name="([a-z_]+)"/g)])].sort()
        const codes = [...new Set([...matches(source, /code="([a-z_]+)"/g), ...matches(source, /ArtifactVerificationStatus\.\w+, "([a-z_]+)"/g)])].sort()
        expect(names.length).toBeGreaterThanOrEqual(15)
        expect(codes.length).toBeGreaterThanOrEqual(6)
        expect(Object.keys(CHECK_SENTENCE).sort()).toEqual(names)
        expect(Object.keys(WARNING_SENTENCE).sort()).toEqual(codes)
    })

    it("has a refusal sentence for every structure_finding_ code", () => {
        const codes = matches(only(FINDINGS), /"(structure_finding_[a-z_]+)"/g)
        expect(codes.length).toBeGreaterThanOrEqual(12)
        for (const code of codes) expect(Object.keys(REFUSAL_SENTENCE), code).toContain(code)
    })

    it("contains no snake_case in any sentence", () => {
        const sentences = [
            ...Object.values(FINDING_KIND), ...Object.values(FINDING_VERDICT), ...Object.values(SOURCE_ROLE),
            ...Object.values(FINDING_SCOPE), ...Object.values(FINDING_AUTHORITY),
            ...Object.values(CHECK_SENTENCE), ...Object.values(WARNING_SENTENCE),
        ]
        expect(sentences.length).toBeGreaterThan(30)
        for (const sentence of sentences) expect(sentence, sentence).not.toMatch(/[a-z]+_[a-z]+/)
    })
})
