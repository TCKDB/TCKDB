import { createContext, useContext } from "react"

/** Page-level slot: at most one live 3D viewer. A newcomer evicts the holder, which returns to its picture. */
export type LiveViewerSlot = {
    claim: (id: string, onEvicted: () => void) => void
    release: (id: string) => void
}

export function createLiveViewerSlot(): LiveViewerSlot {
    let holder: { id: string; onEvicted: () => void } | null = null
    return {
        claim(id, onEvicted) {
            const previous = holder
            holder = { id, onEvicted }
            if (previous && previous.id !== id) previous.onEvicted()
        },
        release(id) {
            if (holder?.id === id) holder = null
        },
    }
}

/** Without a provider there is no one-viewer limit. */
export const LiveViewerSlotContext = createContext<LiveViewerSlot | null>(null)

export function useLiveViewerSlot(): LiveViewerSlot | null {
    return useContext(LiveViewerSlotContext)
}
