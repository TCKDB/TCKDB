import { Link } from "react-router-dom"
import type { ReactionKineticsRecord, ReactionFullCalculationEvidence } from "../api/reactionEntryApi"
import { EvidenceChecklist } from "./EvidenceChecklist"
import { ProductLevelsFact } from "./ProductLevels"
import { resolveProductLevels } from "../domain/productLevels"
import { buildCalculationsByRef, deriveKineticsLevelsFallback } from "../domain/reactionKineticsLevels"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { reviewPillClass } from "../domain/reviewPillFormat"

function token(value: string): string {
    return value.replaceAll("_", " ")
}

// `ArrheniusAUnits` (`backend/app/db/models/common.py`) -> the typeset unit
// string this record's own `A` is reported in. A token this table has not
// been taught yet falls back to the raw token, spaces for underscores --
// never blocks the page on an unrecognised enum value.
const A_UNIT_LABELS: Record<string, string> = {
    per_s: "s⁻¹",
    cm3_mol_s: "cm³ mol⁻¹ s⁻¹",
    cm3_molecule_s: "cm³ molecule⁻¹ s⁻¹",
    m3_mol_s: "m³ mol⁻¹ s⁻¹",
    cm6_mol2_s: "cm⁶ mol⁻² s⁻¹",
    cm6_molecule2_s: "cm⁶ molecule⁻² s⁻¹",
    m6_mol2_s: "m⁶ mol⁻² s⁻¹",
}

function aUnitLabel(units: string | null | undefined): string {
    if (!units) return ""
    return A_UNIT_LABELS[units] ?? token(units)
}

function formatUncertainty(u: ReactionKineticsRecord["uncertainty"]): string | null {
    const parts: string[] = []
    if (u.A_uncertainty != null) {
        const kind = u.A_uncertainty_kind
        const symbol = kind === "multiplicative" ? `×/÷ ${u.A_uncertainty}` : `± ${u.A_uncertainty}`
        parts.push(`A ${symbol}${kind ? ` (${kind})` : ""}`)
    }
    if (u.n_uncertainty != null) parts.push(`n ± ${u.n_uncertainty}`)
    if (u.Ea_uncertainty_kj_mol != null) parts.push(`Ea ± ${u.Ea_uncertainty_kj_mol} kJ/mol`)
    return parts.length ? parts.join(" · ") : null
}

/** R in kJ mol⁻¹ K⁻¹ -- the same constant plan §4's future `domain/arrhenius.ts` names. */
const GAS_CONSTANT_KJ_MOL_K = 8.314462618e-3
const TABLE_POINT_COUNT = 12

function arrheniusTermK(A: number, n: number | null | undefined, Ea_kj_mol: number | null | undefined, temperatureK: number): number {
    const exponent = n ?? 0
    const ea = Ea_kj_mol ?? 0
    return A * Math.pow(temperatureK, exponent) * Math.exp(-ea / (GAS_CONSTANT_KJ_MOL_K * temperatureK))
}

/**
 * k(T) sampled at `TABLE_POINT_COUNT` evenly spaced temperatures over the
 * record's own fitted range -- the table equivalent for the Arrhenius
 * chart PR 3 ships (plan §4/§6: "PR 2 owns cards + the k(T) TABLE only").
 * Only computed for a plain `arrhenius`/`modified_arrhenius` record (a
 * single `A`) or `multi_arrhenius` (summed over its own terms) -- a
 * pressure-dependent form (PLOG/Chebyshev/falloff) has no single k(T)
 * curve without a pressure, and is excluded with a note rather than
 * plotted against an unstated pressure.
 */
function computeKineticsTable(record: ReactionKineticsRecord): { temperatureK: number; k: number }[] | null {
    if (record.plog_entries || record.chebyshev || record.falloff) return null
    const min = record.temperature_coverage?.record_min_k
    const max = record.temperature_coverage?.record_max_k
    if (min == null || max == null || !(max > min)) return null

    const terms = record.multi_arrhenius && record.multi_arrhenius.length > 0
        ? record.multi_arrhenius
        : record.parameters.A != null
            ? [{ A: record.parameters.A, n: record.parameters.n, Ea_kj_mol: record.parameters.Ea_kj_mol }]
            : null
    if (!terms) return null

    const rows: { temperatureK: number; k: number }[] = []
    for (let i = 0; i < TABLE_POINT_COUNT; i++) {
        const temperatureK = min + (i * (max - min)) / (TABLE_POINT_COUNT - 1)
        const k = terms.reduce((sum, term) => sum + arrheniusTermK(term.A, term.n, term.Ea_kj_mol, temperatureK), 0)
        rows.push({ temperatureK, k })
    }
    return rows
}

