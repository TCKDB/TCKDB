import { Fragment } from "react"
import "../path-section.css"
import type { CalculationRecord } from "../api/calculationApi"
import { loadCalculationChildren, type CalculationChild } from "../api/calculationChildrenApi"
import { loadPathSearchPoints, type PathSearchSummary } from "../api/calculationPathsApi"
import { loadReactionEnergyProfile } from "../api/reactionEnergyProfileApi"
import { calculationTitleWords } from "../domain/calculationLineage"
import { baseLabel } from "../domain/levelLabel"
import { OPTIMISATION_STAGE_WORDS } from "../domain/optimisationStage"
import { pathSearchCaption, pathSearchProfile, type PathProfile } from "../domain/pathProfile"
import {
    childFacts,
    childOrder,
    climbingWords,
    convergedWords,
    edgeWords,
    endsWords,
    guessIndex,
    guessWords,
    methodShort,
    methodWords,
    partialBandNote,
    pointsWords,
} from "../domain/pathSearchFacts"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { useLoaded } from "../hooks/useLoaded"
import { LevelLabel } from "./LevelLabel"
import { SectionHeading } from "./PageSections"
import { PathFigure } from "./PathFigure"
import { RecordLink } from "./RecordLink"

type Lot = NonNullable<CalculationRecord["level_of_theory"]>

function Facts({ summary, profile, calculation }: { summary: PathSearchSummary; profile: PathProfile; calculation: CalculationRecord }) {
    const lot = calculation.level_of_theory
    const programs = [softwareLabel(calculation.software_release), toolReleaseLabel(calculation.workflow_tool_release)].filter(Boolean).join(" · ")
    return (
        <dl className="kv-list path-facts">
            <div><dt>Method</dt><dd>{methodWords(summary.method)}</dd></div>
            <div><dt>Ends</dt><dd>{endsWords(summary)}</dd></div>
            <div><dt>Points</dt><dd>{pointsWords(summary)}</dd></div>
            <div><dt>Picked guess</dt><dd className="t-preserve-case">{guessWords(summary, profile)}</dd></div>
            <div><dt>Climbing image</dt><dd>{climbingWords(summary, profile)}</dd></div>
            <div><dt>Converged</dt><dd>{convergedWords(summary.converged)}</dd></div>
            <div>
                <dt>Level · program</dt>
                <dd>
                    {lot ? <LevelLabel levelOfTheory={lot} /> : "level not recorded"}
                    {programs && <> · {programs}</>}
                </dd>
            </div>
        </dl>
    )
}

function childTitle(child: CalculationChild, role: string | null): string {
    if (child.calculation.type === "opt" && role === "optimized_from") return OPTIMISATION_STAGE_WORDS.fine.label
    return calculationTitleWords(child.calculation.type)
}

function Node({ child, role }: { child: CalculationChild; role: string | null }) {
    const facts = childFacts(child)
    const level = child.level_of_theory ? baseLabel(child.level_of_theory) : null
    return (
        <div className="path-lineage-node">
            <RecordLink kind="calculation" to={`/calculations/${child.calculation.calculation_ref}`} label={childTitle(child, role)} />
            <span className="path-lineage-facts t-preserve-case">{[level, facts].filter(Boolean).join(" · ")}</span>
        </div>
    )
}

function Grandchildren({ parentRef }: { parentRef: string }) {
    const state = useLoaded(loadCalculationChildren, parentRef)
    if (state.status !== "ready" || state.data.length === 0) return null
    return (
        <>
            <li className="path-lineage-edge" aria-hidden="true"><span className="path-lineage-arrow">→</span></li>
            <li className="path-lineage-group">
                <span className="path-lineage-edge-words">then, on that geometry</span>
                <ul>
                    {childOrder(state.data).map((child) => <li key={child.calculation.calculation_ref}><Node child={child} role={null} /></li>)}
                </ul>
            </li>
        </>
    )
}

function sameLevel(a: Lot, b: Lot): boolean {
    if (a.level_of_theory_ref && b.level_of_theory_ref) return a.level_of_theory_ref === b.level_of_theory_ref
    return baseLabel(a) === baseLabel(b)
}

/** Where the transition-state pages draw their energy hill, and that this path is not on it. */
function HillNote({ reactionEntryRef, transitionStateEntryRef, lot }: { reactionEntryRef: string | null; transitionStateEntryRef: string; lot: Lot | null }) {
    const state = useLoaded(loadReactionEnergyProfile, reactionEntryRef)
    const fallback = "This path's points are not drawn on the energy hill of those pages."
    if (state.status !== "ready") return <>{fallback}</>
    const hills = state.data.profiles.filter((profile) => profile.transition_state_entry_ref === transitionStateEntryRef)
    if (hills.length === 0) return <>Those pages draw no energy hill for this saddle point, and this path&apos;s points are drawn only here.</>
    const levels = [...new Set(hills.map((profile) => baseLabel(profile.level_of_theory)))]
    const here = lot ? baseLabel(lot) : "a level that was not recorded"
    if (lot && hills.some((profile) => sameLevel(profile.level_of_theory, lot))) {
        return <>The energy hill on those pages is at {levels.join(", ")}, from other calculations; this path&apos;s points are not on it.</>
    }
    return <>The energy hill on those pages is at {levels.join(", ")}; this path was searched at {here} and its points are not on that hill.</>
}

