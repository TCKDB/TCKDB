import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import "../path-figure.css"
import { hartreeText, relativeKjText } from "../domain/energyProfile"
import {
    coordinateScale,
    coordinateText,
    directLabel,
    energyScale,
    energyText,
    NOT_DEPOSITED,
    pointSentence,
    type PathPoint,
    type PathProfile,
    whereText,
} from "../domain/pathProfile"
import { geometryWords } from "../domain/recordWords"
import { useOverflows } from "../hooks/useOverflows"
import { RecordLink } from "./RecordLink"

const W = 720
const H_ENERGY = 260
const H_EMPTY = 200
const LEFT = 64
const RIGHT = 28
const TOP = 44
const BOTTOM = 58
/** Space between the y axis (and the right edge) and the first (last) point, so markers never sit on tick labels. */
const INSET = 18
const PLOT_W = W - LEFT - RIGHT
const MARKER_R = 5
const LABEL_CHAR_W = 7.6

type Placed = { point: PathPoint; sx: number; sy: number }

function linePaths(placed: Placed[]): string[] {
    const paths: string[] = []
    let current: string[] = []
    for (const { point, sx, sy } of placed) {
        if (point.kj === null) {
            if (current.length > 1) paths.push(current.join(" "))
            current = []
            continue
        }
        current.push(`${current.length === 0 ? "M" : "L"}${sx.toFixed(1)},${sy.toFixed(1)}`)
    }
    if (current.length > 1) paths.push(current.join(" "))
    return paths
}

/** Direct labels above their markers, moved below when they would overlap a label already placed. */
function placeLabels(profile: PathProfile, placed: Placed[]) {
    const boxes: { x0: number; x1: number; y: number }[] = []
    return placed.filter(({ point }) => point.labels.length > 0).map(({ point, sx, sy }) => {
        const text = directLabel(profile, point)
        const width = text.length * LABEL_CHAR_W
        const anchor: "start" | "middle" | "end" = sx - width / 2 < LEFT ? "start" : sx + width / 2 > W - 4 ? "end" : "middle"
        const x0 = anchor === "start" ? sx - 6 : anchor === "end" ? sx - width + 6 : sx - width / 2
        const box = { x0, x1: x0 + width }
        const above = sy - 14
        const below = sy + 26
        const clashes = (y: number) => boxes.some((other) => other.y === y && box.x0 < other.x1 + 8 && other.x0 < box.x1 + 8)
        const y = clashes(above) || above < TOP - 6 ? below : above
        boxes.push({ ...box, y })
        return { key: point.index, text, x: anchor === "start" ? sx - 6 : anchor === "end" ? sx + 6 : sx, y, anchor, strong: point.emphasis !== null }
    })
}

function Marker({ point, sx, sy }: Placed) {
    const emphasis = point.emphasis
    if (emphasis === "climbing" || emphasis === "guess-climbing") {
        const r = MARKER_R + 2
        return (
            <path
                className={emphasis === "guess-climbing" ? "path-figure-marker path-figure-marker--climbing path-figure-marker--filled" : "path-figure-marker path-figure-marker--climbing"}
                d={`M${sx},${sy - r} L${sx + r},${sy} L${sx},${sy + r} L${sx - r},${sy} Z`}
                data-marker={emphasis}
            />
        )
    }
    const filled = emphasis === "guess" || emphasis === "saddle"
    return (
        <circle
            className={filled ? "path-figure-marker path-figure-marker--filled" : "path-figure-marker"}
            cx={sx}
            cy={sy}
            r={filled ? MARKER_R + 1 : MARKER_R}
            data-marker={emphasis ?? "point"}
        />
    )
}

function Tooltip({ profile, active, height }: { profile: PathProfile; active: Placed; height: number }) {
    const left = (active.sx / W) * 100
    const top = (active.sy / height) * 100
    const sideClass = active.sx > W * 0.66 ? "path-figure-tooltip path-figure-tooltip--left" : "path-figure-tooltip"
    const { point } = active
    return (
        <div className={sideClass} style={{ left: `${left}%`, top: `${top}%` }} aria-hidden="true">
            <strong>Point {point.index}{point.leg ? ` · ${point.leg}` : ""}</strong>
            {point.labels.length > 0 && <span>{point.labels.join(", ")}</span>}
            <span className="t-preserve-case">
                {whereText(profile, point)}
            </span>
            <span className="t-preserve-case">{energyText(point.kj)}</span>
        </div>
    )
}

