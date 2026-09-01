import { useEffect, useState } from "react"
import { loadEntryConformers, loadSpeciesEntry } from "../api/speciesEntryApi"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import { ScientificApiError } from "../api/scientificTransport"
import { loadSpeciesSinglePointEnergies } from "../api/speciesCalculationsApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"

export type SpeciesEntryState =
    | { entryRef: string; entry: SpeciesEntryProjection; conformers: ConformerProjection[]; spEnergies: SpeciesCalculationEnergyRecord[] }
    | { entryRef: string; status: "missing" | "malformed" | "http-error" }

export function useSpeciesEntry(entryRef: string): SpeciesEntryState | null {
    const [state, setState] = useState<SpeciesEntryState | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        void Promise.all([
            loadSpeciesEntry(entryRef, controller.signal),
            loadEntryConformers(entryRef, controller.signal),
            // Best-effort enrichment (see the module docstring on
            // `loadSpeciesSinglePointEnergies`) -- it resolves to `[]`
            // rather than rejecting, so a failure here never turns the
            // whole entry page into an error state.
            loadSpeciesSinglePointEnergies(entryRef, controller.signal),
        ])
            .then(([entry, conformers, spEnergies]) => {
                setState(entry ? { entryRef, entry, conformers, spEnergies } : { entryRef, status: "missing" })
            })
            .catch((error: unknown) => {
                if (!(error instanceof DOMException && error.name === "AbortError")) {
                    const status = error instanceof ScientificApiError && error.status === 200
                        ? "malformed"
                        : "http-error"
                    setState({ entryRef, status })
                }
            })
        return () => controller.abort()
    }, [entryRef])

    return state
}
