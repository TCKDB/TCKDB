import { z } from "zod"
import { geometrySummarySchema, levelOfTheorySchema } from "./scientificSchemas"
import {
    scientificSpeciesSearchSchema,
    speciesEntrySummarySchema,
} from "./scientificSpeciesSchemas"
import { listMetaOf, listRequestEchoSchema, paginationSchema, type ListMeta } from "./listEnvelope"
import { nextPageRequest, pageQuery, parsePagedRef } from "../domain/paging"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

const softwareReleaseSchema = z.object({
    software_release_ref: z.string(),
    software: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const workflowToolReleaseSchema = z.object({
    workflow_tool_release_ref: z.string(),
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

// `software_release` / `workflow_tool_release` are measured-present on this
// endpoint's calculation rows (live, `conformers/search?include=calculations`)
// even though nothing here previously typed them -- `.passthrough()` already
// carried them at runtime; these two fields just give the geometry/SP tabs a
// typed way to read what was already on the wire, not a new request shape.
const calculationSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
}).passthrough()
const observationCalculationsSchema = z.array(calculationSchema).nullable().optional()
const observationSchema = z.object({
    conformer_observation: z.object({ conformer_observation_ref: z.string() }).passthrough(),
    calculations: observationCalculationsSchema,
}).passthrough()

// One rotor's torsion within a group's numeric basin identity --
// `include=fingerprints` on `conformers/search` (opt-in; see
// `backend/app/schemas/reads/scientific_conformer.py`). `quantized_bin`
// (read together with the parent fingerprint's `bin_width_deg`) is what
// DEFINES basin membership for this rotor; `raw_torsion_deg` /
// `folded_torsion_deg` are the REPRESENTATIVE conformer's own measured
// angle there -- a member of the basin, not the basin's boundary. The
// backend already zips these positionally (rotor_key together with its
// own bin/angles) before they reach the wire, so nothing here re-pairs
// them -- only renders what arrived already paired.
const conformerRotorTorsionSchema = z.object({
    rotor_key: z.string(),
    quantized_bin: z.number(),
    raw_torsion_deg: z.number(),
    folded_torsion_deg: z.number(),
}).passthrough()

const conformerGroupFingerprintSchema = z.object({
    rotor_count: z.number(),
    bin_width_deg: z.number(),
    torsions: z.array(conformerRotorTorsionSchema),
}).passthrough()

const conformerResponseSchema = z.object({
    records: z.array(z.object({
        conformer_group: z.object({
            conformer_group_ref: z.string(),
            label: z.string().nullable(),
            fingerprint: conformerGroupFingerprintSchema.nullable().optional(),
        }).passthrough(),
        observations_summary: z.object({ total: z.number() }).passthrough(),
        evidence_summary: z.object({
            calculation_count: z.number(),
            optimization_chain_count: z.number(),
            geometry_count: z.number(),
            evidence_coverage: z.object({
                opt: z.number(),
                freq: z.number(),
                sp: z.number(),
            }).passthrough(),
            levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)),
        }).passthrough(),
        observations: z.array(observationSchema).nullable().optional(),
        calculations: z.array(calculationSchema).nullable().optional(),
        // `role` is measured-present on this endpoint's geometry links
        // (live: "final") but was never typed by the shared
        // `geometrySummarySchema` -- extended locally here, the same way
        // `calculationSchema` above extends the shared calculation shape,
        // rather than widening a schema three other API modules also import.
        geometries: z.array(z.object({
            calculation_ref: z.string(),
            geometry: geometrySummarySchema.extend({ role: z.string().nullable().optional() }),
        }).passthrough()).nullable().optional(),
    }).passthrough()),
    // Optional only so a response without the window (a stub) still parses; the live route always sends both.
    pagination: paginationSchema.optional(),
    request: listRequestEchoSchema.optional(),
}).passthrough()

export type SpeciesEntryProjection = z.infer<typeof speciesEntrySummarySchema> & {
    speciesRef: string
    canonicalSmiles: string
    inchiKey: string
    formula: string | null
    charge: number
    multiplicity: number
}
export type ConformerProjection = z.infer<typeof conformerResponseSchema>["records"][number]
export type ConformerGroupFingerprint = z.infer<typeof conformerGroupFingerprintSchema>
export type ConformerRotorTorsion = z.infer<typeof conformerRotorTorsionSchema>

