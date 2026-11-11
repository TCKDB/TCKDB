import type { SpeciesValues } from "../api/speciesValuesApi"
import { statmechRow, thermoRow, type NumberRow } from "./entryNumbers"

/** A record's row plus the version (species entry) the archive says it belongs to; `null` when it names none. */
export type SpeciesValueRow = NumberRow & { entryRef: string | null }

/** Where a record's values are evidenced: its row on its version's page, and that page. Both null when no version is named. */
export function valueRecordLinks(row: { kind: string; ref: string; entryRef: string | null; conformerGroupRef: string | null }): { to: string | null; entryPath: string | null } {
    if (!row.entryRef) return { to: null, entryPath: null }
    const entryPath = `/species-entries/${row.entryRef}`
    const query = row.conformerGroupRef ? `?conformer=${encodeURIComponent(row.conformerGroupRef)}` : ""
    return { to: `${entryPath}/${row.kind}${query}#${row.kind}-heading-${row.ref}`, entryPath }
}

/** Thermo rows, then statmech rows, each list in the order it was served. */
export function speciesValueRows(values: SpeciesValues): SpeciesValueRow[] {
    return [
        ...values.thermo.records.map(({ entryRef, record }) => ({ ...thermoRow(record), entryRef })),
        ...values.statmech.records.map(({ entryRef, record }) => ({ ...statmechRow(record), entryRef })),
    ]
}
