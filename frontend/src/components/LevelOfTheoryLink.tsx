import { Link } from "react-router-dom"
import { lotLabel } from "../api/scientificSchemas"
import { levelOfTheoryPath } from "../domain/methodsLinks"

/**
 * The one place `lotLabel()`'s compact "method/basis" text becomes a real
 * link into `/methods/:lotRef` (`LevelOfTheoryPage`) -- see
 * `docs/plans/methods-surface.md` §2.4/§6 (`plan-methods-surface-v2`, not
 * committed to this repo) for the 11-file sweep this replaces (every
 * `lotLabel()` call site rendered its output as plain, unlinked text
 * before this PR).
 *
 * Falls back to the SAME plain text `lotLabel()` always rendered when
 * `level_of_theory_ref` is absent -- every call site's rendered text stays
 * byte-identical either way; only whether it is now wrapped in an anchor
 * changes. `level_of_theory_ref` is a required field on the backend's
 * `LevelOfTheorySummary` (`scientific_common.py:237`) so in practice this
 * archive never actually hits the fallback branch for a level of theory
 * that is present at all -- it exists so a malformed or future-shape
 * response degrades to today's plain text instead of a broken link.
 */
export function LevelOfTheoryLink({ levelOfTheory }: {
    levelOfTheory: { method: string; basis?: string | null; display?: string; level_of_theory_ref?: string }
}) {
    const label = lotLabel(levelOfTheory)
    if (!levelOfTheory.level_of_theory_ref) return <>{label}</>
    return <Link to={levelOfTheoryPath(levelOfTheory.level_of_theory_ref)}>{label}</Link>
}
