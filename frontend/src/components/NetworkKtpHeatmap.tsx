import { Fragment, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import "../network-ktp-heatmap.css"
import "../record-blocks.css"
import type { NetworkChannel, NetworkFullRecord, NetworkState } from "../api/networkEntryApi"
import { loadNetworkKtpEvaluation, type NetworkKtpEvaluatedPoint, type NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { arrheniusUnitLabel } from "../domain/arrheniusChartLayout"
import { buildKtpRequestGrid, groupKtpFitsByChannel, modelKindLabel } from "../domain/networkKtpChartLayout"
import { channelRowId } from "../domain/networkMap"
import { channelHandle } from "../domain/publicText"
import { evaluated, type Quantity } from "../domain/quantityFormat"
import { useAnnounce } from "../hooks/useAnnounce"
import { useOverflows } from "../hooks/useOverflows"
import { Disclosure } from "./Disclosure"
import { QuantityValue, EVALUATED_NOTE } from "./QuantityValue"

const COLUMN_COUNT = 8
const CELL_W = 72
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

const SUPERSCRIPT: Record<string, string> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻", "−": "⁻" }

function superscript(exponent: string): string {
    return exponent.split("").map((char) => SUPERSCRIPT[char] ?? char).join("")
}

/** The served k to 6 significant figures, as text, in the house form. */
function kText(k: number): string {
    const quantity: Quantity | null = evaluated(k)
    if (!quantity) return "no value"
    return quantity.exponent ? `${quantity.value} × 10${superscript(quantity.exponent)}` : quantity.value
}

/** The same number at 2 figures for a heat-map cell, with a written exponent. */
function cellText(k: number): string {
    if (k === 0) return "0"
    const [mantissa, exponent] = Math.abs(k).toExponential(1).split("e")
    return `${k < 0 ? "−" : ""}${mantissa}×10${superscript(exponent.replace("+", ""))}`
}

/** Step 0..4 by log10 k within one record's own positive values. */
function heatStep(k: number, low: number, high: number): number | null {
    if (!(k > 0)) return null
    if (!(high > low)) return 0
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

function rangeText(fit: NetworkKtpFit): string {
    const pair = (low: number | null | undefined, high: number | null | undefined, unit: string) =>
        (low == null || high == null ? `not stated (${unit})` : `${String(Number(low.toPrecision(6)))}–${String(Number(high.toPrecision(6)))} ${unit}`)
    return `${pair(fit.tmin_k, fit.tmax_k, "K")}, ${pair(fit.pmin_bar, fit.pmax_bar, "bar")}`
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
    const go = (next: number) => {
        const clamped = Math.min(count - 1, Math.max(0, next))
        if (clamped !== index) onChange(clamped)
    }
    const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
        const steps: Record<string, number> = { ArrowRight: index + 1, ArrowUp: index + 1, ArrowLeft: index - 1, ArrowDown: index - 1, PageUp: index + 2, PageDown: index - 2, Home: 0, End: count - 1 }
        if (!Object.hasOwn(steps, event.key)) return
        event.preventDefault()
        go(steps[event.key])
    }
    return (
        <div className="ktp-field">
            <span id={id}>{label}</span>
            <div className="ktp-slider-row">
                <button type="button" className="ktp-step" aria-label={`Lower ${label.toLowerCase()}`} disabled={index === 0} onClick={() => go(index - 1)}>−</button>
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
                <button type="button" className="ktp-step" aria-label={`Raise ${label.toLowerCase()}`} disabled={index === count - 1} onClick={() => go(index + 1)}>+</button>
            </div>
        </div>
    )
}

type Loaded = { networkRef: string; status: "ready"; fits: NetworkKtpFit[] } | { networkRef: string; status: "error" }

/**
 * k(T,P) for one deposited rate record at a time, as the archive evaluated it: one request, the stored fit's own
 * values, nothing interpolated here. Every cell shows its value; the table lists every record of the channel.
 */
export function NetworkKtpHeatmap({ networkRef, network, channels, states }: {
    networkRef: string
    network: NetworkFullRecord["network"]
    channels: NetworkChannel[]
    states: NetworkState[]
}) {
    const grid = useMemo(() => heatmapGrid(network), [network])
    const anyKinetics = channels.some((channel) => channel.has_kinetics)
    const [loaded, setLoaded] = useState<Loaded | null>(null)
    const [pick, setPick] = useState<{ networkRef: string; fitRef: string } | null>(null)
    const [temperatureIndex, setTemperatureIndex] = useState(Math.floor(COLUMN_COUNT / 2))
    const [pressureIndex, setPressureIndex] = useState(2)
    const announce = useAnnounce()
    const captionId = useId()
    const recordId = useId()

    useEffect(() => {
        if (!grid || !anyKinetics) return
        const controller = new AbortController()
        loadNetworkKtpEvaluation(networkRef, grid.temperaturesK, grid.pressuresBar, controller.signal)
            .then((result) => setLoaded({ networkRef, status: "ready", fits: result.fits }))
            .catch((error: unknown) => {
                if (error instanceof DOMException && error.name === "AbortError") return
                setLoaded({ networkRef, status: "error" })
            })
        return () => controller.abort()
    }, [networkRef, grid, anyKinetics])

    // Only what was loaded for this network counts: after a move to another network the page is loading again.
    const current = loaded?.networkRef === networkRef ? loaded : null
    const fits = current?.status === "ready" ? current.fits : null
    const options = useMemo(() => (fits ? fitOptions(fits, channels, states) : []), [fits, channels, states])

    if (!anyKinetics) return <p className="empty-projection">No rate records are deposited on this network.</p>
    if (!grid) return <p className="empty-projection">This network records no temperature and pressure range, so nothing was asked of the archive.</p>
    if (!current) return <p className="t-body">Asking the archive for the stored rate records.</p>
    if (current.status === "error") return <p className="empty-projection">The rate records could not be evaluated right now.</p>
    if (options.length === 0) return <p className="empty-projection">No rate records are deposited on this network.</p>

    const chosenRef = pick?.networkRef === networkRef ? pick.fitRef : null
    const chosen = options.find((option) => option.fit.network_kinetics_ref === chosenRef) ?? options[0]
    const { fit } = chosen
    const { temperaturesK, pressuresBar } = grid
    const unit = arrheniusUnitLabel(fit.k_units)
    const channelIndex = channels.findIndex((candidate) => candidate.channel_key === fit.channel_key)
    const evidence = channelIndex >= 0 ? `#${channelRowId(channelIndex)}` : null
    const siblings = options.filter((option) => option.fit.channel_key === fit.channel_key)

    const positive = temperaturesK.flatMap((t) => pressuresBar.map((p) => pointAt(fit, t, p)?.k ?? 0)).filter((k) => k > 0).map(Math.log10)
    const low = Math.min(...positive)
    const high = Math.max(...positive)
    const rows = pressuresBar.map((p, index) => ({ p, index })).reverse()

    const link = (body: ReactNode) => (evidence ? <a className="value-link" href={evidence}>{body}</a> : body)
    const where = (tIndex: number, pIndex: number) => `${kelvinText(temperaturesK[tIndex])} K and ${barText(pressuresBar[pIndex])} bar`
    const readout = (tIndex: number, pIndex: number): string => {
        const point = pointAt(fit, temperaturesK[tIndex], pressuresBar[pIndex])
        if (!point) return `k at ${where(tIndex, pIndex)}: no value`
        return `k at ${where(tIndex, pIndex)}: ${kText(point.k)} ${unit}${point.in_range ? "" : ", outside the stated range"}`
    }
    const move = (tIndex: number, pIndex: number) => {
        setTemperatureIndex(tIndex)
        setPressureIndex(pIndex)
        announce(readout(tIndex, pIndex))
    }
    const picked = pointAt(fit, temperaturesK[temperatureIndex], pressuresBar[pressureIndex])
    const width = LEFT + temperaturesK.length * CELL_W + 8
    const height = TOP + pressuresBar.length * CELL_H + BOTTOM
    const duplicateKinds = new Set(siblings.map((option) => modelKindLabel(option.fit.model_kind)).filter((kind, index, all) => all.indexOf(kind) !== index))
    const columnName = (option: Option) => {
        const kind = modelKindLabel(option.fit.model_kind)
        return duplicateKinds.has(kind) ? `${kind} (${siblings.indexOf(option) + 1} of ${siblings.length})` : kind
    }

    return (
        <div className="ktp">
            <div className="ktp-controls">
                <div className="ktp-field">
                    <label htmlFor={recordId}>Rate record</label>
                    <select id={recordId} value={fit.network_kinetics_ref} onChange={(event) => setPick({ networkRef, fitRef: event.target.value })}>
                        {options.map((option) => (
                            <option key={option.fit.network_kinetics_ref} value={option.fit.network_kinetics_ref}>{option.label}</option>
                        ))}
                    </select>
                </div>
                <Slider label="Pressure" count={pressuresBar.length} index={pressureIndex} valueText={`${barText(pressuresBar[pressureIndex])} bar`} onChange={(next) => move(temperatureIndex, next)} />
                <Slider label="Temperature" count={temperaturesK.length} index={temperatureIndex} valueText={`${kelvinText(temperaturesK[temperatureIndex])} K`} onChange={(next) => move(next, pressureIndex)} />
            </div>
            <p className="ktp-readout" data-channel-key={channelHandle(fit.channel_key)}>
                {`k at ${where(temperatureIndex, pressureIndex)}: `}
                {picked
                    ? link(<QuantityValue value={evaluated(picked.k, unit)} />)
                    : "no value"}
                {picked && !picked.in_range && " (outside the stated range)"}
            </p>
            <figure>
                <ScrollBox label="k(T,P) heat map, scrolls sideways">
                    <svg className="ktp-svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-labelledby={captionId}>
                        {rows.map(({ p, index: pIndex }, rowIndex) => (
                            <g key={p}>
                                <text className="ktp-axis t-preserve-case" x={LEFT - 8} y={TOP + rowIndex * CELL_H + CELL_H / 2 + 4} textAnchor="end" aria-hidden="true">{barText(p)} bar</text>
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
                                                className={`ktp-cell ${step === null ? "ktp-heat-none" : `ktp-heat-${step}`}${chosenCell ? " ktp-cell--chosen" : ""}`}
                                                data-testid={`ktp-cell-${tIndex}-${pIndex}`}
                                            />
                                            <text className="ktp-value t-preserve-case" aria-hidden="true" x={x + CELL_W / 2} y={y + CELL_H / 2 + 4} textAnchor="middle">
                                                {point ? `${cellText(point.k)}${point.in_range ? "" : "†"}` : "n/a"}
                                            </text>
                                        </g>
                                    )
                                })}
                            </g>
                        ))}
                        {temperaturesK.map((t, tIndex) => (
                            <text key={t} className="ktp-axis t-preserve-case" aria-hidden="true" x={LEFT + tIndex * CELL_W + CELL_W / 2} y={TOP + pressuresBar.length * CELL_H + 18} textAnchor="middle">{kelvinText(t)} K</text>
                        ))}
                    </svg>
                </ScrollBox>
                <figcaption id={captionId} className="ktp-caption">
                    <span className="t-preserve-case">{`k in ${unit}, for ${chosen.label}. `}</span>
                    {"Stronger colour is larger, within this record only. † is outside the stated range ("}
                    <span className="t-preserve-case">{rangeText(fit)}</span>
                    {"). Record "}
                    <code className="t-preserve-case">{fit.network_kinetics_ref}</code>
                    {", solve "}
                    <code className="t-preserve-case">{fit.network_solve_ref}</code>
                    {". The table has the same values."}
                </figcaption>
            </figure>
            <Disclosure summary="Table of the same values, every record of this channel">
                <div className="table-scroll ktp-table-scroll">
                    <table className="data-table ktp-table" aria-label="k at each pressure and temperature">
                        <caption className="t-preserve-case">k, every record of this channel</caption>
                        <thead>
                            <tr>
                                <th scope="col">Pressure</th>
                                <th scope="col">Temperature</th>
                                {siblings.map((option) => (
                                    <th key={option.fit.network_kinetics_ref} scope="col" className="t-preserve-case">{`${columnName(option)}, ${arrheniusUnitLabel(option.fit.k_units)}`}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {pressuresBar.map((p) => temperaturesK.map((t) => (
                                <tr key={`${p}-${t}`}>
                                    <th scope="row" className="t-preserve-case">{barText(p)} bar</th>
                                    <td className="num t-preserve-case">{kelvinText(t)} K</td>
                                    {siblings.map((option) => {
                                        const point = pointAt(option.fit, t, p)
                                        return (
                                            <td key={option.fit.network_kinetics_ref} className="num">
                                                {point
                                                    ? <Fragment>{link(<QuantityValue value={evaluated(point.k)} />)}{!point.in_range && <span title="Outside the stated range"> †</span>}</Fragment>
                                                    : "n/a"}
                                            </td>
                                        )
                                    })}
                                </tr>
                            )))}
                        </tbody>
                    </table>
                </div>
                <p className="note">{EVALUATED_NOTE}</p>
            </Disclosure>
        </div>
    )
}
