import type { CalculationDependency } from "../api/calculationApi"

/**
 * Which pass of a two-step ARC optimisation this calculation is: a quick
 * coarse pass, then a fine pass at the same level, linked by an
 * `optimized_from` dependency edge (parent = coarse, child = fine).
 * `"unknown"` is a real, common third state -- not "single pass" -- see
 * `optimisationStage`'s own docstring.
 */
export type OptimisationStageKind = "coarse" | "fine" | "unknown"

/** Discriminated on `kind` so a caller that has checked `kind !== "unknown"`
 * gets `otherRef` narrowed to a required `string`, never an optional one it
 * has to re-check. */
export type OptimisationStage =
    | { kind: "unknown" }
    | { kind: "coarse" | "fine"; otherRef: string }

/**
 * ONE wording table for every place `CalculationDetailPage.tsx` names an
 * optimisation stage -- the h1 subject prefix ("Coarse optimisation of
 * C9H9"), the stage-strip box label, and the kicker suffix ("… ·
 * coarse pass", rendered upper-case by the kicker's own CSS, matching the
 * existing "Frequency calculation · deposited evidence" casing) all read
 * the SAME two entries, so a future rewrite can't drift the title one way
 * and the strip another. Never define either string a second time at a
 * call site -- see the literal-pinned test in `optimisationStage.test.ts`.
 */
export const OPTIMISATION_STAGE_WORDS: Record<Exclude<OptimisationStageKind, "unknown">, {
    /** h1 subject prefix ("{label} of C9H9") AND stage-strip box label. */
    label: string
    /** Kicker suffix, after "{type} calculation · ". */
    kickerSuffix: string
}> = {
    coarse: { label: "Coarse optimisation", kickerSuffix: "coarse pass" },
    fine: { label: "Fine optimisation", kickerSuffix: "fine pass" },
}

/** Kicker suffix for an `opt` calculation with no known stage -- the SAME
 * "deposited evidence" every non-`opt` calculation type's kicker already
 * uses (`CalculationDetailPage.tsx`), kept here so the two stage-aware
 * kicker helpers below have one shared "nothing known" fallback. */
export const OPTIMISATION_STAGE_UNKNOWN_KICKER_SUFFIX = "deposited evidence"

/**
 * "Which of N optimisations is this" for an opt calculation, read from the
 * SAME `dependencies` payload the Related-calculations graph renders --
 * never a second, independently-derived graph read. A parent-side
 * `optimized_from` edge means this calculation was later refined further
 * (it is the coarse pass); a child-side one means this calculation IS the
 * refinement.
 *
 * Neither present does NOT mean this is confidently a single pass --
 * review finding: an earlier version of this page asserted a stage from an
 * absence of edges, including on a calculation with no dependency edges at
 * all (nothing to read a stage from, one way or the other). An edge that
 * doesn't exist in the archive is not evidence there is no refinement
 * stage, only that this page has no evidence of one -- so the no-edge case
 * returns `"unknown"`, never a guessed "single pass".
 */
export function optimisationStage(dependencies: CalculationDependency[]): OptimisationStage {
    const parentEdge = dependencies.find((dep) => dep.direction === "parent" && dep.role === "optimized_from")
    if (parentEdge) return { kind: "coarse", otherRef: parentEdge.child_calculation_ref }
    const childEdge = dependencies.find((dep) => dep.direction === "child" && dep.role === "optimized_from")
    if (childEdge) return { kind: "fine", otherRef: childEdge.parent_calculation_ref }
    return { kind: "unknown" }
}
