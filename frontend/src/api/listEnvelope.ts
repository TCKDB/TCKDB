import { z } from "zod"

// ---------------------------------------------------------------------------
// The parts of a scientific list response every paged list shares: the
// `pagination` window and the two statements the `request` echo makes about
// the list (`sort`, the display order it was served in, and
// `hidden_by_default`, how many records the default visibility left out).
// Both echo fields are optional here on purpose: an older response, or a
// route that makes no statement, parses as "no statement", never as a default
// that would read as a claim.
// ---------------------------------------------------------------------------

export const paginationSchema = z.object({
    offset: z.number(),
    limit: z.number(),
    returned: z.number(),
    total: z.number(),
}).passthrough()

const orderKeySchema = z.object({
    key: z.string(),
    direction: z.string(),
    meaning: z.string(),
}).passthrough()

/** `OrderEcho` (`backend/app/schemas/reads/scientific_common.py`): the display order a list was served in. */
export const orderEchoSchema = z.object({
    keys: z.array(orderKeySchema),
    statement: z.string().nullish(),
    requested_by: z.string().nullish(),
}).passthrough()

/** `HiddenByDefault`: how many records the default visibility hid, per excluded status. */
export const hiddenByDefaultSchema = z.object({
    counts: z.record(z.string(), z.number()),
    include_with: z.record(z.string(), z.string()).nullish(),
    counted: z.string().nullish(),
}).passthrough()

export const listRequestEchoSchema = z.object({
    sort: orderEchoSchema.nullish(),
    hidden_by_default: hiddenByDefaultSchema.nullish(),
}).passthrough()

export type Pagination = z.infer<typeof paginationSchema>
export type OrderEcho = z.infer<typeof orderEchoSchema>
export type HiddenByDefault = z.infer<typeof hiddenByDefaultSchema>
export type ListRequestEcho = z.infer<typeof listRequestEchoSchema>

/** What a paged list carries beyond its records, in the shape `ListPager` reads. */
export type ListMeta = {
    pagination: Pagination
    sort: OrderEcho | null
    hiddenByDefault: HiddenByDefault | null
}

export function listMetaOf(response: { pagination: Pagination; request?: ListRequestEcho | null }): ListMeta {
    return {
        pagination: response.pagination,
        sort: response.request?.sort ?? null,
        hiddenByDefault: response.request?.hidden_by_default ?? null,
    }
}
