import { describe, expect, it } from "vitest"
import { assessmentRead, findingPage, findingRead, removedAdjudication } from "./curationFixtures"

// Same pattern as `apiVocabulary.parity.test.ts`: `import.meta.glob`, and a count check so a moved file fails rather than
// passing with nothing to compare.
const GOLDEN = import.meta.glob("../../../backend/tests/api/golden/openapi.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>

function properties(component: string): string[] {
    expect(Object.keys(GOLDEN)).toHaveLength(1)
    const schemas = JSON.parse(Object.values(GOLDEN)[0]).components.schemas as Record<string, { properties?: Record<string, unknown> }>
    const names = Object.keys(schemas[component]?.properties ?? {})
    expect(names.length, `${component} has properties in the golden OpenAPI`).toBeGreaterThanOrEqual(3)
    return names.sort()
}

type Schema = { type?: string; format?: string; enum?: unknown[]; anyOf?: Schema[]; $ref?: string; items?: Schema }

// Checks each fixture value against the golden property: JSON type, nullability, date-time format, enum membership,
// and array item shape one level down.
function conforms(value: unknown, schema: Schema, schemas: Record<string, Schema>, path: string): void {
    if (schema.$ref) return conforms(value, schemas[schema.$ref.split("/").pop() as string], schemas, path)
    if (schema.anyOf) {
        const options = schema.anyOf
        const ok = options.some((option) => {
            try {
                conforms(value, option, schemas, path)
                return true
            } catch {
                return false
            }
        })
        expect(ok, `${path} matches one of ${JSON.stringify(options)}`).toBe(true)
        return
    }
    if (schema.enum) return void expect(schema.enum, `${path} is a listed value`).toContain(value)
    if (schema.type === "null") return void expect(value, path).toBeNull()
    if (schema.type === "array") {
        expect(Array.isArray(value), `${path} is an array`).toBe(true)
        if (schema.items && Object.keys(schema.items).length > 0) (value as unknown[]).forEach((item, i) => conforms(item, schema.items as Schema, schemas, `${path}[${i}]`))
        return
    }
    if (schema.type === "object") return void expect(value !== null && typeof value === "object" && !Array.isArray(value), `${path} is an object`).toBe(true)
    if (schema.type === "integer") return void expect(Number.isInteger(value), `${path} is an integer`).toBe(true)
    if (schema.type) expect(typeof value, `${path} is a ${schema.type}`).toBe(schema.type)
    if (schema.format === "date-time") expect(Number.isNaN(Date.parse(value as string)), `${path} parses as a date-time`).toBe(false)
}

function allSchemas(): Record<string, Schema & { properties?: Record<string, Schema> }> {
    return JSON.parse(Object.values(GOLDEN)[0]).components.schemas
}

function valueShapes(component: string, fixture: object): void {
    const schemas = allSchemas()
    const props = schemas[component].properties ?? {}
    for (const [name, value] of Object.entries(fixture)) conforms(value, props[name], schemas, `${component}.${name}`)
}

describe("curation fixtures match the golden OpenAPI", () => {
    it("StructureEvidenceFindingRead", () => {
        expect(Object.keys(findingRead()).sort()).toEqual(properties("StructureEvidenceFindingRead"))
    })
    it("StructureEvidenceFindingPageRead", () => {
        expect(Object.keys(findingPage([])).sort()).toEqual(properties("StructureEvidenceFindingPageRead"))
    })
    it("RemovedAdjudication", () => {
        expect(Object.keys(removedAdjudication()).sort()).toEqual(properties("RemovedAdjudication"))
    })
    it("ReproducibilityAssessmentRead", () => {
        expect(Object.keys(assessmentRead()).sort()).toEqual(properties("ReproducibilityAssessmentRead"))
    })

    it("fixture values have the golden types, enum values and nested shapes", () => {
        valueShapes("StructureEvidenceFindingRead", findingRead())
        valueShapes("RemovedAdjudication", removedAdjudication())
        valueShapes("StructureEvidenceFindingPageRead", findingPage([findingRead()]))
        valueShapes("ReproducibilityAssessmentRead", assessmentRead())
    })
})
