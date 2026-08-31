import type { BrowseKind } from "../api/browseApi"

const KIND_PLURAL: Record<BrowseKind, string> = {
    species: "species records",
    vdw: "van der Waals complexes",
    transition_state: "transition-state records",
}

/**
 * The archive-wide absence message, one per kind. This is the copy path the
 * design brief calls out by name: a zero-row `vdw` result must read as a
 * FACT about the archive ("nothing of this kind exists yet"), never as a
 * broken search or a wrong filter -- see `docs` for the measured 60/0
 * minimum/vdw_complex split. `species` and `transition_state` carry the
 * same honest phrasing for symmetry even though the live corpus (59/34
 * records respectively) never exercises this branch for them today; the
 * archive is allowed to become empty of either in the future and the UI
 * should already say the true thing if it does.
 *
 * Never called when filters narrowed a nonzero corpus to zero -- that is
 * `filteredEmptyMessage` below, a deliberately different message. See
 * `BrowsePage`'s use of `hasActiveFilters` to choose between the two.
 */
export function archiveEmptyMessage(kind: BrowseKind): string {
    if (kind === "vdw") {
        return "No van der Waals complexes have been deposited in this archive yet. That is a fact about the archive, " +
            "not a failed search -- species and transition-state records are unaffected."
    }
    return `No ${KIND_PLURAL[kind]} have been deposited in this archive yet.`
}

/** A nonzero corpus exists for `kind`, but the current filters excluded every row of it. */
export function filteredEmptyMessage(kind: BrowseKind): string {
    return `No ${KIND_PLURAL[kind]} match these filters. Clear or widen them to see more of the archive.`
}
