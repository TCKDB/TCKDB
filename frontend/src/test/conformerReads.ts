import { http, HttpResponse, type JsonBodyType, type RequestHandler } from "msw"

/** A two-atom geometry, enough for a picture and a shape sentence. */
export const OH_GEOMETRY = {
    geometry_ref: "geom_oh_final",
    natoms: 2,
    geom_hash: "ab12cd34".repeat(8),
    format: "cartesian",
    coordinate_units: "angstrom",
    symbols: ["O", "H"],
    coords: [[0, 0, 0], [0, 0, 0.98]],
    atoms: [
        { atom_index: 1, element: "O", x: 0, y: 0, z: 0 },
        { atom_index: 2, element: "H", x: 0, y: 0, z: 0.98 },
    ],
    xyz_text: "2\n\nO 0.000000 0.000000 0.000000\nH 0.000000 0.000000 0.980000",
    created_at: "2026-07-21T12:06:50.748258",
    provenance: {},
}

/**
 * The reads a conformer page makes besides its own record: the entry's single-point and optimisation energies, which
 * records name each conformer group, and the geometry the picture is drawn from. Pass `energies` to serve records.
 */
export function conformerReadHandlers(options: {
    entryRef: string
    energies?: { sp?: unknown[]; opt?: unknown[] }
    usage?: JsonBodyType
    geometry?: Record<string, unknown> | null
}): RequestHandler[] {
    const { entryRef, energies = {}, usage = { species_entry_ref: entryRef, groups: [], ensemble_thermo_refs: [] }, geometry = OH_GEOMETRY } = options
    return [
        http.get("/api/v1/scientific/species-calculations/search", ({ request }) => {
            const type = new URL(request.url).searchParams.get("calculation_type")
            return HttpResponse.json({ records: (type === "opt" ? energies.opt : energies.sp) ?? [] })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/conformer-usage`, () => HttpResponse.json(usage)),
        http.get("/api/v1/scientific/geometries/:ref", () => (geometry ? HttpResponse.json(geometry) : new HttpResponse(null, { status: 404 }))),
    ]
}
