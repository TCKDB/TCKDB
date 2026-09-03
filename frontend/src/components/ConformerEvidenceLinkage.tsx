import type { ReactNode } from "react"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { CalculationTypeCount } from "../domain/conformerEvidence"
import { calculationTypeCounts, conformerLabel, describeConformerEvidence, geometryConvergence } from "../domain/conformerEvidence"
import { SectionHeading } from "./PageSections"

// The published `calculation_count` and the per-stage breakdown come from
// TWO different sources on the wire (`evidence_summary.calculation_count`
// vs. the conformer's own `calculations` list) -- a stale or not-yet-loaded
// `calculations` array must never be read as "zero calculation rows exist"
// when the archive's own published count says otherwise. Three states,
// never conflated: genuinely zero rows, a positive published count with no
// breakdown available, and a positive published count WITH a breakdown.
function calculationRowDetail(
    evidence: ConformerProjection["evidence_summary"],
    typeCounts: CalculationTypeCount[],
): string {
    if (evidence.calculation_count === 0) return "no calculation rows recorded"
    if (typeCounts.length === 0) return "breakdown not loaded"
    return `${typeCounts.map(({ type, count }) => `${count} ${type}`).join(" · ")}, in `
        + `${evidence.optimization_chain_count} optimisation chain${evidence.optimization_chain_count === 1 ? "" : "s"}`
        + " (a staged coarse-then-fine reoptimisation counts as one chain)"
}

// Pulled out of the FULL prose `describeConformerEvidence` builds, rather
// than reimplemented here: that function (`domain/conformerEvidence.ts`) is
// the single source of truth for how staged optimisation is described --
// it encodes real chemistry judgement (see its own module comment on why a
// three-row/one-chain observation can't honestly be called "three
// stages"), and this component has no business owning a second copy of
// that judgement.
//
// The full prose has a FIXED shape: exactly one lead sentence ("This
// conformer was sighted N times."), then zero or more staging sentences,
// then exactly two coverage sentences (frequency, then single-point) --
// both of which just restate numbers the conformer card already shows.
// Splitting on sentence boundaries and dropping the first sentence and the
// last two, however many are left over, isolates the staging content --
// the one thing in the paragraph that ISN'T already on the card -- without
// this component re-deriving it from the raw evidence fields itself.
function stagingSentence(conformer: ConformerProjection): string | null {
    const sentences = describeConformerEvidence(conformer).split(/(?<=\.)\s+/)
    const middle = sentences.slice(1, -2)
    return middle.length > 0 ? middle.join(" ") : null
}

/**
 * Answers the owner's own confusion directly: "they see 2 distinct Geoms
 * but then 4 conformers... 14 calculation rows but only 12 I see (opt 4/4,
 * freq 4/4, sp 3/4)." Every one of those numbers is correct; they are
 * three different UNITS printed as if they were comparable. But naming the
 * units was still the wrong answer to a different question he actually
 * asked -- "is the 7 opt also including the pre-opt?" -- which is about
 * what happened to THIS molecule, not what each figure counts. The
 * conformer card above already states the counts (how many observations,
 * how many calculation rows, per-stage coverage) -- repeating them here in
 * prose would just be a third form of the same two numbers, so the only
 * thing shown by default is `stagingSentence` below: the ONE fact the card
 * cannot express (whether a sighting's optimisation ran in more than one
 * pass). The figures that back the counts up -- the unit-by-unit flow and
 * the per-stage coverage -- sit inside a collapsed `<details>` for the
 * reader who wants the mechanics. No number here is recomputed or merged
 * -- each is read straight off the same `evidence_summary` fields the
 * conformer card already renders.
 */