function log10(value: number): string {
    if (value <= 0) return "n/a"
    return Math.log10(value).toFixed(4)
}

function scientificText(value: number): string {
    if (value === 0) return "0"
    const exponent = Math.floor(Math.log10(Math.abs(value)))
    const mantissa = value / Math.pow(10, exponent)
    return `${mantissa.toFixed(4)}×10${superscript(exponent)}`
}

const SUPERSCRIPT_DIGITS: Record<string, string> = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻",
}

function superscript(value: number): string {
    return String(value).split("").map((ch) => SUPERSCRIPT_DIGITS[ch] ?? ch).join("")
}

export function ReactionKineticsSection({ kinetics, calculations, networksStatus, networkRef }: {
    kinetics: ReactionKineticsRecord[]
    calculations: ReactionFullCalculationEvidence[] | null | undefined
    /** Whether this entry's network membership is known yet, and whether it has any -- decides the empty-kinetics wording. */
    networksStatus: "loading" | "empty" | "populated"
    /** The first network ref, used only when `networksStatus === "populated"`. */
    networkRef?: string
}) {
    if (kinetics.length === 0) {
        if (networksStatus === "populated" && networkRef) {
            return (
                <>
                    <p className="empty-projection">
                        No rate coefficient deposited on this entry; phenomenological k(T,P) for this system is
                        served by network <a href="#network-heading">{networkRef}</a>.
                    </p>
                    <p className="note">
                        A network-only reaction never renders channel kinetics as if they were this entry's own --
                        see the Pressure-dependent network section below for what the network itself serves.
                    </p>
                </>
            )
        }
        return <p className="empty-projection">No rate coefficient has been deposited for this reaction entry.</p>
    }

    const calculationsByRef = buildCalculationsByRef(calculations)

    return (
        <div className="kinetics-record-list">
            {kinetics.map((record) => (
                <KineticsRecordCard key={record.kinetics_ref} record={record} calculationsByRef={calculationsByRef} />
            ))}
        </div>
    )
}

