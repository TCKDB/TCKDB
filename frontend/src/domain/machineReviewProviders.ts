import type { Provider } from "../types/machineReviewJob"

/**
 * Which reviewers the caller may ask, and why not when none. The server decides each availability; this only reads it.
 */

/** Availability tokens from the one that blocks every reviewer to the one that blocks a single reviewer. */
const BLOCKING_ORDER = [
    "mode_off",
    "machine_review_disabled",
    "curator_requests_disabled",
    "not_allowed_for_user",
    "provider_not_configured",
    "remote_runner_unavailable",
]

export function okProviders(providers: readonly Provider[]): Provider[] {
    return providers.filter((p) => p.availability === "ok")
}

/**
 * The availability token that best explains why nothing can be requested, or `null` when something can.
 * A deployment-wide reason outranks a reason that holds for one reviewer only.
 */
export function blockingAvailability(providers: readonly Provider[]): string | null {
    if (okProviders(providers).length > 0) return null
    const present = new Set(providers.map((p) => p.availability))
    for (const token of BLOCKING_ORDER) if (present.has(token)) return token
    return providers[0]?.availability ?? null
}
