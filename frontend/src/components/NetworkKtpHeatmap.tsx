import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import "../network-ktp-heatmap.css"
import type { NetworkChannel, NetworkFullRecord, NetworkState } from "../api/networkEntryApi"
import { loadNetworkKtpEvaluation, type NetworkKtpEvaluatedPoint, type NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { arrheniusUnitLabel } from "../domain/arrheniusChartLayout"
import { scientificText } from "../domain/kineticsTable"
import { buildKtpRequestGrid, groupKtpFitsByChannel, modelKindLabel } from "../domain/networkKtpChartLayout"
import { channelHandle } from "../domain/publicText"
import { useAnnounce } from "../hooks/useAnnounce"
import { useOverflows } from "../hooks/useOverflows"
import { Disclosure } from "./Disclosure"
import { ValueLink } from "./ValueLink"

const COLUMN_COUNT = 8
const CELL_W = 68
const CELL_H = 34
const LEFT = 76
const TOP = 12
const BOTTOM = 40
const HEAT_STEPS = 5

/** Evenly spaced picks from the requested temperatures, so every column is a value that was asked for. */
function heatmapGrid(network: NetworkFullRecord["network"]): { temperaturesK: number[]; pressuresBar: number[] } | null {
    const full = buildKtpRequestGrid(network.solve_temperature_min_k, network.solve_temperature_max_k, network.solve_pressure_min_bar, network.solve_pressure_max_bar)
    if (!full) return null
    const last = full.temperaturesK.length - 1
    const picks = Array.from(new Set(Array.from({ length: COLUMN_COUNT }, (_unused, i) => Math.round((i * last) / (COLUMN_COUNT - 1)))))
    return { temperaturesK: picks.map((index) => full.temperaturesK[index]), pressuresBar: full.pressuresBar }
}

function close(a: number, b: number): boolean {
    return Math.abs(a - b) <= Math.max(Math.abs(b) * 1e-9, 1e-9)
}

function pointAt(fit: NetworkKtpFit, temperatureK: number, pressureBar: number): NetworkKtpEvaluatedPoint | null {
    return fit.points.find((point) => close(point.temperature_k, temperatureK) && close(point.pressure_bar, pressureBar)) ?? null
}

function barText(pressureBar: number): string {
    return String(Number(pressureBar.toPrecision(3)))
}

function kelvinText(temperatureK: number): string {
    return String(Math.round(temperatureK))
}

function cellText(k: number): string {
    return k > 0 ? k.toExponential(1).replace("e+", "e") : "n/a"
}

/** Step 0..4 by log10 k within one record's own values. */
function heatStep(k: number, low: number, high: number): number {
    if (!(k > 0) || !(high > low)) return 0
    const share = (Math.log10(k) - low) / (high - low)
    return Math.min(HEAT_STEPS - 1, Math.max(0, Math.floor(share * HEAT_STEPS)))
}

type Option = { fit: NetworkKtpFit; label: string }

function fitOptions(fits: NetworkKtpFit[], channels: NetworkChannel[], states: NetworkState[]): Option[] {
    const raw = groupKtpFitsByChannel(fits, channels, states).flatMap((group) => group.series.map((fit) => ({ fit, label: `${group.label}, ${modelKindLabel(fit.model_kind)}` })))
    const seen = new Map<string, number>()
    const totals = new Map<string, number>()
    for (const option of raw) totals.set(option.label, (totals.get(option.label) ?? 0) + 1)
    return raw.map((option) => {
        const n = (seen.get(option.label) ?? 0) + 1
        seen.set(option.label, n)
        return (totals.get(option.label) ?? 0) > 1 ? { ...option, label: `${option.label} (${n} of ${totals.get(option.label)})` } : option
    })
}

/** A box that scrolls sideways when its content is wider, and then says so and takes focus. */
function ScrollBox({ label, children }: { label: string; children: ReactNode }) {
    const boxRef = useRef<HTMLDivElement | null>(null)
    const overflows = useOverflows(boxRef)
    return (
        <>
            <div className="ktp-box" ref={boxRef} {...(overflows ? { tabIndex: 0, role: "region", "aria-label": label } : {})}>{children}</div>
            {overflows && <p className="net-map-hint">Scroll sideways inside the box to see every column. The table has the same values.</p>}
        </>
    )
}

function Slider({ label, count, index, valueText, onChange }: {
    label: string
    count: number
    index: number
    valueText: string
    onChange: (next: number) => void
}) {
    const id = useId()
    const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
        let next = index
        if (event.key === "ArrowRight" || event.key === "ArrowUp") next = Math.min(count - 1, index + 1)
        else if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = Math.max(0, index - 1)
        else if (event.key === "Home") next = 0
        else if (event.key === "End") next = count - 1
        else return
        event.preventDefault()
        if (next !== index) onChange(next)
    }
    return (
        <div className="ktp-field">
            <span id={id}>{label}</span>
            <div
                className="ktp-slider"
                role="slider"
                tabIndex={0}
                aria-labelledby={id}
                aria-valuemin={0}
                aria-valuemax={count - 1}
                aria-valuenow={index}
                aria-valuetext={valueText}
                onKeyDown={onKeyDown}
            >
                <span className="t-preserve-case">{valueText}</span>
                <span className="ktp-slider-hint" aria-hidden="true">arrow keys</span>
            </div>
        </div>
    )
}

