import { useEffect, useId, useRef, useState, type ReactNode } from "react"
import "../molecule-page.css"
import "../path-steps.css"
import { loadGeometryAtoms, type GeometryRecord } from "../api/geometryApi"
import { hartreeText, relativeKjText } from "../domain/energyProfile"
import { coordinateText, markerWord, NOT_DEPOSITED, pointSentence, type PathPoint, type PathProfile } from "../domain/pathProfile"
import { geometryWords } from "../domain/recordWords"
import { useLoaded } from "../hooks/useLoaded"
import { RecordLink } from "./RecordLink"
import { ShapeCardHead } from "./ShapePicture"
import { ViewerStage } from "./ViewerStage"

function SelectedFacts({ profile, point, extra }: { profile: PathProfile; point: PathPoint; extra?: ReactNode }) {
    const position = profile.points.indexOf(point) + 1
    const marker = markerWord(point)
    return (
        <dl className="kv-list path-steps-facts">
            <div>
                <dt>Point</dt>
                <dd>
                    {profile.points.length > 1 ? `${point.index} · ${position} of ${profile.points.length} in path order` : `${point.index}, the only stored point`}
                    {point.leg && <> · {point.leg}</>}
                    {marker && <> <span className="value-pill">{marker === "guess" ? "picked guess" : marker}</span></>}
                </dd>
            </div>
            <div><dt>{profile.xColumn}</dt><dd className="t-preserve-case">{coordinateText(profile, point.coordinate)}</dd></div>
            <div>
                <dt>Energy</dt>
                <dd className="t-preserve-case">
                    {point.kj === null ? NOT_DEPOSITED : `${relativeKjText(point.kj)} kJ/mol`}
                    {point.hartree !== null && <> · {hartreeText(point.hartree)} hartree</>}
                </dd>
            </div>
            {profile.hasGradients && <div><dt>RMS gradient</dt><dd>{point.rmsGradient === null ? NOT_DEPOSITED : point.rmsGradient}</dd></div>}
            <div>
                <dt>Geometry</dt>
                <dd>
                    {point.geometryRef
                        ? <RecordLink kind="geometry" to={`/geometries/${point.geometryRef}`} label={geometryWords(point.natoms)} context={`point ${point.index}`} />
                        : NOT_DEPOSITED}
                </dd>
            </div>
            {extra}
        </dl>
    )
}

/** The selected point's geometry in the shared viewer; the last one read stays up while the next loads. */
function PointViewer({ point, label }: { point: PathPoint; label: string }) {
    const state = useLoaded(loadGeometryAtoms, point.geometryRef)
    const [shown, setShown] = useState<{ ref: string; index: number; record: GeometryRecord } | null>(null)
    if (state.status === "ready" && point.geometryRef && shown?.ref !== point.geometryRef) setShown({ ref: point.geometryRef, index: point.index, record: state.data })
    const atoms = shown?.record.atoms ?? []
    const waiting = point.geometryRef !== null && shown?.ref !== point.geometryRef
    return (
        <figure className="viewer-figure path-steps-viewer" aria-label={`Geometry of point ${point.index}`}>
            {point.geometryRef === null && <p className="empty-projection">No geometry was stored for point {point.index}.</p>}
            {waiting && (state.status === "loading" || state.status === "idle") && <p className="note" role="status">Loading point {point.index}.</p>}
            {waiting && (state.status === "failed" || state.status === "rate-limited") && <p className="empty-projection">The geometry of point {point.index} could not be read.</p>}
            {atoms.length > 0 && <ViewerStage atoms={atoms} formula={`${label}, point ${shown?.index}`} xyzText={shown?.record.xyz_text ?? null} />}
        </figure>
    )
}

/**
 * Step through the stored points of a path: a strip of small pictures, a slider with previous and next, the selected
 * point in the shared 3D viewer, and that point's own values.
 */
export function PathPointStepper({ profile, selectedIndex, onSelect, label, extraFacts, sliderLabel = "Point along the path" }: {
    profile: PathProfile
    selectedIndex: number
    onSelect: (index: number) => void
    /** What the path is of, for the pictures' accessible names. */
    label: string
    extraFacts?: (point: PathPoint) => ReactNode
    sliderLabel?: string
}) {
    const sliderId = useId()
    const position = Math.max(0, profile.points.findIndex((point) => point.index === selectedIndex))
    const point = profile.points[position]
    const previous = profile.points[position - 1]
    const next = profile.points[position + 1]
    const stripRef = useRef<HTMLOListElement | null>(null)
    const cardRefs = useRef(new Map<number, HTMLLIElement | null>())
    // Keeps the selected card in the middle of the strip without scrolling the page.
    useEffect(() => {
        const strip = stripRef.current
        const card = cardRefs.current.get(selectedIndex)
        if (!strip || !card) return
        strip.scrollLeft = card.offsetLeft - (strip.clientWidth - card.clientWidth) / 2
    }, [selectedIndex])
    if (!point) return null
    const several = profile.points.length > 1
    return (
        <div className="path-steps">
            {several && <ol className="path-strip" ref={stripRef} aria-label="Points of the path">
                {profile.points.map((candidate) => {
                    const marker = markerWord(candidate)
                    return (
                        <li
                            key={candidate.index}
                            ref={(element) => { cardRefs.current.set(candidate.index, element) }}
                            className={marker ? "conformer-card path-strip-card path-strip-card--marked" : "conformer-card path-strip-card"}
                            data-selected={candidate.index === selectedIndex}
                        >
                            <ShapeCardHead
                                geometryRef={candidate.geometryRef}
                                formula={`${label}, point ${candidate.index}`}
                                auto
                                selected={candidate.index === selectedIndex}
                                label={marker ? `${candidate.index} · ${marker}` : String(candidate.index)}
                                counts={profile.hasCoordinate ? coordinateText(profile, candidate.coordinate) : candidate.leg ?? ""}
                                onSelect={() => onSelect(candidate.index)}
                            />
                        </li>
                    )
                })}
            </ol>}
            <div className="path-steps-body">
                <div className="path-steps-stage">
                    {several && <div className="path-stepper">
                        <button type="button" disabled={!previous} onClick={() => previous && onSelect(previous.index)}>
                            ‹ {previous ? `Point ${previous.index}` : "First point"}
                        </button>
                        <label className="path-stepper-slider" htmlFor={sliderId}>
                            <span className="path-stepper-label">{sliderLabel}</span>
                            <input
                                id={sliderId}
                                type="range"
                                min={0}
                                max={profile.points.length - 1}
                                step={1}
                                value={position}
                                aria-valuetext={pointSentence(profile, point)}
                                onChange={(event) => onSelect(profile.points[Number(event.target.value)].index)}
                            />
                        </label>
                        <button type="button" disabled={!next} onClick={() => next && onSelect(next.index)}>
                            {next ? `Point ${next.index}` : "Last point"} ›
                        </button>
                    </div>}
                    <PointViewer point={point} label={label} />
                </div>
                <SelectedFacts profile={profile} point={point} extra={extraFacts?.(point)} />
            </div>
        </div>
    )
}
