import { useEffect, useState } from "react"
import { loadReactionEnergyProfile, type EnergyProfileRead } from "../api/reactionEnergyProfileApi"

/** One energy-profile read per reaction entry, each kept or marked failed; one entry's failure never hides another's. */
export type EnergyProfilesState =
    | { key: string; status: "loading" }
    | { key: string; status: "ready"; reads: ReadonlyMap<string, EnergyProfileRead | null> }

/** Reads the energy profile of each listed reaction entry once, and lets go of the requests on unmount. */
export function useEnergyProfiles(entryRefs: readonly string[]): EnergyProfilesState {
    const key = entryRefs.join(",")
    const [state, setState] = useState<EnergyProfilesState>({ key, status: "loading" })
    useEffect(() => {
        const controller = new AbortController()
        const refs = key ? key.split(",") : []
        void Promise.all(refs.map((ref) => loadReactionEnergyProfile(ref, controller.signal).then((read) => [ref, read] as const, () => [ref, null] as const)))
            .then((pairs) => { if (!controller.signal.aborted) setState({ key, status: "ready", reads: new Map(pairs) }) })
        return () => controller.abort()
    }, [key])
    return state.key === key ? state : { key, status: "loading" }
}
