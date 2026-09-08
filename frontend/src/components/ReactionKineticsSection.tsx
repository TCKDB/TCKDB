import { Link } from "react-router-dom"
import type { ReactionFullCalculationEvidence, ReactionKineticsRecord, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { formatArrheniusValue } from "../domain/kineticsTable"
import { ArrheniusChart } from "./ArrheniusChart"
import { EvidenceChecklist } from "./EvidenceChecklist"
import { ProductLevelsFact } from "./ProductLevels"
import { resolveProductLevels } from "../domain/productLevels"
import { buildCalculationsByRef, buildDependencyEdgesByChildRef, deriveKineticsLevelsFallback } from "../domain/reactionKineticsLevels"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { reviewPillClass } from "../domain/reviewPillFormat"

function token(value: string): string {
    return value.replaceAll("_", " ")
}

// `ArrheniusAUnits` (`backend/app/db/models/common.py`) -> the typeset unit
// string this record's own `A` is reported in. A token this table has not
// been taught yet falls back to the raw token, spaces for underscores —
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

// `evidence_completeness.checklist`'s nine keys (`backend/app/services/scientific_read/kinetics.py`'s
// own `checklist = {...}` literal) -> the mock's prose labels. A key this
// table has not been taught yet (a future ninth-plus addition) falls back
// to `token(key)`, the same "never block on an unrecognised value" rule
// `aUnitLabel` follows above.
const EVIDENCE_LABELS: Record<string, string> = {
    has_source_calculations: "Source calculations",
    has_transition_state_entry: "Transition-state entry",
    has_ts_opt_evidence: "TS opt evidence",
    has_ts_freq_evidence: "TS freq evidence",
    has_ts_sp_evidence: "TS sp evidence",
    has_path_search_or_irc_evidence: "Path search or IRC evidence",
    has_uncertainty: "Uncertainty",
    has_geometry_validation: "Geometry validation",
    has_scf_stability: "SCF stability",
}

function evidenceLabel(key: string): string {
    return EVIDENCE_LABELS[key] ?? token(key)
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

/**
 * A software release without a recorded version (this record's own
 * "kinetics fit" software, Arkane, on the live sample entry) must say so,
 * not silently print just the name — mirrors `TransitionStateEntryPage.tsx`'s
 * own `softwareCellText` helper (that page's local copy, not exported;
 * duplicated here rather than reached into a page-scoped file).
 */
function softwareCellText(release: { software: string; version?: string | null } | null | undefined): string | null {
    if (!release) return null
    if (release.version === null || release.version === undefined || release.version === "") {
        return `${release.software} (version not recorded)`
    }
    return softwareLabel(release)
}

export function ReactionKineticsSection({ kinetics, calculations, transitionStates, networksStatus, networkRef }: {
    kinetics: ReactionKineticsRecord[]
    calculations: ReactionFullCalculationEvidence[] | null | undefined
    transitionStates: ReactionTransitionStateInFull[]
    /** Whether this entry's network membership is known yet, and whether it has any — decides the empty-kinetics wording. */
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
                        A network-only reaction never renders channel kinetics as if they were this entry's own —
                        see the Pressure-dependent network section below for what the network itself serves.
                    </p>
                </>
            )
        }
        return <p className="empty-projection">No rate coefficient has been deposited for this reaction entry.</p>
    }

    const calculationsByRef = buildCalculationsByRef(calculations)
    const dependencyEdgesByChildRef = buildDependencyEdgesByChildRef(transitionStates)

    return (
        <>
            <div className="kinetics-record-list">
                {kinetics.map((record) => (
                    <KineticsRecordCard
                        key={record.kinetics_ref}
                        record={record}
                        calculationsByRef={calculationsByRef}
                        dependencyEdgesByChildRef={dependencyEdgesByChildRef}
                    />
                ))}
            </div>
            {/* One combined Arrhenius plot (one panel per A_units ORDER
                FAMILY -- unimolecular/bimolecular/termolecular, so e.g. a
                cm3_mol_s record and an m3_mol_s record share one panel and
                one unit selector; only a genuine dimensional difference,
                like per_s beside cm3_mol_s, still gets two panels)
                spanning every deposited kinetics record, ABOVE its own k(T)
                table equivalent -- both after the per-record cards, per
                plan §2's "one card per KineticsRecord ... Then the
                Arrhenius chart with its table equivalent." A PLOG/
                Chebyshev/falloff/third-body record is listed there by ref
                and reason, never plotted. */}
            <ArrheniusChart kinetics={kinetics} />
        </>
    )
}

