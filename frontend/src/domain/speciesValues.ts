import type { SpeciesValues } from "../api/speciesValuesApi"
import { statmechRow, thermoRow, type NumberRow } from "./entryNumbers"

/** A record's row plus the version (species entry) the archive says it belongs to; `null` when it names none. */
export type SpeciesValueRow = NumberRow & { entryRef: string | null }

/** Thermo rows, then statmech rows, each list in the order it was served. */
export function speciesValueRows(values: SpeciesValues): SpeciesValueRow[] {
    return [
        ...values.thermo.records.map(({ entryRef, record }) => ({ ...thermoRow(record), entryRef })),
        ...values.statmech.records.map(({ entryRef, record }) => ({ ...statmechRow(record), entryRef })),
    ]
}
