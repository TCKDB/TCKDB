import { useState, type ReactNode } from "react"
import "../path-section.css"
import type { CalculationRecord } from "../api/calculationApi"
import { loadIrcPoints, loadScanPoints, type IrcPoints, type ScanPoints } from "../api/calculationPathsApi"
import { coordinateText, initialPointIndex, ircProfile, scanCoordinateWords, scanProfile, whereText, type PathProfile } from "../domain/pathProfile"
import { convergedWords } from "../domain/pathSearchFacts"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { useLoaded, type LoadedState } from "../hooks/useLoaded"
import { LevelLabel } from "./LevelLabel"
import { SectionHeading } from "./PageSections"
import { PathFigure } from "./PathFigure"
import { PathPointStepper } from "./PathPointStepper"

function LevelProgram({ calculation }: { calculation: CalculationRecord }) {
    const lot = calculation.level_of_theory
    const programs = [softwareLabel(calculation.software_release), toolReleaseLabel(calculation.workflow_tool_release)].filter(Boolean).join(" · ")
    return (
        <div>
            <dt>Level · program</dt>
            <dd>{lot ? <LevelLabel levelOfTheory={lot} /> : "level not recorded"}{programs && <> · {programs}</>}</dd>
        </div>
    )
}

function noEnergies(profile: PathProfile, what: string): string {
    return profile.energyMode === "none" ? ` No energies were deposited for this ${what}, so the points sit on the zero line and the energy axis is empty.` : ""
}

/** The figure and the stepper for one path-shaped calculation, sharing one selected point. */
function PathBody({ profile, label, caption, noun }: { profile: PathProfile; label: string; caption: string; noun: string }) {
    const [picked, setPicked] = useState<number | null>(null)
    const selected = picked ?? initialPointIndex(profile)
    return (
        <>
            <SectionHeading id="path-energy-heading" subsection>{label}</SectionHeading>
            <PathFigure profile={profile} label={label} caption={caption} selectedIndex={selected} onSelect={setPicked} />
            {selected !== null && (
                <>
                    <SectionHeading id="path-steps-heading" subsection>{profile.points.length > 1 ? "Step through the points" : "The stored point"}</SectionHeading>
                    <PathPointStepper profile={profile} selectedIndex={selected} onSelect={setPicked} label={noun} sliderLabel={`Point along the ${noun}`} />
                </>
            )}
        </>
    )
}

function Section<T extends { points: unknown[] }>({ heading, state, noun, children }: {
    heading: string
    state: LoadedState<T>
    noun: string
    children: (data: T) => ReactNode
}) {
    return (
        <section className="ledger-section path-section" aria-labelledby="path-heading">
            <SectionHeading id="path-heading" guide={{ to: "reaction-path", question: "What is a reaction path?" }}>{heading}</SectionHeading>
            {state.status === "failed" && <p className="note" role="alert">The points of this {noun} could not be loaded.</p>}
            {state.status === "rate-limited" && <p className="note" role="alert">The archive is busy, so the points of this {noun} were not loaded. Reload the page to try again.</p>}
            {(state.status === "loading" || state.status === "idle") && <p className="note" role="status">Loading the points of this {noun}.</p>}
            {state.status === "ready" && state.data.points.length === 0 && <p className="empty-projection">No point of this {noun} was stored.</p>}
            {state.status === "ready" && state.data.points.length > 0 && children(state.data)}
        </section>
    )
}

const LEG_WORDS: Readonly<Record<string, string>> = { both: "both legs", forward: "forward leg", reverse: "reverse leg" }

function IrcBody({ data, calculation }: { data: IrcPoints; calculation: CalculationRecord }) {
    const profile = ircProfile(data)
    const { summary } = data
    const saddle = profile.points.find((point) => point.emphasis === "saddle")
    const direction = summary.direction ? (Object.hasOwn(LEG_WORDS, summary.direction) ? LEG_WORDS[summary.direction] : summary.direction) : "not recorded"
    const legs = [
        summary.forward_point_count > 0 ? `${summary.forward_point_count} forward` : null,
        summary.reverse_point_count > 0 ? `${summary.reverse_point_count} reverse` : null,
    ].filter(Boolean).join(" · ")
    const caption = `${profile.points.length} points placed by the reaction coordinate${profile.xUnit ? (profile.xUnitSuffix ? `, in ${profile.xUnit}.` : `, given as a ${profile.xUnit}.`) : " as the program reported it; no unit is stored for it."}`
        + `${saddle ? " The filled point is the saddle point; the ends are labelled by leg." : ""}${noEnergies(profile, "IRC")}`
    return (
        <>
            <dl className="kv-list path-facts">
                <div><dt>Direction</dt><dd>{direction}</dd></div>
                <div><dt>Points</dt><dd>{profile.points.length} stored{legs && ` · ${legs}`}</dd></div>
                <div><dt>Saddle point</dt><dd className="t-preserve-case">{saddle ? `point ${saddle.index} · ${whereText(profile, saddle)}` : "not marked"}</dd></div>
                <LevelProgram calculation={calculation} />
            </dl>
            <PathBody profile={profile} label="Energy along the IRC" caption={caption} noun="IRC" />
        </>
    )
}

function ScanBody({ data, calculation }: { data: ScanPoints; calculation: CalculationRecord }) {
    const profile = scanProfile(data)
    const { summary, coordinates } = data
    const coordinateFacts = coordinates.map((coordinate) => {
        const range = coordinate.start_value != null && coordinate.end_value != null
            ? `${coordinateText(profile, coordinate.start_value)} to ${coordinateText(profile, coordinate.end_value)}${coordinate.value_unit ? ` ${coordinate.value_unit === "degree" ? "degrees" : coordinate.value_unit === "angstrom" ? "Å" : coordinate.value_unit}` : ""}`
            : null
        const steps = coordinate.step_count != null ? `${coordinate.step_count} steps` : null
        return [scanCoordinateWords(coordinate), range, steps].filter(Boolean).join(" · ")
    })
    const caption = (profile.hasCoordinate
        ? `${profile.points.length} points at their stored ${profile.xColumn.toLowerCase()} values.`
        : `${profile.points.length} points placed by point number.`) + noEnergies(profile, "scan")
    return (
        <>
            <dl className="kv-list path-facts">
                <div><dt>{coordinates.length > 1 ? "Coordinates" : "Coordinate"}</dt><dd className="t-preserve-case">{coordinateFacts.length > 0 ? coordinateFacts.join("; ") : "not recorded"}</dd></div>
                <div><dt>Relaxed</dt><dd>{convergedWords(summary.is_relaxed)}</dd></div>
                <div><dt>Points</dt><dd>{profile.points.length} stored</dd></div>
                <LevelProgram calculation={calculation} />
            </dl>
            <PathBody profile={profile} label="Energy along the scan" caption={caption} noun="scan" />
        </>
    )
}

/** "The path" for an IRC: its legs, the saddle point, energy along the reaction coordinate, and every point in order. */
export function IrcPath({ calculation }: { calculation: CalculationRecord }) {
    const state = useLoaded(loadIrcPoints, calculation.calculation.calculation_ref)
    return <Section heading="The path" state={state} noun="IRC">{(data) => <IrcBody data={data} calculation={calculation} />}</Section>
}

/** "The scan" for a scan: the scanned coordinate, energy along it, and every point in order. */
export function ScanPath({ calculation }: { calculation: CalculationRecord }) {
    const state = useLoaded(loadScanPoints, calculation.calculation.calculation_ref)
    return <Section heading="The scan" state={state} noun="scan">{(data) => <ScanBody data={data} calculation={calculation} />}</Section>
}