function KineticsRecordCard({ record, calculationsByRef, dependencyEdgesByChildRef }: {
    record: ReactionKineticsRecord
    calculationsByRef: ReturnType<typeof buildCalculationsByRef>
    dependencyEdgesByChildRef: ReturnType<typeof buildDependencyEdgesByChildRef>
}) {
    // `resolveProductLevels`'s own fallback path expects `source_calculations[]`
    // rows, a shape kinetics records do not carry — so when the server's
    // own `levels` is absent this record uses ITS OWN fallback
    // (`deriveKineticsLevelsFallback`, the dependency-edge walk plan §7 and
    // PR 398's own post-review commit describe) rather than the generic
    // `source_calculations` derivation. When `levels` IS present (every
    // live record, as of PR 398's deploy), `resolveProductLevels` is still
    // the one normaliser used everywhere else in the app (missing
    // sub-fields -> `null`, never `undefined`), so it stays the single
    // source of truth for "trust the server's own answer as-is".
    const { levels: resolvedLevels, energyFallbackNote } = record.levels
        ? { levels: resolveProductLevels(record.levels, undefined), energyFallbackNote: null }
        : deriveKineticsLevelsFallback(record.provenance, calculationsByRef, dependencyEdgesByChildRef)

    const uncertaintyText = formatUncertainty(record.uncertainty)
    const evidenceRows = Object.entries(record.evidence_completeness.checklist).map(([key, passed]) => ({
        label: evidenceLabel(key),
        value: passed ? "present" : "absent",
        tone: passed ? ("pill" as const) : ("pill-muted" as const),
    }))

    const tRange = record.temperature_coverage?.record_min_k != null && record.temperature_coverage?.record_max_k != null
        ? `${record.temperature_coverage.record_min_k}–${record.temperature_coverage.record_max_k} K`
        : null

    const unitLabel = aUnitLabel(record.parameters.A_units)

    // The SP row is the one that can name a calculation not among this
    // page's own `calculations[]` (the plan §7 "resolver disagreement"
    // case) — still a REAL, resolvable calc ref in the archive either way,
    // so it stays a link the same as opt/freq above it; `spNote` carries
    // the honesty caveat about where the row's ref came from when it
    // genuinely isn't part of this reaction's own TS graph.
    const spKnown = record.provenance.ts_sp_calculation_ref
        ? calculationsByRef.has(record.provenance.ts_sp_calculation_ref)
        : true
    const spNote = record.provenance.ts_sp_calculation_ref && !spKnown
        ? "not among the calculations this reaction entry's transition-state graph itself lists"
        : null

    return (
        <div className="card">
            <dl className="kv-list">
                <div><dt>Kinetics ref</dt><dd><code className="data">{record.kinetics_ref}</code></dd></div>
                <div><dt>Model kind</dt><dd>{token(record.model_kind)}</dd></div>
                <div><dt>Origin</dt><dd>{token(record.scientific_origin)}</dd></div>
                <div><dt>Review</dt><dd><span className={reviewPillClass(record.review.status)}>{token(record.review.status)}</span></dd></div>
                {/* SCIENTIFIC ERROR fixed here (sweep finding): `.kv-list dt`
                    (design-system.css) uppercases every fact label via
                    `--type-label-transform`. "n" here is the Arrhenius
                    temperature exponent -- upper-cased to "N" it collides
                    visually with the unrelated symbol N, the same class of
                    defect as "Epsilon / k (K)" (EntryTransportSection.tsx)
                    and "log₁₀ k" (arrhenius-chart.css)'s own y-axis fix.
                    "A"/"Ea" are the same Arrhenius-parameter family, kept
                    consistent alongside it. `.t-preserve-case`
                    (design-system.css) is the shared fix. */}
                {record.parameters.A != null && (
                    <div><dt className="t-preserve-case">A</dt><dd><code className="data">{formatArrheniusValue(record.parameters.A)} {unitLabel}</code></dd></div>
                )}
                {record.parameters.n != null && (
                    <div><dt className="t-preserve-case">n</dt><dd><code className="data">{formatArrheniusValue(record.parameters.n)}</code></dd></div>
                )}
                {record.parameters.Ea_kj_mol != null && (
                    <div><dt className="t-preserve-case">Ea</dt><dd><code className="data">{formatArrheniusValue(record.parameters.Ea_kj_mol)} kJ/mol</code></dd></div>
                )}
                {tRange && <div><dt>Fitted T range</dt><dd><code className="data">{tRange}</code></dd></div>}
                {uncertaintyText && <div><dt>Uncertainty</dt><dd>{uncertaintyText}</dd></div>}
                {record.tunneling_model && <div><dt>Tunnelling model</dt><dd>{token(record.tunneling_model)}</dd></div>}
                <div><dt>Third body</dt><dd>{record.is_third_body ? "yes" : "no"}</dd></div>
            </dl>

            {/* A SEPARATE `<dl>`, not appended to the facts list above --
                MEASURED (post-review): in one shared grid, the auto-flow
                column count let Geometry/Frequencies/Energy split across a
                row boundary with "Software" interleaved into the same row
                as one of them. Each `<dl>` here is its own independent
                grid, so a group can never spill into an unrelated one. */}
            <dl className="kv-list reaction-product-levels">
                <ProductLevelsFact levels={resolvedLevels} />
            </dl>
            {energyFallbackNote && <p className="note">{energyFallbackNote}</p>}

            <dl className="kv-list">
                {record.provenance.primary_software && (
                    <div><dt>Software</dt><dd>{softwareCellText(record.provenance.primary_software)}</dd></div>
                )}
                {record.provenance.workflow_tool_release && (
                    <div><dt>Workflow tool</dt><dd>{toolReleaseLabel(record.provenance.workflow_tool_release)}</dd></div>
                )}
                {record.provenance.software_release && (
                    <div><dt>Fit software</dt><dd>{softwareCellText(record.provenance.software_release) ?? "not recorded"}</dd></div>
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

            <dl className="kv-list kinetics-own-links">
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
                            ? <Link to={`/calculations/${record.provenance.ts_sp_calculation_ref}`}><code className="data">{record.provenance.ts_sp_calculation_ref}</code></Link>
                            : <span className="record-identity-absent-inline">not recorded</span>}
                        {spNote && <div className="note">({spNote})</div>}
                    </dd>
                </div>
                {record.provenance.transition_state_entry_ref && (
                    <div>
                        <dt>Transition-state entry used</dt>
                        <dd><Link to={`/transition-state-entries/${record.provenance.transition_state_entry_ref}`}>{record.provenance.transition_state_entry_ref}</Link></dd>
                    </div>
                )}
            </dl>
        </div>
    )
}