export async function loadSpeciesEntry(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SpeciesEntryProjection | null> {
    const query = new URLSearchParams({ species_entry_ref: entryRef })
    for (const include of ["thermo", "statmech", "transport", "conformers"]) query.append("include", include)
    const payload = await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(scientificSpeciesSearchSchema, payload, "species entry")
    for (const species of response.records) {
        const entry = species.entries.find((candidate) => candidate.species_entry_ref === entryRef)
        if (entry) return {
            ...entry,
            speciesRef: species.species_ref,
            canonicalSmiles: species.canonical_smiles,
            inchiKey: species.inchi_key,
            formula: species.formula,
            charge: species.charge,
            multiplicity: species.multiplicity,
        }
    }
    return null
}

const CONFORMER_INCLUDES = ["observations", "observation_details", "calculations", "geometries", "fingerprints"]

/** Joins an entry ref and a conformer group ref into the one string key the cached loaders take. */
export function conformerKey(entryRef: string, conformerGroupRef: string): string {
    return `${entryRef}@@${conformerGroupRef}`
}

/**
 * One conformer group, fetched by its own ref (`conformer_group_ref` filter) with the same includes a page of the list
 * carries, so it renders exactly as it would on its page. `null` when the entry has no such group. The selection of a
 * conformer must not depend on which page of the list it falls on; this is how one on another page is opened.
 */
export async function loadEntryConformerByRef(
    key: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerProjection | null> {
    const at = key.indexOf("@@")
    const entryRef = key.slice(0, at)
    const groupRef = key.slice(at + 2)
    const query = new URLSearchParams({ species_entry_ref: entryRef, conformer_group_ref: groupRef, limit: "1" })
    for (const include of CONFORMER_INCLUDES) query.append("include", include)
    const payload = await requestScientificJson(`/api/v1/scientific/conformers/search?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(conformerResponseSchema, payload, "conformer")
    return response.records.find((record) => record.conformer_group.conformer_group_ref === groupRef) ?? null
}

/**
 * Every conformer group of the entry WITHOUT the heavy includes, read page by page, so a record that cites a basin on a
 * page the reader has not opened still gets its label. Capped; a capped read returns what it has, and a ref it did not
 * reach is shown as its ref, never as a guess.
 */
export async function loadEntryConformerLabels(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerProjection[]> {
    const records: ConformerProjection[] = []
    let request = { offset: 0, limit: 200 }
    for (let page = 0; page < 25; page += 1) {
        const query = new URLSearchParams({ species_entry_ref: entryRef })
        for (const [key, value] of pageQuery(request)) query.set(key, value)
        const payload = await requestScientificJson(`/api/v1/scientific/conformers/search?${query}`, signal, onRateLimited)
        const response = parseScientificResponse(conformerResponseSchema, payload, "conformer")
        records.push(...response.records)
        const next = response.pagination ? nextPageRequest(response.pagination) : null
        if (!next) break
        request = next
    }
    return records
}

export type ConformerPage = { records: ConformerProjection[]; meta: ListMeta }

/**
 * One page of the entry's conformer groups. `pagedRefValue` is a plain entry ref (the first page, limit 50, exactly as
 * before) or a paged ref from `domain/paging.ts`'s `pagedRef`. The page's `meta` carries the server's own window, so a
 * list longer than a page is reported as such rather than shown as the whole.
 */
export async function loadEntryConformerPage(
    pagedRefValue: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerPage> {
    const { ref: entryRef, request } = parsePagedRef(pagedRefValue)
    const query = new URLSearchParams({ species_entry_ref: entryRef })
    for (const [key, value] of (request ? pageQuery(request) : new URLSearchParams({ limit: "50" }))) query.set(key, value)
    // `fingerprints` populates `conformer_group.fingerprint` -- the numeric
    // basin identity (quantized torsion bins + representative angles) that
    // distinguishes one group from another under the same entry. Small
    // (bounded per group, a handful of rotors) so requesting it on every
    // load of this list -- rather than lazily, per card -- costs nothing
    // meaningful and lets `ConformerSelector` render it without a second
    // round trip per card.
    //
    // `observation_details` is required alongside `observations` to get
    // each embedded observation's own `calculations` -- since issue #537
    // the default `observations` shape is a lean summary with no
    // `calculations` field at all. `domain/conformerEvidence.ts`'s
    // `optimizationStaging` reads `observation.calculations` per
    // observation (its `everyObservationLoaded` check) to tell a
    // per-observation staging verdict from a merely aggregate one; without
    // this token every observation's `calculations` would be `undefined`,
    // silently downgrading every conformer card on this page from a
    // per-observation staging statement to the aggregate-only one.
    for (const include of CONFORMER_INCLUDES) query.append("include", include)
    const payload = await requestScientificJson(`/api/v1/scientific/conformers/search?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(conformerResponseSchema, payload, "conformer")
    const count = response.records.length
    const pagination = response.pagination ?? { offset: 0, limit: 50, returned: count, total: count }
    return { records: response.records, meta: listMetaOf({ pagination, request: response.request }) }
}

/** The first page's records, for callers that only need the groups. */
export async function loadEntryConformers(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerProjection[]> {
    return (await loadEntryConformerPage(entryRef, signal, onRateLimited)).records
}