function Picture({ profile, captionId, label, selectedIndex, onSelect }: {
    profile: PathProfile
    captionId: string
    label: string
    selectedIndex?: number | null
    onSelect?: (index: number) => void
}) {
    const xScale = coordinateScale(profile)
    const yScale = energyScale(profile)
    const H = profile.energyMode === "none" ? H_EMPTY : H_ENERGY
    const PLOT_H = H - TOP - BOTTOM
    const sx = (x: number) => LEFT + INSET + ((x - xScale.min) / (xScale.max - xScale.min || 1)) * (PLOT_W - 2 * INSET)
    const sy = (kj: number) => TOP + ((yScale.max - kj) / (yScale.max - yScale.min || 1)) * PLOT_H
    const zeroY = sy(0)
    const placed: Placed[] = profile.points.map((point) => ({ point, sx: sx(point.x), sy: point.kj === null ? zeroY : sy(point.kj) }))
    // Undrawn points (no energy while others have one) are kept out of the picture and listed in the table.
    const shown = profile.energyMode === "none" ? placed : placed.filter(({ point }) => point.kj !== null)
    const [active, setActive] = useState<number | null>(null)
    const [focus, setFocus] = useState<number | null>(null)
    const markerRefs = useRef(new Map<number, SVGGElement | null>())
    const rovingIndex = focus ?? (selectedIndex != null && shown.some(({ point }) => point.index === selectedIndex) ? selectedIndex : shown[0]?.point.index)
    const activePlaced = shown.find(({ point }) => point.index === active) ?? null

    const nearest = (event: { clientX: number; currentTarget: SVGRectElement }) => {
        const svg = event.currentTarget.ownerSVGElement
        if (!svg || shown.length === 0) return null
        const box = svg.getBoundingClientRect()
        const x = ((event.clientX - box.left) / (box.width || 1)) * W
        return shown.reduce((best, candidate) => (Math.abs(candidate.sx - x) < Math.abs(best.sx - x) ? candidate : best)).point.index
    }

    const move = (event: KeyboardEvent<SVGGElement>, position: number) => {
        const last = shown.length - 1
        const next = event.key === "ArrowRight" || event.key === "ArrowDown" ? Math.min(position + 1, last)
            : event.key === "ArrowLeft" || event.key === "ArrowUp" ? Math.max(position - 1, 0)
                : event.key === "Home" ? 0
                    : event.key === "End" ? last
                        : null
        if (next !== null) {
            event.preventDefault()
            markerRefs.current.get(shown[next].point.index)?.focus()
            return
        }
        if (onSelect && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault()
            onSelect(shown[position].point.index)
        }
    }

    const labels = placeLabels(profile, shown)
    return (
        <div className="path-figure-stage">
            <svg className="path-figure-svg" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={label} aria-describedby={captionId}>
                {yScale.ticks.map((tick) => (
                    <g key={tick}>
                        <line className={tick === 0 ? "path-figure-zero" : "path-figure-grid"} x1={LEFT} x2={W - RIGHT} y1={sy(tick)} y2={sy(tick)} />
                        <text className="path-figure-tick t-preserve-case" x={LEFT - 8} y={sy(tick) + 5} textAnchor="end">{tick === 0 ? "0" : relativeKjText(tick).replace(/\.0$/, "")}</text>
                    </g>
                ))}
                {xScale.ticks.map((tick) => (
                    <text key={tick} className="path-figure-tick t-preserve-case" x={sx(tick)} y={H - BOTTOM + 24} textAnchor="middle">
                        {profile.hasCoordinate ? String(tick).replace("-", "−") : Number.isInteger(tick) ? String(tick) : ""}
                    </text>
                ))}
                <text className="path-figure-axis-title t-preserve-case" x={LEFT} y={18}>{profile.yTitle}</text>
                <text className="path-figure-axis-title t-preserve-case" x={W - RIGHT} y={H - 10} textAnchor="end">{profile.xTitle}</text>
                {activePlaced && <line className="path-figure-crosshair" x1={activePlaced.sx} x2={activePlaced.sx} y1={TOP - 6} y2={H - BOTTOM + 6} />}
                {linePaths(shown).map((d) => <path key={d} className="path-figure-line" d={d} />)}
                {labels.map((item) => (
                    <text
                        key={item.key}
                        className={item.strong ? "path-figure-label path-figure-label--strong t-preserve-case" : "path-figure-label t-preserve-case"}
                        x={item.x}
                        y={item.y}
                        textAnchor={item.anchor}
                    >
                        {item.text}
                    </text>
                ))}
                <rect
                    className="path-figure-hover"
                    x={LEFT - 12}
                    y={TOP - 12}
                    width={PLOT_W + 24}
                    height={PLOT_H + 24}
                    onPointerMove={(event) => setActive(nearest(event))}
                    onPointerLeave={() => setActive(focus)}
                    onClick={onSelect ? (event) => { const index = nearest(event); if (index !== null) onSelect(index) } : undefined}
                />
                {shown.map((item, position) => (
                    <g
                        key={item.point.index}
                        ref={(element) => { markerRefs.current.set(item.point.index, element) }}
                        className={item.point.index === selectedIndex ? "path-figure-point path-figure-point--selected" : "path-figure-point"}
                        tabIndex={item.point.index === rovingIndex ? 0 : -1}
                        role={onSelect ? "button" : "img"}
                        aria-label={pointSentence(profile, item.point)}
                        aria-pressed={onSelect ? item.point.index === selectedIndex : undefined}
                        data-point-index={item.point.index}
                        onPointerEnter={() => setActive(item.point.index)}
                        onFocus={() => { setFocus(item.point.index); setActive(item.point.index) }}
                        onBlur={() => { setFocus(null); setActive(null) }}
                        onKeyDown={(event) => move(event, position)}
                        onClick={onSelect ? () => onSelect(item.point.index) : undefined}
                    >
                        {item.point.index === selectedIndex && <circle className="path-figure-ring" cx={item.sx} cy={item.sy} r={MARKER_R + 6} />}
                        <Marker {...item} />
                    </g>
                ))}
            </svg>
            {activePlaced && <Tooltip profile={profile} active={activePlaced} height={H} />}
        </div>
    )
}