function KineticsRecordCard({ record, calculationsByRef }: {
    record: ReactionKineticsRecord
    calculationsByRef: Map<string, ReactionFullCalculationEvidence>
}) {
    // `resolveProductLevels`'s own fallback path expects `source_calculations[]`
    // rows, a shape kinetics records do not carry -- so when the server's
    // own `levels` is absent this record uses ITS OWN fallback
    // (`deriveKineticsLevelsFallback`, the TS-chain cross-reference plan §7
    // describes) rather than the generic `source_calculations` derivation.
    // When `levels` IS present, `resolveProductLevels` is still the one
    // normaliser used everywhere else in the app (missing sub-fields ->
    // `null`, never `undefined`), so it stays the single source of truth
    // for "trust the server's own answer as-is".
    const resolvedLevels = record.levels
        ? resolveProductLevels(record.levels, undefined)
        : deriveKineticsLevelsFallback(record.provenance, calculationsByRef)

    const uncertaintyText = formatUncertainty(record.uncertainty)
    const table = computeKineticsTable(record)
    const evidenceRows = Object.entries(record.evidence_completeness.checklist).map(([key, passed]) => ({
        label: token(key),
        value: passed ? "present" : "absent",
        tone: passed ? ("pill" as const) : ("pill-muted" as const),
    }))

    const tRange = record.temperature_coverage?.record_min_k != null && record.temperature_coverage?.record_max_k != null
        ? `${record.temperature_coverage.record_min_k}–${record.temperature_coverage.record_max_k} K`
        : null

    return (
        <div className="card">
            <dl className="kv-list">
                <div><dt>Kinetics ref</dt><dd><code className="data">{record.kinetics_ref}</code></dd></div>
                <div><dt>Model kind</dt><dd>{token(record.model_kind)}</dd></div>
                <div><dt>Origin</dt><dd>{token(record.scientific_origin)}</dd></div>
                <div><dt>Review</dt><dd><span className={reviewPillClass(record.review.status)}>{token(record.review.status)}</span></dd></div>
                {record.parameters.A != null && (
                    <div><dt>A</dt><dd><code className="data">{record.parameters.A} {aUnitLabel(record.parameters.A_units)}</code></dd></div>
                )}
                {record.parameters.n != null && (
                    <div><dt>n</dt><dd><code className="data">{record.parameters.n}</code></dd></div>
                )}
                {record.parameters.Ea_kj_mol != null && (
                    <div><dt>Ea</dt><dd><code className="data">{record.parameters.Ea_kj_mol} kJ/mol</code></dd></div>
                )}
                {tRange && <div><dt>Fitted T range</dt><dd><code className="data">{tRange}</code></dd></div>}
                {uncertaintyText && <div><dt>Uncertainty</dt><dd>{uncertaintyText}</dd></div>}
                {record.tunneling_model && <div><dt>Tunnelling model</dt><dd>{token(record.tunneling_model)}</dd></div>}
                <div><dt>Third body</dt><dd>{record.is_third_body ? "yes" : "no"}</dd></div>
                <ProductLevelsFact levels={resolvedLevels} />
                {record.provenance.primary_software && (
                    <div><dt>Software</dt><dd>{softwareLabel(record.provenance.primary_software)}</dd></div>
                )}
                {record.provenance.workflow_tool_release && (
                    <div><dt>Workflow tool</dt><dd>{toolReleaseLabel(record.provenance.workflow_tool_release)}</dd></div>
                )}
                {record.provenance.software_release && (
                    <div><dt>Fit software</dt><dd>{softwareLabel(record.provenance.software_release) ?? "not recorded"}</dd></div>
                )}
                <div><dt>Literature</dt><dd>{record.provenance.literature ? (record.provenance.literature.title ?? record.provenance.literature.literature_ref) : <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
            </dl>

            <EvidenceChecklist
                heading={`Evidence completeness — ${record.evidence_completeness.score} of ${record.evidence_completeness.max}`}
                rows={evidenceRows}
            />

            {record.provenance.network_kinetics_ref && (
                <p className="note">
                    Bridged to pressure-dependent network kinetics <code className="data">{record.provenance.network_kinetics_ref}</code>.
                </p>
            )}

            <dl className="kv-list" style={{ marginTop: "1.5rem" }}>
                <div>
                    <dt>TS opt calculation (kinetics' own link)</dt>
                    <dd>
                        {record.provenance.ts_opt_calculation_ref
                            ? <Link to={`/calculations/${record.provenance.ts_opt_calculation_ref}`}><code className="data">{record.provenance.ts_opt_calculation_ref}</code></Link>
                            : <span className="record-identity-absent-inline">not recorded</span>}
                    </dd>
                </div>
                <div>
                    <dt>TS freq calculation (kinetics' own link)</dt>
                    <dd>
                        {record.provenance.ts_freq_calculation_ref
                            ? <Link to={`/calculations/${record.provenance.ts_freq_calculation_ref}`}><code className="data">{record.provenance.ts_freq_calculation_ref}</code></Link>
                            : <span className="record-identity-absent-inline">not recorded</span>}
                    </dd>
                </div>
                <div>
                    <dt>TS sp calculation (kinetics' own link)</dt>
                    <dd>
                        {record.provenance.ts_sp_calculation_ref
                            ? <code className="data">{record.provenance.ts_sp_calculation_ref}</code>
                            : <span className="record-identity-absent-inline">not recorded</span>}
                    </dd>
                </div>
                {record.provenance.transition_state_entry_ref && (
                    <div>
                        <dt>Transition-state entry used</dt>
                        <dd><Link to={`/transition-state-entries/${record.provenance.transition_state_entry_ref}`}>{record.provenance.transition_state_entry_ref}</Link></dd>
                    </div>
                )}
            </dl>

            {table && (
                <details className="disclosure">
                    <summary>k(T) table <span className="disclosure-count">({table.length})</span></summary>
                    <div className="disclosure-body">
                        <div className="table-scroll">
                            <table className="data-table" aria-label={`k(T) for ${record.kinetics_ref}`}>
                                <caption>
                                    k(T) = A·T^n·exp(−Ea/(R·T)){record.parameters.A_units ? `, in ${aUnitLabel(record.parameters.A_units)}` : ""}.
                                    The Arrhenius chart itself ships in a follow-up PR; this table is its
                                    accessible/table equivalent, computed client-side from the deposited
                                    parameters.
                                </caption>
                                <thead>
                                    <tr>
                                        <th scope="col">T (K)</th>
                                        <th scope="col">k</th>
                                        <th scope="col">log₁₀ k</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {table.map((row) => (
                                        <tr key={row.temperatureK}>
                                            <td className="num" data-label="T (K)">{row.temperatureK.toFixed(2)}</td>
                                            <td className="num" data-label="k">{scientificText(row.k)}</td>
                                            <td className="num" data-label="log10 k">{log10(row.k)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </details>
            )}
            {!table && Boolean(record.plog_entries || record.chebyshev || record.falloff) && (
                <p className="note">
                    This record's rate form ({token(record.model_kind)}) is pressure-dependent and is not plotted
                    as k(T) here -- see its own parameter fields above.
                </p>
            )}
        </div>
    )
}
