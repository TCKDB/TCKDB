/**
 * Where a record's public ref can be opened, if anywhere.
 *
 * Machine findings lists concerns against records of many types, and a
 * curator's first move is always the same: look at the thing. This is the
 * one place that knows which types have a page and what its path is.
 *
 * Deliberately built from `App.tsx`'s routes rather than guessed from the
 * type name: several do not follow the obvious pattern (`reaction` lives
 * at `/reactions`, not `/chem-reactions`; `conformer_observation` is
 * plural-hyphenated). A guessed path produces a link that 404s, which is
 * worse than no link -- a curator who clicks and lands nowhere learns to
 * stop clicking.
 *
 * **Returning `null` is a normal answer, not a failure.** Eight of the
 * seventeen `SubmissionRecordType` members have no page today
 * (`transition_state`, `statmech`, `thermo`, `kinetics`, `transport`,
 * `network_solve`, `applied_energy_correction`, `artifact`). The queue
 * shows the ref as plain text for those, so a curator can still copy it
 * into the API or a search, rather than pretending the record is
 * unreachable.
 */

/** Record types that have a page, and the route prefix that serves it. */
const ROUTE_PREFIX_BY_RECORD_TYPE: Readonly<Record<string, string>> = {
    species: "/species",
    species_entry: "/species-entries",
    conformer_group: "/conformer-groups",
    conformer_observation: "/conformer-observations",
    calculation: "/calculations",
    reaction: "/reactions",
    reaction_entry: "/reaction-entries",
    transition_state_entry: "/transition-state-entries",
    network: "/networks",
}

/**
 * The in-app path for a record, or `null` when it cannot be linked.
 *
 * `null` for two different reasons, and the caller does not need to tell
 * them apart: the type has no page, or the backend could not name the
 * record (`record_public_ref` is null, which happens for
 * `applied_energy_correction` and for a record deleted since its task was
 * raised).
 */
export function recordRoute(
    recordType: string,
    publicRef: string | null,
): string | null {
    if (!publicRef) return null
    const prefix = ROUTE_PREFIX_BY_RECORD_TYPE[recordType]
    if (prefix === undefined) return null
    return `${prefix}/${encodeURIComponent(publicRef)}`
}

/** Whether this record type has a page at all, independent of any one record. */
export function recordTypeHasPage(recordType: string): boolean {
    return recordType in ROUTE_PREFIX_BY_RECORD_TYPE
}

/** The record types this module can link, for tests and for a completeness check. */
export const LINKABLE_RECORD_TYPES: readonly string[] = Object.keys(
    ROUTE_PREFIX_BY_RECORD_TYPE,
)