export function ConformerEvidenceLinkage({ conformer }: { conformer: ConformerProjection }) {
    const total = conformer.observations_summary.total
    const evidence = conformer.evidence_summary
    const coverage = evidence.evidence_coverage
    const typeCounts = calculationTypeCounts(conformer)
    const convergence = geometryConvergence(conformer)
    const label = conformerLabel(conformer)
    const staging = stagingSentence(conformer)

    return (
        <section className="evidence-linkage" aria-labelledby="evidence-linkage-heading">
            {/* No separate "Evidence" eyebrow here -- the conformer picker
                right above already carries one, and the tab strip below
                carries its own "Evidence for this conformer" eyebrow too.
                Three "evidence" labels in a row said nothing a heading
                alone doesn't; the heading is enough. */}
            <SectionHeading id="evidence-linkage-heading" label={`Evidence for ${label}`}>Evidence for {label}</SectionHeading>
            {staging && <p className="evidence-linkage-story">{staging}</p>}
            <details className="evidence-linkage-detail">
                {/* The count in the summary is the "References (4)" lesson applied
                    here: a bare "How this evidence connects" gave a reader no reason
                    to open it. Both figures are already published elsewhere on this
                    card/panel, so this repeats nothing new -- it previews what's
                    behind the click. */}
                <summary>
                    How this evidence connects ({evidence.calculation_count} calculation row{evidence.calculation_count === 1 ? "" : "s"}
                    {", "}{evidence.geometry_count} distinct geometr{evidence.geometry_count === 1 ? "y" : "ies"})
                </summary>
                <p className="evidence-linkage-note">
                    Three different units, not three counts of the same thing — the card above abbreviates the
                    first two as "obs" and "calc": an observation is a deposited sighting of this basin; a
                    calculation row is one piece of evidence attached to an observation; a stored geometry is a
                    coordinate set several calculation rows can share.
                </p>
                <div className="linkage-flow">
                    <LinkageStep
                        kind="observations"
                        count={total}
                        unit={total === 1 ? "deposited observation" : "deposited observations"}
                        detail="each a separate sighting of this basin"
                    />
                    <LinkageConnector />
                    <LinkageStep
                        kind="calculations"
                        count={evidence.calculation_count}
                        unit={evidence.calculation_count === 1 ? "calculation row" : "calculation rows"}
                        detail={calculationRowDetail(evidence, typeCounts)}
                    />
                    <LinkageConnector />
                    <LinkageStep
                        kind="geometries"
                        count={evidence.geometry_count}
                        unit={evidence.geometry_count === 1 ? "distinct stored geometry" : "distinct stored geometries"}
                    >
                        {/* Plain text, not links -- the Geometry tab below already links each
                            of these refs (`ConformerGeometryTab`); repeating them as links
                            here would give two same-named links their own separate targets
                            for assistive tech to disambiguate, for no navigational benefit
                            this summary needs to provide. */}
                        {convergence.length > 0 ? (
                            <ul className="linkage-geometry-list">
                                {convergence.map((entry) => (
                                    <li key={entry.geometryRef}>
                                        <code>{entry.geometryRef}</code>
                                        {` — ${entry.calculationCount} calculation output${entry.calculationCount === 1 ? "" : "s"}`}
                                    </li>
                                ))}
                            </ul>
                        ) : evidence.geometry_count > 0 && (
                            <span className="linkage-step-detail">breakdown not loaded</span>
                        )}
                    </LinkageStep>
                </div>
                <p className="linkage-coverage">
                    <strong>Stage coverage</strong> — of the {total} observation{total === 1 ? "" : "s"}: opt{" "}
                    {coverage.opt}/{total} · freq {coverage.freq}/{total} · sp {coverage.sp}/{total}. This counts which
                    observations have at least one calculation of that stage, not the number of calculation rows.
                </p>
            </details>
        </section>
    )
}

// `data-linkage-step` is a stable test hook: it lets a test bind a count
// and its unit to the SAME step (`within(getStep("observations"))...`)
// instead of two independent `getByText` calls that would still pass if
// the observation and geometry counts were swapped -- the exact gap the
// review round caught.
function LinkageStep({ kind, count, unit, detail, children }: {
    kind: "observations" | "calculations" | "geometries"
    count: number
    unit: string
    detail?: string
    children?: ReactNode
}) {
    return (
        <div className="linkage-step" data-linkage-step={kind}>
            <span className="linkage-step-count">{count}</span>
            <span className="linkage-step-unit">{unit}</span>
            {detail && <span className="linkage-step-detail">{detail}</span>}
            {children}
        </div>
    )
}

function LinkageConnector() {
    return <span className="linkage-connector" aria-hidden="true">→</span>
}
