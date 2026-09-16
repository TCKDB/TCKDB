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
 * - `no-page`: the record IS named, there is simply nowhere to open it. The
 *   ref is still shown so it can be pasted into the API.
 * - `unnamed`: the backend could not name the record at all -- either its
 *   table has no `public_ref` (only `applied_energy_correction` today), or
 *   the row is gone. A missing row is a real defect and must not be filed
 *   under the same sentence as a missing page.
 *
 * **What `no-page` deliberately does NOT distinguish,** because it cannot:
 * a record whose type has no page AND whose parent could not be named looks
 * identical here to one whose type simply has no parent. Both arrive as two
 * nulls, because the wire contract is that `container_type` and
 * `container_ref` are null together -- a type without a ref names nothing a
 * client could address, so sending half a pair would be worse.
 *
 * Telling those apart needs a third wire field saying WHY the container is
 * absent, and that is not worth it here: the backend's four null causes are
 * enumerated in `app/services/record_containers.py`, and the one that would
 * make the difference (the parent row is gone) is unreachable in production.
 * The foreign keys are ON DELETE NO ACTION, so Postgres refuses to delete a
 * parent that still has children; producing it at all takes a deferred
 * -constraint transaction, which is exactly how the backend test for it is
 * written. So rather than assert a cause it cannot check, the `no-page`
 * sentence states only the two things that are certainly true -- the type
 * has no page, and nothing else could be opened either. It used to read "no
 * page for this record type yet", which named the missing page as the sole
 * reason and would have been actively misleading in that case.
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

/**
 * A record type's display name for a reviewer, singular or plural.
 *
 * `recordTypeWords` above is deliberately literal -- it is built to be
 * combined into a sentence alongside a ref ("shown on species entry
 * spc_..."), and drifting from the backend's own vocabulary there would
 * be confusing. This is a different job: a heading over a group of
 * records in the review queue (task #269), where "statmech" and
 * "kinetics" read as jargon and "applied_energy_correction" reads as
 * a table name, not a sentence fragment. Unmapped types fall back to
 * `recordTypeWords`, capitalized -- never blank, and never invented
 * chemistry, just plainer English for the same word.
 */
const _GROUP_LABELS: Readonly<Record<string, { one: string; many: string }>> = {
    species: { one: "Species", many: "Species" },
    species_entry: { one: "Species entry", many: "Species entries" },
    conformer_group: { one: "Conformer group", many: "Conformer groups" },
    conformer_observation: {
        one: "Conformer observation",
        many: "Conformer observations",
    },
    reaction: { one: "Reaction", many: "Reactions" },
    reaction_entry: { one: "Reaction entry", many: "Reaction entries" },
    transition_state: { one: "Transition state", many: "Transition states" },
    transition_state_entry: {
        one: "Transition state entry",
        many: "Transition state entries",
    },
    calculation: { one: "Calculation", many: "Calculations" },
    statmech: { one: "Statistical mechanics", many: "Statistical mechanics" },
    thermo: { one: "Thermochemistry", many: "Thermochemistry" },
    kinetics: { one: "Kinetics", many: "Kinetics" },
    transport: { one: "Transport", many: "Transport" },
    network: { one: "Network", many: "Networks" },
    network_solve: { one: "Network solve", many: "Network solves" },
    applied_energy_correction: {
        one: "Energy correction",
        many: "Energy corrections",
    },
    artifact: { one: "Artifact", many: "Artifacts" },
}

function _capitalize(words: string): string {
    return words.length === 0 ? words : words[0].toUpperCase() + words.slice(1)
}

/** "Energy correction" (count 1) / "Energy corrections" (count != 1). */
export function recordTypeGroupLabel(recordType: string, count: number): string {
    const mapped = _GROUP_LABELS[recordType]
    if (mapped) return count === 1 ? mapped.one : mapped.many
    return _capitalize(recordTypeWords(recordType))
}
