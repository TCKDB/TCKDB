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
 * `network_solve`, `applied_energy_correction`, `artifact`).
 *
 * That null used to be the end of the story, and it cost the review queue
 * 30% of its rows: 385 of 1,299 could not be opened, including every
 * thermo, statmech, kinetics and energy correction in the archive. Six of
 * those eight types are not unreachable at all -- they are rendered inside
 * their parent, and the review row now says which parent. See
 * `resolveRecordLocation` at the bottom of this file, which is what the
 * queue calls; `recordRoute` remains the primitive underneath it, used
 * directly only where no container is available (Machine findings).
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

/**
 * Where one record can be opened, and -- when it cannot -- which of the two
 * reasons applies.
 *
 * `recordRoute` alone was not enough, and the gap was 30% of the review
 * queue. Eight of the seventeen record types have no page, but six of those
 * eight are *rendered*: thermo and statmech as tabs on the species entry
 * page, kinetics and transition states as sections on the reaction entry
 * page. The backend now says which parent (`container_type`/`container_ref`
 * on the review row), so those rows can be opened after all -- at their
 * container.
 *
 * The two absent cases are kept apart deliberately. Eight identical "cannot
 * be named" lines told a curator nothing about whether they were looking at
 * a bug or a known gap, and those are different things to do about:
 *
 * - `no-page`: the record IS named, there is simply nowhere to open it. A
 *   known gap. The ref is still shown so it can be pasted into the API.
 * - `unnamed`: the backend could not name the record at all -- either its
 *   table has no `public_ref` (only `applied_energy_correction` today), or
 *   the row is gone. A missing row is a real defect and must not be filed
 *   under the same sentence as a missing page.
 */
export type RecordLocation =
    /** The record's own page. */
    | { kind: "record"; href: string; ref: string }
    /** No page of its own; open the record it is part of. */
    | {
          kind: "container"
          href: string
          /** Null when the record has no ref of its own to distinguish. */
          ref: string | null
          containerType: string
          containerRef: string
      }
    /** Named, but nothing to open -- neither it nor its container has a page. */
    | { kind: "no-page"; ref: string }
    /** Not even named. */
    | { kind: "unnamed" }

/**
 * Resolve a review row to where its record can be seen.
 *
 * Order matters and is the whole policy: **the record's own page wins.** The
 * backend reports a container for every type that has an owning parent,
 * including types that are perfectly addressable in their own right (a
 * species entry belongs to a species), so without this precedence a
 * `species_entry` row would link to its species -- a different record from
 * the one under review.
 */
export function resolveRecordLocation(
    recordType: string,
    publicRef: string | null,
    containerType: string | null,
    containerRef: string | null,
): RecordLocation {
    const own = recordRoute(recordType, publicRef)
    if (own !== null && publicRef) return { kind: "record", href: own, ref: publicRef }

    // Both halves or neither: a ref with no type cannot be routed (the
    // `spc_`/`rxn_` prefixes are a naming convention, not a contract this
    // module may parse), and a type with no ref names nothing.
    if (containerType && containerRef) {
        const href = recordRoute(containerType, containerRef)
        if (href !== null) {
            return {
                kind: "container",
                href,
                ref: publicRef ?? null,
                containerType,
                containerRef,
            }
        }
    }

    if (publicRef) return { kind: "no-page", ref: publicRef }
    return { kind: "unnamed" }
}

/**
 * A record type as a reader should see it: `species_entry` -> "species entry".
 *
 * Only ever applied to a CONTAINER type, to build the sentence "shown on
 * species entry spc_...". The record's own type is still printed verbatim,
 * because that column is the queue's index of what kind of claim is being
 * judged and a curator reads it against the backend's own vocabulary.
 */
export function recordTypeWords(recordType: string): string {
    return recordType.replace(/_/g, " ")
}
