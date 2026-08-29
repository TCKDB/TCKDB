import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// Shape notes (measured 2026-08-29 against https://tckdb.homecalvin.com,
// backend/app/schemas/reads/scientific_geometry.py and
// backend/app/services/scientific_read/geometry.py):
//
// - Unlike every other `/scientific/*` detail surface this project has
//   built, the payload is FLAT AT THE ROOT — there is no `record` wrapper.
//   `parseGeometryResponse` below returns the parsed root object directly.
//
// - The endpoint accepts `include=` (legal tokens: `review`, `provenance`,
//   `internal_ids`, `all` — read off `_LEGAL_INCLUDE_TOKENS` in
//   `app/services/scientific_read/geometry.py`), but as measured, none of
//   those tokens actually gate anything in the response body:
//     * `provenance` is built and attached unconditionally, regardless of
//       whether `provenance` (or `all`) was requested.
//     * `review` is a legal token with NO MATCHING FIELD anywhere in
//       `ScientificGeometryResponse` — there is no review/review_history
//       on a geometry at all. Requesting it changes only the request echo.
//     * `internal_ids` does not surface `geometry_id` on the hosted
//       deployment either (same Phase-D visibility gate as the
//       calculation surface — the deployment does not permit it).
//   So this client still requests `include=provenance` (the one token with
//   a real, if currently redundant, semantic match to a field this page
//   renders) for forward-compatibility, but never `review` — there is
//   nothing on this endpoint's own schema for that token to gate.
//
// - `format` and `coordinate_units` are typed `Literal["cartesian"]` /
//   `Literal["angstrom"]` server-side, not open enums — there is currently
//   only one value each. Read here as plain strings (not a closed union)
//   so a future widening doesn't require a client schema change, but do
//   not assume other values exist today.
//
// - `atoms` does carry more than `symbols` + `coords`: each row has its own
//   `atom_index`, and measured live data (geom_qcnisbgb4abax5oxym3dtjxu34)
//   shows atom_index is 1-based while `symbols`/`coords` are plain
//   0-indexed arrays in the same order. They are two views of the same
//   underlying atom list, not independent data.
//
// - There is NO validation field on this endpoint at all — no
//   `geometry_validation`, no pass/fail, nothing. The plan's IA entry for
//   this route promises "coordinates, validation, producer/consumer
//   links", but geometry-level validation is recorded on the *producing
//   calculation's* `geometry_validation` section (keyed by
//   input/output geometry refs), not here. The page below says this
//   plainly and links to a producing calculation rather than fabricating
//   a verdict.
// ---------------------------------------------------------------------------

const geometryAtomSchema = z.object({
    atom_index: z.number(),
    element: z.string(),
    x: z.number(),
    y: z.number(),
    z: z.number(),
}).passthrough()

const geometryProvenanceCalcLinkSchema = z.object({
    calculation_ref: z.string(),
    calculation_type: z.string(),
    role: z.string().nullable().optional(),
}).passthrough()

const geometryProvenanceSchema = z.object({
    produced_by: z.array(geometryProvenanceCalcLinkSchema).nullable().optional(),
    used_as_input_by: z.array(geometryProvenanceCalcLinkSchema).nullable().optional(),
}).passthrough()

const geometryRecordSchema = z.object({
    geometry_ref: z.string(),
    natoms: z.number(),
    geom_hash: z.string(),
    format: z.string(),
    coordinate_units: z.string(),
    symbols: z.array(z.string()).nullable().optional(),
    coords: z.array(z.array(z.number())).nullable().optional(),
    atoms: z.array(geometryAtomSchema).nullable().optional(),
    xyz_text: z.string().nullable().optional(),
    created_at: z.string(),
    provenance: geometryProvenanceSchema.nullable().optional(),
}).passthrough()

export type GeometryRecord = z.infer<typeof geometryRecordSchema>
export type GeometryProvenanceCalcLink = z.infer<typeof geometryProvenanceCalcLinkSchema>
export type GeometryAtom = z.infer<typeof geometryAtomSchema>

/**
 * Load the geometry detail record. Requests `include=provenance` (see the
 * module docstring for why not `review`); the response is the root object
 * itself, not `{ record: ... }`.
 */
export async function loadGeometry(ref: string, signal?: AbortSignal): Promise<GeometryRecord> {
    const query = new URLSearchParams()
    query.append("include", "provenance")
    const endpoint = `/api/v1/scientific/geometries/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(geometryRecordSchema, payload, "geometry")
}
