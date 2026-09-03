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
 * level and its software always describe the SAME calculation. Software is
 * never silently omitted: a stage with no software on record renders
 * "software not recorded", never a blank -- see the module's "software
 * must always be stated" invariant. When several selected stages share one
 * software name, it is stated once; when they differ, each stage gets its
 * own software note.
 */
function provenanceSummary(record: TransitionStateBrowseRecord): { levelOfTheory: string; software: string } {
    const levels = record.evidence_summary.levels_of_theory
    const software = record.evidence_summary.software
    const stages = selectedStages(levels)

    if (stages.length === 0) {
        return { levelOfTheory: "level of theory not recorded", software: "software not recorded" }
    }

    const levelOfTheory = stages.map((stage) => `${stage} ${lotLabel(levels![stage][0])}`).join(" · ")

    const perStageSoftware = stages.map((stage) => {
        const entries = software?.[stage]
        return entries && entries.length > 0
            ? (entries[0].version ? `${entries[0].software} ${entries[0].version}` : entries[0].software)
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
 * Fixed with the same stretched-link technique `SpeciesBrowseRow.tsx`
 * already used for its own title-only link (`browse.css`'s
 * `.ts-browse-row .browse-row-title::after`, `inset: 0` against
 * `.ts-browse-row`'s `position: relative`): the `<Link>` wraps ONLY the
 * equation (plus a screen-reader-only mention of the TS label, so the
 * accessible name is "equation (+ label)" rather than the equation alone),
 * while the ref/pills/evidence line sit OUTSIDE it as ordinary, selectable
 * text. The full row still reads as the click target visually -- the
 * pseudo-element overlay covers the row's box, not just the title's own
 * inline extent.
 *
 * Earlier revision of this doc comment claimed the row links to
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
        <li className="browse-row ts-browse-row">
            <div className="browse-row-headline">
                {target
                    ? (
                        <Link className="browse-row-title" to={target}>
                            {equation}
                            {/* Folds the label into the accessible name
                                ("equation (+ label)") without duplicating
                                it as VISIBLE text -- the label is already
                                visible on `.browse-row-meta` below. */}
                            <span className="sr-only"> ({entryLabel})</span>
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
            <p className="browse-row-provenance">{levelOfTheory} · {software}</p>
            <ul className="browse-row-entries">
                <li className="browse-entry-chip">
                    <span className="value-pill browse-entry-kind-pill">{entryStatusText}</span>
                    <span className="value-pill value-pill--muted browse-entry-review">
                        {entryReviewStatusText}
                    </span>
                </li>
            </ul>
            <p className="browse-row-footer">
                <span>{evidenceText}</span>
                <code className="browse-row-ref">{entryRef}</code>
            </p>
        </li>
    )
}
