import { Link } from "react-router-dom"
import type { TransitionStateBrowseRecord } from "../api/browseApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { lotLabel } from "../api/scientificSchemas"

function token(value: string) {
    return value.replaceAll("_", " ")
}

// Single named seam for this row's link target. The in-flight ROUTING batch
// is repointing TS browse rows at a new `/transition-state-entries/:ref`
// page instead of the reaction they belong to -- when that lands, this is
// the only line it needs to change. Do not inline this expression anywhere
// else in this file.
function tsRowTarget(record: TransitionStateBrowseRecord): string | null {
    return record.transition_state_entry.transition_state_entry_ref
        ? `/transition-state-entries/${record.transition_state_entry.transition_state_entry_ref}`
        : null
}

/**
 * Headline stages shown on the row (item 1): "opt" and "sp" preferred (the
 * composite-workflow pair -- cheap geometry, then one accurate single
 * point, per `levels_of_theory.py`'s own module docstring), falling back to
 * whichever stages ARE present when neither ran (e.g. only `freq`/`irc`).
 * Capped at two -- enough to distinguish same-reaction deposits without
 * turning the row into a calculation-detail dump.
 */
const HEADLINE_STAGE_ORDER = ["opt", "sp", "freq", "irc", "path_search"] as const

function selectedStages(levels: Record<string, unknown[]> | undefined): string[] {
    if (!levels) return []
    const present = HEADLINE_STAGE_ORDER.filter((stage) => (levels[stage]?.length ?? 0) > 0)
    const headline = present.filter((stage) => stage === "opt" || stage === "sp")
    return (headline.length ? headline : present).slice(0, 2)
}

/**
 * Item 1's provenance line: "the highest-stage level of theory (and
 * software when served)". Both `levels_of_theory` and `software` on
 * `evidence_summary` are per-calculation-type maps (see
 * `scientific_transition_state.py`'s `TransitionStateEntryEvidenceSummary`)
 * -- this reads the same `selectedStages` set out of each so a stage's
 * level and its software always describe the SAME calculation. A stage can
 * carry more than one distinct level (the same reason
 * `levels_of_theory.py` returns a list, not a scalar: benchmark
 * comparisons at one stage are real, deposited data) -- every distinct
 * level for a selected stage is joined, never just the first.
 *
 * `software` return value is `null` -- as opposed to the literal text
 * "software not recorded" -- specifically when `evidence_summary.software`
 * itself is `undefined`: an OLDER API response that never served this
 * field at all is a claim about the WIRE VERSION, not about the record,
 * and must not be rendered as if the archive had asserted "no software on
 * this calculation". "software not recorded" is reserved for the case the
 * CURRENT API actually asserts it (the field is present, but a selected
 * stage's list is empty) -- that is a real statement about the record,
 * per the "software must always be stated" invariant. The caller renders
 * the `· {software}` clause only when this is non-null.
 */
function provenanceSummary(
    record: TransitionStateBrowseRecord,
): { levelOfTheory: string; software: string | null } {
    const levels = record.evidence_summary.levels_of_theory
    const software = record.evidence_summary.software
    const stages = selectedStages(levels)

    if (stages.length === 0) {
        return {
            levelOfTheory: "level of theory not recorded",
            software: software === undefined ? null : "software not recorded",
        }
    }

    const levelOfTheory = stages
        .map((stage) => `${stage} ${levels![stage].map((entry) => lotLabel(entry)).join(", ")}`)
        .join(" · ")

    if (software === undefined) {
        return { levelOfTheory, software: null }
    }

    const perStageSoftware = stages.map((stage) => {
        const entries = software[stage]
        return entries && entries.length > 0
            ? entries
                .map((entry) => (entry.version ? `${entry.software} ${entry.version}` : entry.software))
                .join(", ")
            : null
    })
    const distinct = [...new Set(perStageSoftware.filter((value): value is string => Boolean(value)))]

    let softwareSummary: string
    if (distinct.length === 0) {
        softwareSummary = "software not recorded"
    } else if (distinct.length === 1) {
        softwareSummary = distinct[0]
    } else {
        softwareSummary = stages
            .map((stage, index) => `${stage} ${perStageSoftware[index] ?? "not recorded"}`)
            .join(" · ")
    }

    return { levelOfTheory, software: softwareSummary }
}

/** `created_at` truncated to a plain date -- same slice(0, 10) pattern `EntryTransportSection.tsx`'s `isoDate` uses for the same ISO-timestamp-to-date reduction. */
function depositedDate(createdAt: string | undefined): string | null {
    return createdAt ? createdAt.slice(0, 10) : null
}