function markerWords(point: PathPoint): string {
    return [point.leg ?? "", ...point.labels].filter(Boolean).join(", ")
}

function PathTable({ profile, label }: { profile: PathProfile; label: string }) {
    return (
        <div className="table-scroll path-figure-scroll" tabIndex={0} role="region" aria-label={label}>
            <table className="data-table path-figure-table">
                <caption className="path-figure-caption-table">Every stored point, in the order drawn</caption>
                <thead>
                    <tr>
                        <th scope="col">Point</th>
                        <th scope="col">{profile.xColumn}</th>
                        <th scope="col">Electronic energy (hartree)</th>
                        <th scope="col" className="t-preserve-case">Relative (kJ/mol)</th>
                        <th scope="col">RMS gradient</th>
                        <th scope="col">Marker</th>
                        <th scope="col">Geometry</th>
                    </tr>
                </thead>
                <tbody>
                    {profile.points.map((point) => (
                        <tr key={point.index} data-point-index={point.index} className={point.emphasis ? "path-figure-row--marked" : undefined}>
                            <td data-label="Point" className="num">{point.index}</td>
                            <td data-label={profile.xColumn} className="num">{coordinateText(profile, point.coordinate)}</td>
                            <td data-label="Electronic energy (hartree)" className="num">{point.hartree === null ? NOT_DEPOSITED : hartreeText(point.hartree)}</td>
                            <td data-label="Relative (kJ/mol)" className="num">{point.kj === null ? NOT_DEPOSITED : relativeKjText(point.kj)}</td>
                            <td data-label="RMS gradient" className="num">{point.rmsGradient === null ? NOT_DEPOSITED : point.rmsGradient}</td>
                            <td data-label="Marker">{markerWords(point)}</td>
                            <td data-label="Geometry">
                                {point.geometryRef
                                    ? <RecordLink kind="geometry" to={`/geometries/${point.geometryRef}`} label={geometryWords(point.natoms)} context={`point ${point.index}`} />
                                    : NOT_DEPOSITED}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

/**
 * Energy along a path as one single-axis picture, or the same points as a table. Markers are hollow; the picked
 * guess or saddle point is filled, a climbing image is a diamond. Pointer and keyboard both move a crosshair with a
 * tooltip; with `onSelect` a point can be chosen.
 */
export function PathFigure({ profile, label, caption, selectedIndex, onSelect }: {
    profile: PathProfile
    /** The picture's accessible name, e.g. "Energy along the path". */
    label: string
    caption: ReactNode
    selectedIndex?: number | null
    onSelect?: (index: number) => void
}) {
    const captionId = useId()
    const [view, setView] = useState<"picture" | "table">("picture")
    const boxRef = useRef<HTMLDivElement | null>(null)
    const overflows = useOverflows(boxRef)
    return (
        <figure className="path-figure">
            <div className="path-figure-toggle" role="group" aria-label={`Show ${label.toLowerCase()} as`}>
                <button type="button" aria-pressed={view === "picture"} onClick={() => setView("picture")}>Picture</button>
                <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
            </div>
            {view === "picture" ? (
                <>
                    {overflows && <p className="path-figure-hint">Scroll sideways inside the box to see the whole picture. The table has the same numbers.</p>}
                    <div
                        className="path-figure-box"
                        ref={boxRef}
                        {...(overflows ? { tabIndex: 0, role: "region", "aria-label": `${label}, scrolls sideways` } : {})}
                    >
                        <Picture profile={profile} captionId={captionId} label={label} selectedIndex={selectedIndex} onSelect={onSelect} />
                    </div>
                </>
            ) : (
                <PathTable profile={profile} label={`${label}, every point`} />
            )}
            <figcaption id={captionId} className="path-figure-caption">
                {caption}
                {profile.undrawnCount > 0 && ` ${profile.undrawnCount} of ${profile.points.length} points have no energy and are not drawn; the table lists them.`}
            </figcaption>
        </figure>
    )
}
