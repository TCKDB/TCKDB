import { EMPTY_BROWSE_FILTERS, loadBrowse } from "../api/browseApi"
import { loadLevelOfTheoryBrowse } from "../api/methodsApi"
import { useLoaded } from "./useLoaded"

const ONE_ROW = { offset: 0, limit: 1 }

async function moleculeTotal(_key: string, signal?: AbortSignal): Promise<number> {
    return (await loadBrowse("species", EMPTY_BROWSE_FILTERS, ONE_ROW.offset, ONE_ROW.limit, signal)).pagination.total
}

async function reactionTotal(_key: string, signal?: AbortSignal): Promise<number> {
    return (await loadBrowse("reaction", EMPTY_BROWSE_FILTERS, ONE_ROW.offset, ONE_ROW.limit, signal)).pagination.total
}

async function levelTotal(_key: string, signal?: AbortSignal): Promise<number> {
    return (await loadLevelOfTheoryBrowse(signal, {}, ONE_ROW)).pagination.total
}

/** Each count is the server's own `pagination.total` from the read the matching index page makes; null until it arrives or when the read fails. */
export type CountKey = "molecules" | "reactions" | "levels"
export type HomeCounts = { molecules: number | null; reactions: number | null; levels: number | null; pending: boolean }

export function useHomeCounts(): HomeCounts {
    const molecules = useLoaded(moleculeTotal, "molecules")
    const reactions = useLoaded(reactionTotal, "reactions")
    const levels = useLoaded(levelTotal, "levels")
    return {
        molecules: molecules.status === "ready" ? molecules.data : null,
        reactions: reactions.status === "ready" ? reactions.data : null,
        levels: levels.status === "ready" ? levels.data : null,
        pending: [molecules, reactions, levels].some((read) => read.status === "idle" || read.status === "loading"),
    }
}
