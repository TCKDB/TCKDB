import "../path-section.css"
import { loadPathSearchPoints } from "../api/calculationPathsApi"
import { relativeKjText } from "../domain/energyProfile"
import { baseLabel, type LevelFields } from "../domain/levelLabel"
import { coordinateScale, energyScale, pathSearchProfile, type PathProfile } from "../domain/pathProfile"
import { guessWords, methodWords, partialBandNote, pointsWords } from "../domain/pathSearchFacts"
import { useLoaded } from "../hooks/useLoaded"
import { RecordLink } from "./RecordLink"

const W = 320
const H = 44
const PAD = 8

/** The path as a 44 px line: energies when there are any, otherwise the points on one rule at their coordinates. */
export function PathSparkline({ profile }: { profile: PathProfile }) {
    const xScale = coordinateScale(profile)
    const yScale = energyScale(profile)
    const sx = (x: number) => PAD + ((x - xScale.min) / (xScale.max - xScale.min || 1)) * (W - 2 * PAD)
    const sy = (kj: number | null) => (profile.energyMode === "none" || kj === null ? H / 2 : PAD + ((yScale.max - kj) / (yScale.max - yScale.min || 1)) * (H - 2 * PAD))
    const drawn = profile.points.filter((point) => profile.energyMode === "none" || point.kj !== null)
    const line = profile.energyMode === "none" ? null : drawn.map((point, index) => `${index === 0 ? "M" : "L"}${sx(point.x).toFixed(1)},${sy(point.kj).toFixed(1)}`).join(" ")
    const peak = profile.energyMode === "none" ? null : Math.max(...drawn.map((point) => point.kj ?? -Infinity))
    const summary = profile.energyMode === "none"
        ? `${profile.points.length} points along the path; no energies were deposited.`
        : `${profile.points.length} points along the path; highest ${relativeKjText(peak ?? 0)} kJ/mol.`
    return (
        <svg className="path-sparkline" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}>
            {profile.energyMode === "none" && <line className="path-sparkline-rule" x1={PAD} x2={W - PAD} y1={H / 2} y2={H / 2} />}
            {line && <path className="path-sparkline-line" d={line} />}
            {drawn.map((point) => {
                const marked = point.emphasis === "guess" || point.emphasis === "guess-climbing" || point.emphasis === "saddle"
                return (
                    <circle
                        key={point.index}
                        className={marked ? "path-sparkline-point path-sparkline-point--marked" : "path-sparkline-point"}
                        cx={sx(point.x)}
                        cy={sy(point.kj)}
                        r={marked ? 4.5 : 3}
                    />
                )
            })}
        </svg>
    )
}

/**
 * On a transition-state entry: how its saddle point was found, from one path-search calculation's own points, with a
 * link to the full path. Kept apart from the energy hill, which is at its own level from other calculations.
 */
export function PathSearchCard({ calculationRef, levelOfTheory }: { calculationRef: string; levelOfTheory: LevelFields | null }) {
    const state = useLoaded(loadPathSearchPoints, calculationRef)
    const ready = state.status === "ready" && state.data.points.length > 0 ? state.data : null
    const profile = ready ? pathSearchProfile(ready) : null
    const headingId = `path-card-${calculationRef}`
    return (
        <section className="card path-card" aria-labelledby={headingId}>
            <h3 className="t-heading-2" id={headingId}>How the saddle point was found</h3>
            {state.status === "failed" && <p className="note" role="alert">The points of this path search could not be loaded.</p>}
            {state.status === "rate-limited" && <p className="note" role="alert">The archive is busy, so the points of this path search were not loaded.</p>}
            {(state.status === "loading" || state.status === "idle") && <p className="note" role="status">Loading the path search.</p>}
            {state.status === "ready" && !profile && <p className="empty-projection">No point of this path search was stored.</p>}
            {ready && profile && (
                <>
                    <dl className="kv-list path-card-facts">
                        <div><dt>Method</dt><dd>{methodWords(ready.summary.method)}</dd></div>
                        <div><dt>Points</dt><dd>{pointsWords(ready.summary)}</dd></div>
                        <div><dt>Picked guess</dt><dd className="t-preserve-case">{guessWords(ready.summary, profile)}</dd></div>
                        <div><dt>Level</dt><dd className="t-preserve-case">{levelOfTheory ? baseLabel(levelOfTheory) : "not recorded"}</dd></div>
                    </dl>
                    <PathSparkline profile={profile} />
                    <p className="note">
                        {partialBandNote(ready.summary) ?? ""}{partialBandNote(ready.summary) ? " " : ""}
                        {profile.energyMode === "none"
                            ? profile.hasCoordinate ? "No energies were deposited for this path; the points sit on one line at their stored distances. " : "No energies or distances were deposited for this path. "
                            : ""}
                        Not part of the energy hill above. <RecordLink kind="calculation" to={`/calculations/${calculationRef}`} hash="path-heading" label="The whole path">See the whole path</RecordLink>.
                    </p>
                </>
            )}
        </section>
    )
}
