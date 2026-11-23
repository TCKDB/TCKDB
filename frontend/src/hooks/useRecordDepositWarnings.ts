import { useScientificRecord } from "./useScientificRecord"
import type { DepositWarnings } from "../api/depositWarningsSchema"

/**
 * One record's deposit warnings, read lazily from its by-ref route (the entry-scoped statmech and transport lists carry
 * none). `null` while loading, `"unavailable"` when the read failed or was rate-limited, so the card can say so in one
 * line and render everything else. `load` is a stable module-level loader, which is also the request cache's namespace.
 */
export function useRecordDepositWarnings(
    ref: string,
    load: (ref: string, signal: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<DepositWarnings>,
): DepositWarnings | "unavailable" | null {
    const state = useScientificRecord(ref, load)
    if (state.status === "ready") return state.record
    if (state.status === "loading" || state.status === "retrying") return null
    return "unavailable"
}