/**
 * A transition state has no formula of its own -- it is identified by the
 * REACTION it connects (see `TransitionStatesBrowseRequest`'s module
 * docstring), so this row leads with the rendered equation and reaction
 * family rather than a molecular headline.
 *
 * Link semantics (item 2, reworked from an earlier whole-row `<Link>`):
 * two independent reviewers found that wrapping the ref inside the link
 * made it (a) part of a ~170-character accessible name spelled out letter
 * by letter (the ref alone is 26 characters) and (b) unselectable --
 * dragging over the ref started a link drag instead of a text selection.
 * Fixed with a stretched-link overlay (`browse.css`'s `.ts-browse-row
 * .browse-row-title::after`, `inset: 0` against `.ts-browse-row`'s
 * `position: relative`): the `<Link>` wraps ONLY the equation (plus a
 * screen-reader-only mention of the TS label, so the accessible name is
 * "equation (+ label)" rather than the equation alone). Measured against
 * an earlier claim that this pattern was "already used by
 * `SpeciesBrowseRow.tsx`" -- it is not; that row's own title `<Link>` has
 * no stretched-link overlay and no full-row click target at all today.
 * The overlay here is new, scoped to `.ts-browse-row` so it cannot reach
 * the species row.
 *
 * The click target is the row MINUS the footer line (`.browse-row-
 * footer`, evidence text + ref): that element alone carries `position:
 * relative` to paint above the overlay, because the ref is the one thing
 * on the row a reader needs to drag-select. `.browse-row-entries` (the
 * pills) and `.browse-row-provenance` (the level-of-theory/software line)
 * carry no such value and are NOT lifted above the overlay -- an earlier
 * revision lifted both anyway "to be safe", which shrank the actual click
 * target to about 62% of the row's box while this comment still claimed
 * "the full row". See `browse.css`'s comment beside `.ts-browse-row
 * .browse-row-footer` for the CSS-painting-order mechanics.
 *
 * Earlier revision of this doc comment also claimed the row links to
 * `/reactions/:ref` and that "no such page exists" -- both stale by the
 * time of this pass: the link target is
 * `/transition-state-entries/:transition_state_entry_ref` (`tsRowTarget`
 * above), which does exist.
 */
export function TransitionStateBrowseRow({ record }: { record: TransitionStateBrowseRecord }) {
    const evidence = [
        record.evidence_summary.has_opt && "opt",
        record.evidence_summary.has_freq && "freq",
        record.evidence_summary.has_sp && "sp",
        record.evidence_summary.has_irc && "irc",
        record.evidence_summary.has_path_search && "path search",
        record.evidence_summary.has_geometry_validation && "geometry validation",
        record.evidence_summary.has_scf_stability && "scf stability",
    ].filter((label): label is string => Boolean(label))

    const equation = record.reaction.equation ?? "Equation not recorded"
    const target = tsRowTarget(record)

    const entryLabel = record.transition_state.label ?? "Unlabeled transition state"
    // Item 3: the pill is the entry's STATUS only now ("optimized"), not
    // the label fused onto it ("TS0 · optimized") -- the label moved to
    // the meta line as plain text, alongside family/charge/spin/deposited.
    const entryStatusText = token(record.transition_state_entry.status)
    const entryReviewStatusText = token(record.transition_state_entry.review.status)
    const entryRef = record.transition_state_entry.transition_state_entry_ref
    const deposited = depositedDate(record.transition_state_entry.created_at)
    const { levelOfTheory, software } = provenanceSummary(record)

    const evidenceText = evidence.length
        ? `Evidence: ${evidence.join(" · ")} (${record.evidence_summary.calculation_count} calculations)`
        : "No calculation evidence recorded"

    return (
        <li className="browse-row card ts-browse-row">
            <div className="browse-row-headline">
                {target
                    ? (
                        // `aria-label` (not a visually-hidden child span) is
                        // what makes the accessible name an EXACT, testable
                        // string ("equation (+ label)") -- letting the
                        // browser's accname algorithm concatenate a visible
                        // text node with a nested element's trimmed text
                        // produced inconsistent spacing ("A <=> B(TS0)",
                        // missing the space before the parenthesis) that
                        // depended on undocumented whitespace-collapsing
                        // behavior around JSX text nodes. The equation still
                        // renders as ordinary visible text as the Link's
                        // child; `aria-label` only overrides what a screen
                        // reader announces, not what is on screen -- the
                        // label is separately visible on `.browse-row-meta`
                        // below, so this does not duplicate it visually.
                        <Link
                            aria-label={`${equation} (${entryLabel})`}
                            className="browse-row-title"
                            to={target}
                        >
                            {equation}
                        </Link>
                    )
                    : <span className="browse-row-title">{equation}</span>}
                <span className="browse-row-meta">
                    {entryLabel}
                    {" · "}
                    {record.reaction.family ? token(record.reaction.family) : <span className="absent">family not recorded</span>}
                    {" · "}
                    charge {chargeDisplay(record.transition_state_entry.charge)} · spin {spinDisplay(record.transition_state_entry.multiplicity)}
                    {deposited && <> · deposited {deposited}</>}
                </span>
            </div>
            <p className="browse-row-provenance">
                {levelOfTheory}
                {software !== null && <> · {software}</>}
            </p>
            <ul className="browse-row-entries">
                <li className="browse-entry-chip">
                    <span className="value-pill browse-entry-kind-pill">{entryStatusText}</span>
                    <span className="value-pill value-pill--muted browse-entry-review">
                        {entryReviewStatusText}
                    </span>
                </li>
            </ul>
            <p className="browse-row-footer">
                <span className="browse-row-evidence">{evidenceText}</span>
                <code className="browse-ref data">{entryRef}</code>
            </p>
        </li>
    )
}
