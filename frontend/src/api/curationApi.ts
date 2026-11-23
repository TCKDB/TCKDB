import { AuthApiError, throwForFailedResponse } from "./authApi"
import {
    StructureEvidenceFindingPageReadSchema,
    StructureEvidenceFindingReadSchema,
    type StructureEvidenceFinding,
    type StructureEvidenceFindingPage,
} from "../types/structureEvidenceFinding"
import { ReproducibilityAssessmentReadSchema, type ReproducibilityAssessment } from "../types/reproducibilityAssessment"

/**
 * Client for `/api/v1/curation/*` (`backend/app/api/routes/curation.py`). Curator and admin only; the panels check the role
 * before they ask, and the server enforces it. Same session-cookie contract as the rest of the admin surface.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/curation`

/** The write reached the server and was applied; the reply could not be read. */
export class CurationResponseError extends Error {
    constructor(what: "finding" | "assessment") {
        super(
            `The ${what} was saved, but this page could not read the reply. Reload to see it.`,
        )
        this.name = "CurationResponseError"
    }
}

async function request(method: "GET" | "POST", url: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
    const response = await fetch(url, {
        method,
        credentials: "include",
        signal,
        headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) return throwForFailedResponse(response)
    return response
}

export async function listStructureFindings(options: {
    subjectRef: string
    scope: string
    skip: number
    limit: number
    signal?: AbortSignal
}): Promise<StructureEvidenceFindingPage> {
    const params = new URLSearchParams({
        subject_ref: options.subjectRef,
        scope: options.scope,
        skip: String(options.skip),
        limit: String(options.limit),
    })
    const response = await request("GET", `${BASE}/structure-evidence-findings?${params}`, undefined, options.signal)
    return StructureEvidenceFindingPageReadSchema.parse(await response.json())
}

export type NewStructureFinding = {
    scope: string
    subjectRef: string
    kind: string
    verdict: string
    rationale: string
    role: string
    sourceCalculationRef: string
    literatureRef: string
    supersedesFindingRef: string
}

export async function appendStructureFinding(input: NewStructureFinding): Promise<StructureEvidenceFinding> {
    const body: Record<string, string> = {
        scope: input.scope,
        subject_ref: input.subjectRef,
        kind: input.kind,
        verdict: input.verdict,
        rationale: input.rationale.trim(),
    }
    if (input.role) body.role = input.role
    if (input.sourceCalculationRef.trim()) body.source_calculation_ref = input.sourceCalculationRef.trim()
    if (input.literatureRef.trim()) body.literature_ref = input.literatureRef.trim()
    if (input.supersedesFindingRef) body.supersedes_finding_ref = input.supersedesFindingRef
    const response = await request("POST", `${BASE}/structure-evidence-findings`, body)
    const parsed = StructureEvidenceFindingReadSchema.safeParse(await response.json().catch(() => null))
    if (!parsed.success) throw new CurationResponseError("finding")
    return parsed.data
}

const assessmentPath = (recordType: string, recordId: number) =>
    `${BASE}/reproducibility-assessments/${encodeURIComponent(recordType)}/${recordId}`

/** `null` when the record has never been assessed (the route answers 404). */
export async function loadLatestReproducibility(recordType: string, recordId: number, signal?: AbortSignal): Promise<ReproducibilityAssessment | null> {
    try {
        const response = await request("GET", `${assessmentPath(recordType, recordId)}/latest`, undefined, signal)
        return ReproducibilityAssessmentReadSchema.parse(await response.json())
    } catch (error) {
        if (error instanceof AuthApiError && error.status === 404) return null
        throw error
    }
}

export async function evaluateReproducibility(recordType: string, recordId: number): Promise<ReproducibilityAssessment> {
    const response = await request("POST", `${assessmentPath(recordType, recordId)}/evaluate`, {})
    const parsed = ReproducibilityAssessmentReadSchema.safeParse(await response.json().catch(() => null))
    if (!parsed.success) throw new CurationResponseError("assessment")
    return parsed.data
}
