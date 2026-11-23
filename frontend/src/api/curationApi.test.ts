import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { AuthApiError } from "./authApi"
import {
    CurationResponseError,
    appendStructureFinding,
    evaluateReproducibility,
    listStructureFindings,
    loadLatestReproducibility,
} from "./curationApi"
import { assessmentRead, findingPage, findingRead } from "../test/curationFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const FINDINGS = "/api/v1/curation/structure-evidence-findings"
const LATEST = "/api/v1/curation/reproducibility-assessments/calculation/42/latest"
const EVALUATE = "/api/v1/curation/reproducibility-assessments/calculation/42/evaluate"

describe("listStructureFindings", () => {
    it("encodes subject, scope, skip and limit and sends credentials", async () => {
        let seen: Request | undefined
        server.use(http.get(FINDINGS, ({ request }) => {
            seen = request
            return HttpResponse.json(findingPage([findingRead()]))
        }))
        const page = await listStructureFindings({ subjectRef: "calc_a b", scope: "calculation", skip: 20, limit: 20 })
        const url = new URL(seen!.url)
        expect(url.searchParams.get("subject_ref")).toBe("calc_a b")
        expect(url.searchParams.get("scope")).toBe("calculation")
        expect(url.searchParams.get("skip")).toBe("20")
        expect(url.searchParams.get("limit")).toBe("20")
        expect(seen!.credentials).toBe("include")
        expect(page.findings).toHaveLength(1)
        expect(page.findings[0].finding_ref).toBe("sef_first")
    })

    it("throws the refusal with its code and context", async () => {
        server.use(http.get(FINDINGS, () => HttpResponse.json({ code: "forbidden", detail: "no", context: { a: 1 } }, { status: 403 })))
        const error = await listStructureFindings({ subjectRef: "calc_a", scope: "calculation", skip: 0, limit: 20 }).catch((e: unknown) => e)
        expect(error).toBeInstanceOf(AuthApiError)
        expect((error as AuthApiError).status).toBe(403)
        expect((error as AuthApiError).context).toEqual({ a: 1 })
    })
})

describe("appendStructureFinding", () => {
    it("leaves empty optional fields out of the body", async () => {
        let body: Record<string, unknown> = {}
        server.use(http.post(FINDINGS, async ({ request }) => {
            body = (await request.json()) as Record<string, unknown>
            expect(request.credentials).toBe("include")
            return HttpResponse.json(findingRead({ finding_ref: "sef_new" }), { status: 201 })
        }))
        const created = await appendStructureFinding({
            scope: "calculation", subjectRef: "calc_a", kind: "identity_incompatibility", verdict: "invalidates",
            rationale: "  Because.  ", role: "", sourceCalculationRef: "", literatureRef: "", supersedesFindingRef: "",
        })
        expect(created.finding_ref).toBe("sef_new")
        expect(body).toEqual({ scope: "calculation", subject_ref: "calc_a", kind: "identity_incompatibility", verdict: "invalidates", rationale: "Because." })
    })

    it("sends the optional fields that are filled", async () => {
        let body: Record<string, unknown> = {}
        server.use(http.post(FINDINGS, async ({ request }) => {
            body = (await request.json()) as Record<string, unknown>
            return HttpResponse.json(findingRead(), { status: 201 })
        }))
        await appendStructureFinding({
            scope: "geometry", subjectRef: "geom_a", kind: "role_invalidation", verdict: "invalidates", rationale: "r",
            role: "energy", sourceCalculationRef: "calc_b", literatureRef: "lit_c", supersedesFindingRef: "",
        })
        expect(body).toMatchObject({ role: "energy", source_calculation_ref: "calc_b", literature_ref: "lit_c" })
        expect(body).not.toHaveProperty("supersedes_finding_ref")
    })

    it("says the finding was saved when a 2xx reply cannot be read", async () => {
        server.use(http.post(FINDINGS, () => HttpResponse.json({ nonsense: true }, { status: 201 })))
        const error = await appendStructureFinding({
            scope: "calculation", subjectRef: "calc_a", kind: "identity_incompatibility", verdict: "invalidates", rationale: "r",
            role: "", sourceCalculationRef: "", literatureRef: "", supersedesFindingRef: "",
        }).catch((e: unknown) => e)
        expect(error).toBeInstanceOf(CurationResponseError)
        expect((error as Error).message).toMatch(/saved, but this page could not read the reply/i)
    })
})

describe("reproducibility", () => {
    it("returns null for a record nobody has assessed", async () => {
        server.use(http.get(LATEST, () => HttpResponse.json({ detail: "none" }, { status: 404 })))
        await expect(loadLatestReproducibility("calculation", 42)).resolves.toBeNull()
    })

    it("returns the latest assessment", async () => {
        server.use(http.get(LATEST, () => HttpResponse.json(assessmentRead())))
        const row = await loadLatestReproducibility("calculation", 42)
        expect(row?.grade).toBe("auditable")
        expect(row?.passed).toHaveLength(3)
    })

    it("throws other failures with code and context", async () => {
        server.use(http.get(LATEST, () => HttpResponse.json({ code: "boom", detail: "x", context: { k: "v" } }, { status: 500 })))
        const error = await loadLatestReproducibility("calculation", 42).catch((e: unknown) => e)
        expect((error as AuthApiError).code).toBe("boom")
        expect((error as AuthApiError).context).toEqual({ k: "v" })
    })

    it("posts to evaluate", async () => {
        let method = ""
        server.use(http.post(EVALUATE, ({ request }) => {
            method = request.method
            return HttpResponse.json(assessmentRead({ assessment_ref: "rpa_new" }), { status: 201 })
        }))
        const row = await evaluateReproducibility("calculation", 42)
        expect(method).toBe("POST")
        expect(row.assessment_ref).toBe("rpa_new")
    })

    it("says the assessment was saved when a 2xx reply cannot be read", async () => {
        server.use(http.post(EVALUATE, () => HttpResponse.json({ nope: 1 }, { status: 201 })))
        await expect(evaluateReproducibility("calculation", 42)).rejects.toBeInstanceOf(CurationResponseError)
    })
})