/**
 * k(T,P) for one deposited rate record at a time, as the archive evaluated it: one request, the stored fit's own
 * values, nothing interpolated here. Every cell shows its value; the table below carries the same values.
 */
export function NetworkKtpHeatmap({ networkRef, network, channels, states }: {
    networkRef: string
    network: NetworkFullRecord["network"]
    channels: NetworkChannel[]
    states: NetworkState[]
}) {
    const grid = useMemo(() => heatmapGrid(network), [network])
    const anyKinetics = channels.some((channel) => channel.has_kinetics)
    const [state, setState] = useState<{ status: "loading" } | { status: "error" } | { status: "ready"; fits: NetworkKtpFit[] }>({ status: "loading" })
    const [fitRef, setFitRef] = useState<string | null>(null)
    const [temperatureIndex, setTemperatureIndex] = useState(Math.floor(COLUMN_COUNT / 2))
    const [pressureIndex, setPressureIndex] = useState(2)
    const announce = useAnnounce()
    const captionId = useId()

    useEffect(() => {
        if (!grid || !anyKinetics) return
        const controller = new AbortController()
        loadNetworkKtpEvaluation(networkRef, grid.temperaturesK, grid.pressuresBar, controller.signal)
            .then((result) => setState({ status: "ready", fits: result.fits }))
            .catch((error: unknown) => {
                if (error instanceof DOMException && error.name === "AbortError") return
                setState({ status: "error" })
            })
        return () => controller.abort()
    }, [networkRef, grid, anyKinetics])

    const options = useMemo(() => (state.status === "ready" ? fitOptions(state.fits, channels, states) : []), [state, channels, states])

    if (!anyKinetics) return <p className="empty-projection">No rate records are deposited on this network.</p>
    if (!grid) return <p className="empty-projection">This network records no temperature and pressure range, so nothing was asked of the archive.</p>
    if (state.status === "loading") return <p className="t-body">Asking the archive for the stored rate records.</p>
    if (state.status === "error") return <p className="empty-projection">The rate records could not be evaluated right now.</p>
    if (options.length === 0) return <p className="empty-projection">No rate records are deposited on this network.</p>

    const chosen = options.find((option) => option.fit.network_kinetics_ref === fitRef) ?? options[0]
    const { fit } = chosen
    const { temperaturesK, pressuresBar } = grid
    const unit = arrheniusUnitLabel(fit.k_units)
    const channel = channels.find((candidate) => candidate.channel_key === fit.channel_key)
    const evidence = channel?.microreactions[0]?.reaction_entry_ref
    const evidencePath = evidence ? `/reaction-entries/${evidence}` : null

    const values = temperaturesK.flatMap((t) => pressuresBar.map((p) => pointAt(fit, t, p)?.k ?? 0)).filter((k) => k > 0).map(Math.log10)
    const low = Math.min(...values)
    const high = Math.max(...values)
    const rows = [...pressuresBar].map((p, index) => ({ p, index })).reverse()

    const readout = (tIndex: number, pIndex: number): string => {
        const point = pointAt(fit, temperaturesK[tIndex], pressuresBar[pIndex])
        const where = `${kelvinText(temperaturesK[tIndex])} K and ${barText(pressuresBar[pIndex])} bar`
        if (!point || !(point.k > 0)) return `k at ${where}: no value`
        return `k at ${where}: ${scientificText(point.k)} ${unit}${point.in_range ? "" : ", outside the record's stated range"}`
    }
    const move = (tIndex: number, pIndex: number) => {
        setTemperatureIndex(tIndex)
        setPressureIndex(pIndex)
        announce(readout(tIndex, pIndex))
    }
    const picked = pointAt(fit, temperaturesK[temperatureIndex], pressuresBar[pressureIndex])
    const width = LEFT + temperaturesK.length * CELL_W + 8
    const height = TOP + pressuresBar.length * CELL_H + BOTTOM

    return (
        <div className="ktp">
            <div className="ktp-controls">
                <div className="ktp-field">
                    <label htmlFor="ktp-record">Rate record</label>
                    <select id="ktp-record" value={fit.network_kinetics_ref} onChange={(event) => setFitRef(event.target.value)}>
                        {options.map((option) => (
                            <option key={option.fit.network_kinetics_ref} value={option.fit.network_kinetics_ref}>{option.label}</option>
                        ))}
                    </select>
                </div>
                <Slider label="Pressure" count={pressuresBar.length} index={pressureIndex} valueText={`${barText(pressuresBar[pressureIndex])} bar`} onChange={(next) => move(temperatureIndex, next)} />
                <Slider label="Temperature" count={temperaturesK.length} index={temperatureIndex} valueText={`${kelvinText(temperaturesK[temperatureIndex])} K`} onChange={(next) => move(next, pressureIndex)} />
            </div>
            <p className="ktp-readout" data-channel-key={channelHandle(fit.channel_key)}>
                {`k at ${kelvinText(temperaturesK[temperatureIndex])} K and ${barText(pressuresBar[pressureIndex])} bar: `}
                {picked && picked.k > 0
                    ? <ValueLink value={scientificText(picked.k)} unit={unit} to={evidencePath} />
                    : "no value"}
                {picked && picked.k > 0 && !picked.in_range && " (outside the record's stated range)"}
            </p>
            <figure>
                <ScrollBox label="k(T,P) heat map, scrolls sideways">
                    <svg className="ktp-svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={captionId}>
                        {rows.map(({ p, index: pIndex }, rowIndex) => (
                            <g key={p}>
                                <text className="ktp-axis t-preserve-case" x={LEFT - 8} y={TOP + rowIndex * CELL_H + CELL_H / 2 + 4} textAnchor="end">{barText(p)} bar</text>
                                {temperaturesK.map((t, tIndex) => {
                                    const point = pointAt(fit, t, p)
                                    const step = point ? heatStep(point.k, low, high) : null
                                    const chosenCell = tIndex === temperatureIndex && pIndex === pressureIndex
                                    const x = LEFT + tIndex * CELL_W
                                    const y = TOP + rowIndex * CELL_H
                                    return (
                                        <g key={t} onClick={() => move(tIndex, pIndex)}>
                                            <rect
                                                x={x}
                                                y={y}
                                                width={CELL_W}
                                                height={CELL_H}
                                                className={`ktp-cell ${point && point.k > 0 ? `ktp-heat-${step}` : "ktp-heat-none"}${chosenCell ? " ktp-cell--chosen" : ""}`}
                                                data-testid={`ktp-cell-${tIndex}-${pIndex}`}
                                            />
                                            <text className="ktp-value t-preserve-case" x={x + CELL_W / 2} y={y + CELL_H / 2 + 4} textAnchor="middle">
                                                {point ? `${cellText(point.k)}${point.in_range ? "" : "†"}` : "n/a"}
                                            </text>
                                        </g>
                                    )
                                })}
                            </g>
                        ))}
                        {temperaturesK.map((t, tIndex) => (
                            <text key={t} className="ktp-axis t-preserve-case" x={LEFT + tIndex * CELL_W + CELL_W / 2} y={TOP + pressuresBar.length * CELL_H + 18} textAnchor="middle">{kelvinText(t)} K</text>
                        ))}
                    </svg>
                </ScrollBox>
                <figcaption id={captionId} className="ktp-caption">
                    {`k in ${unit}, for ${chosen.label}. Darker is larger, within this record only. † is outside the record's stated range.`}
                </figcaption>
            </figure>
            <Disclosure summary="Table of the same values">
                <div className="table-scroll ktp-table-scroll">
                    <table className="data-table ktp-table" aria-label="k at each pressure and temperature">
                        <caption className="t-preserve-case">{`k in ${unit}; ${chosen.label}`}</caption>
                        <thead>
                            <tr>
                                <th scope="col">Pressure</th>
                                {temperaturesK.map((t) => <th key={t} scope="col" className="t-preserve-case">{kelvinText(t)} K</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map(({ p }) => (
                                <tr key={p}>
                                    <th scope="row" className="t-preserve-case">{barText(p)} bar</th>
                                    {temperaturesK.map((t) => {
                                        const point = pointAt(fit, t, p)
                                        return (
                                            <td key={t} className="num">
                                                {point && point.k > 0
                                                    ? <><ValueLink value={scientificText(point.k)} to={evidencePath} />{!point.in_range && <span title="Outside the record's stated range"> †</span>}</>
                                                    : "n/a"}
                                            </td>
                                        )
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </Disclosure>
        </div>
    )
}
