import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// Per-point reads of a path-shaped calculation: `GET /scientific/calculations/{ref}/path-search`.
// The response carries the same summary block as `include=path_search` plus one page of points.

const PAGE_LIMIT = 200
/** Upper bound on pages followed for one calculation, so a misbehaving cursor cannot loop forever. */
const MAX_PAGES = 50

const pointGeometryLinkSchema = z.object({
    geometry_ref: z.string(),
    natoms: z.number().nullable().optional(),
    geom_hash: z.string().nullable().optional(),
}).passthrough()

const paginationSchema = z.object({
    returned: z.number().optional(),
    total: z.number().nullable().optional(),
}).passthrough()

export const pathSearchSummarySchema = z.object({
    method: z.string(),
    is_double_ended: z.boolean().nullable().optional(),
    converged: z.boolean().nullable().optional(),
    n_points: z.number().nullable().optional(),
    selected_ts_point_index: z.number().nullable().optional(),
    climbing_image_index: z.number().nullable().optional(),
    source_endpoint_count: z.number().nullable().optional(),
    zero_energy_reference_hartree: z.number().nullable().optional(),
    stored_point_count: z.number(),
    ts_guess_count: z.number(),
    climbing_image_count: z.number(),
}).passthrough()

const pathSearchPointSchema = z.object({
    point_index: z.number(),
    path_coordinate: z.number().nullable().optional(),
    electronic_energy_hartree: z.number().nullable().optional(),
    relative_energy_kj_mol: z.number().nullable().optional(),
    max_force: z.number().nullable().optional(),
    rms_force: z.number().nullable().optional(),
    max_gradient: z.number().nullable().optional(),
    rms_gradient: z.number().nullable().optional(),
    is_ts_guess: z.boolean().optional(),
    is_climbing_image: z.boolean().optional(),
    geometry_ref: z.string().nullable().optional(),
    geometry_link: pointGeometryLinkSchema.nullable().optional(),
}).passthrough()

const pathSearchPageSchema = z.object({
    path_search: pathSearchSummarySchema,
    points: z.array(pathSearchPointSchema),
    pagination: paginationSchema,
    next_cursor: z.string().nullable().optional(),
}).passthrough()

export type PathSearchSummary = z.infer<typeof pathSearchSummarySchema>
export type PathSearchPoint = z.infer<typeof pathSearchPointSchema>
export type PathSearchPoints = { summary: PathSearchSummary; points: PathSearchPoint[] }

type Page<P> = { points: P[]; pagination: z.infer<typeof paginationSchema>; next_cursor?: string | null }

/** Follows `next_cursor`, or the offset when the read pages by offset, until every point is read. */
async function readAllPages<P, R extends Page<P>>(
    endpoint: (query: URLSearchParams) => string,
    parse: (payload: unknown) => R,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<{ first: R; points: P[] }> {
    const points: P[] = []
    let first: R | null = null
    let cursor: string | null = null
    for (let page = 0; page < MAX_PAGES; page++) {
        const query = new URLSearchParams({ include_geometries: "true", limit: String(PAGE_LIMIT) })
        if (cursor) query.set("cursor", cursor)
        else if (points.length > 0) query.set("offset", String(points.length))
        const response = parse(await requestScientificJson(endpoint(query), signal, onRateLimited))
        first ??= response
        points.push(...response.points)
        const total = response.pagination.total ?? null
        if (response.next_cursor) {
            cursor = response.next_cursor
            continue
        }
        if (response.points.length === 0 || total === null || points.length >= total) break
    }
    if (!first) throw new Error("No page was read.")
    return { first, points }
}

/** Every stored point of one path search, in the order the read returns them (point index). */
export async function loadPathSearchPoints(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<PathSearchPoints> {
    const { first, points } = await readAllPages<PathSearchPoint, z.infer<typeof pathSearchPageSchema>>(
        (query) => `/api/v1/scientific/calculations/${encodeURIComponent(ref)}/path-search?${query}`,
        (payload) => parseScientificResponse(pathSearchPageSchema, payload, "path search"),
        signal,
        onRateLimited,
    )
    return { summary: first.path_search, points }
}