function Lineage({ calculation, summary, profile }: { calculation: CalculationRecord; summary: PathSearchSummary; profile: PathProfile }) {
    const ref = calculation.calculation.calculation_ref
    const edges = (calculation.dependencies ?? []).filter((edge) => edge.direction === "parent")
    const state = useLoaded(loadCalculationChildren, edges.length > 0 ? ref : null)
    const children = state.status === "ready" ? childOrder(state.data) : []
    const roleOf = (child: CalculationChild) => edges.find((edge) => edge.child_calculation_ref === child.calculation.calculation_ref)?.role ?? null
    const lot = calculation.level_of_theory ?? null
    const guess = guessIndex(summary, profile)
    const ts = calculation.owner.transition_state_entry ?? null
    const firstOpt = children.find((child) => child.calculation.type === "opt") ?? null
    return (
        <>
            <SectionHeading id="path-lineage-heading" subsection>Where this path led</SectionHeading>
            {edges.length === 0 && <p className="empty-projection">No calculation is recorded as using this path search.</p>}
            {edges.length > 0 && state.status === "failed" && <p className="note" role="alert">The calculations that used this path could not be loaded.</p>}
            {edges.length > 0 && state.status === "rate-limited" && <p className="note" role="alert">The archive is busy, so the calculations that used this path were not loaded.</p>}
            {children.length > 0 && (
                <ol className="path-lineage link-list" aria-label="Calculations that followed from this path search">
                    <li>
                        <div className="path-lineage-node path-lineage-node--self">
                            <strong>This path search</strong>
                            <span className="path-lineage-facts t-preserve-case">
                                {[methodShort(summary.method), lot ? baseLabel(lot) : null, `${summary.stored_point_count} ${summary.stored_point_count === 1 ? "point" : "points"}`].filter(Boolean).join(" · ")}
                            </span>
                        </div>
                    </li>
                    {children.map((child) => (
                        <Fragment key={child.calculation.calculation_ref}>
                            <li className="path-lineage-edge"><span className="path-lineage-arrow" aria-hidden="true">→</span><span className="path-lineage-edge-words">{edgeWords(roleOf(child), guess)}</span></li>
                            <li><Node child={child} role={roleOf(child)} /></li>
                        </Fragment>
                    ))}
                    {firstOpt && <Grandchildren parentRef={firstOpt.calculation.calculation_ref} />}
                </ol>
            )}
            {ts && (
                <p className="note path-lineage-back">
                    Back to the <RecordLink kind="transition-state" to={`/transition-state-entries/${ts.transition_state_entry_ref}`} label="Transition state entry">transition state entry</RecordLink>
                    {ts.reaction_entry_ref && <> and the <RecordLink kind="reaction-entry" to={`/reaction-entries/${ts.reaction_entry_ref}`} label="Reaction version">reaction version</RecordLink></>}
                    {" "}it belongs to.{" "}
                    <HillNote reactionEntryRef={ts.reaction_entry_ref ?? null} transitionStateEntryRef={ts.transition_state_entry_ref} lot={lot} />
                </p>
            )}
        </>
    )
}

/**
 * "The path" for a path-search calculation: what the search was, the energy along its stored points, and the
 * calculations that took its picked guess forward. Every value is this calculation's own or the named child's own.
 */
export function PathSearchPath({ calculation }: { calculation: CalculationRecord }) {
    const state = useLoaded(loadPathSearchPoints, calculation.calculation.calculation_ref)
    const ready = state.status === "ready" ? state.data : null
    const profile = ready && ready.points.length > 0 ? pathSearchProfile(ready) : null
    const band = ready ? partialBandNote(ready.summary) : null
    return (
        <section className="ledger-section path-section" aria-labelledby="path-heading">
            <SectionHeading id="path-heading" guide={{ to: "reaction-path", question: "What is a reaction path?" }}>The path</SectionHeading>
            {state.status === "failed" && <p className="note" role="alert">The points of this path could not be loaded.</p>}
            {state.status === "rate-limited" && <p className="note" role="alert">The archive is busy, so the points of this path were not loaded. Reload the page to try again.</p>}
            {(state.status === "loading" || state.status === "idle") && <p className="note" role="status">Loading the points of this path.</p>}
            {ready && !profile && <p className="empty-projection">No point of this path was stored.</p>}
            {ready && profile && (
                <>
                    <Facts summary={ready.summary} profile={profile} calculation={calculation} />
                    <SectionHeading id="path-energy-heading" subsection>Energy along the path</SectionHeading>
                    <PathFigure
                        profile={profile}
                        label="Energy along the path"
                        caption={band ? `${pathSearchCaption(profile)} ${band}` : pathSearchCaption(profile)}
                    />
                    <Lineage calculation={calculation} summary={ready.summary} profile={profile} />
                </>
            )}
        </section>
    )
}
