import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import { lotLabel } from "../api/scientificSchemas"
import type { ThermoListResponse, ThermoRecord } from "../api/thermoApi"
import { softwareLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { useEntryThermo } from "../hooks/useEntryThermo"
import { QuantityValue } from "./QuantityValue"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
import { SupersessionNotice } from "./SupersessionNotice"

// ---------------------------------------------------------------------------
// The thermo/statmech/transport read surfaces are ENTRY-SCOPED LISTS
// (`GET /species-entries/{id}/thermo` etc.), not `thermo_ref`-addressed
// detail routes — there is no standalone `/thermo/:ref` page in this
// project and this slice does not build one (the API this endpoint serves
// has no matching per-record route to build a page around). This component
// therefore renders every deposited thermo record for the entry, each as
// its own independent card — never flattened into one merged answer, never
// reduced to "the first one" or "the best one". See `api/thermoApi.ts` for
// the measured wire shape and why nothing here is include-gated.
// ---------------------------------------------------------------------------

const MODEL_KIND_LABELS: Record<string, string> = {
    nasa: "NASA-7",
    nasa9: "NASA-9",
    wilhoit: "Wilhoit",
    points: "Point-based",
    scalar: "Scalar",
}
const modelKindLabel = (kind: string) => MODEL_KIND_LABELS[kind] ?? kind.replaceAll("_", " ")
const statusLabel = (status: string) => status.replaceAll("_", " ")

export function EntryThermoSection({ entryRef }: { entryRef: string }) {
    const state = useEntryThermo(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="thermo-heading">
                        <h2 id="thermo-heading">Thermochemistry</h2>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <ThermoList response={state.record} />
            </SectionErrorBoundary>
        )
    }
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="thermochemistry"
            loadingDetail="Retrieving the deposited thermochemistry records for this entry."
        />
    )
}

function reviewSummaryText(summary: ThermoListResponse["review_summary"]) {
    const parts: string[] = []
    if (summary.approved) parts.push(`${summary.approved} approved`)
    if (summary.under_review) parts.push(`${summary.under_review} under review`)
    if (summary.not_reviewed) parts.push(`${summary.not_reviewed} not reviewed`)
    if (summary.deprecated) parts.push(`${summary.deprecated} deprecated`)
    if (summary.rejected) parts.push(`${summary.rejected} rejected`)
    return parts.length > 0 ? parts.join(" · ") : "no records"
}

function ThermoList({ response }: { response: ThermoListResponse }) {
    const { records, review_summary: reviewSummary, pagination } = response
    return (
        <section className="ledger-section" aria-labelledby="thermo-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Deposited evidence</p>
                <h2 id="thermo-heading">Thermochemistry</h2>
                <p>
                    Every thermo record deposited for this entry, each shown independently. Multiple deposits
                    are never merged, averaged, or reduced to one preferred value on this page.
                </p>
            </div>
            <p className="records-note">
                {pagination.total} record{pagination.total === 1 ? "" : "s"}
                {pagination.total > pagination.returned ? ` (showing ${pagination.returned})` : ""}
                {" · review: "}{reviewSummaryText(reviewSummary)}
            </p>
            {records.length === 0 ? (
                <p className="empty-projection">No thermochemistry records are deposited for this entry.</p>
            ) : (
                records.map((record) => (
                    <SectionErrorBoundary
                        key={record.thermo_ref}
                        fallback={(
                            <article className="science-record" role="alert">
                                <p className="empty-projection">
                                    Record <code>{record.thermo_ref}</code> could not be displayed. Other
                                    records on this page are unaffected.
                                </p>
                            </article>
                        )}
                    >
                        <ThermoRecordCard record={record} />
                    </SectionErrorBoundary>
                ))
            )}
        </section>
    )
}

function ThermoRecordCard({ record }: { record: ThermoRecord }) {
    return (
        <article className="science-record" aria-labelledby={`thermo-heading-${record.thermo_ref}`}>
            <div className="science-record-heading">
                <h3 id={`thermo-heading-${record.thermo_ref}`}>{modelKindLabel(record.model_kind)} thermo record</h3>
                <span className="review-badge">{statusLabel(record.review.status)}</span>
                <code>{record.thermo_ref}</code>
            </div>

            {record.supersession && <SupersessionNotice supersession={record.supersession} />}

            <dl className="kv-list">
                <div><dt>Scientific origin</dt><dd>{record.scientific_origin}</dd></div>
                <div><dt>Model kind</dt><dd>{record.model_kind}</dd></div>
                <div><dt>H298</dt><dd><QuantityValue value={formatQuantity("thermo_h298_kj_mol", record.h298_kj_mol)} /></dd></div>
                <div>
                    <dt>H298 uncertainty</dt>
                    <dd><QuantityValue value={formatQuantity("thermo_h298_uncertainty_kj_mol", record.h298_uncertainty_kj_mol)} /></dd>
                </div>
                <div><dt>S298</dt><dd><QuantityValue value={formatQuantity("thermo_s298_j_mol_k", record.s298_j_mol_k)} /></dd></div>
                <div>
                    <dt>S298 uncertainty</dt>
                    <dd><QuantityValue value={formatQuantity("thermo_s298_uncertainty_j_mol_k", record.s298_uncertainty_j_mol_k)} /></dd>
                </div>
            </dl>

            <TemperatureCoverageBlock coverage={record.temperature_coverage ?? null} thermoRef={record.thermo_ref} />
            <NasaBlock nasa={record.nasa ?? null} thermoRef={record.thermo_ref} />
            <Nasa9Block nasa9={record.nasa9 ?? null} thermoRef={record.thermo_ref} />
            <WilhoitBlock wilhoit={record.wilhoit ?? null} thermoRef={record.thermo_ref} />
            <PointsBlock points={record.points ?? null} thermoRef={record.thermo_ref} />
            <EvidenceCompletenessBlock completeness={record.evidence_completeness ?? null} thermoRef={record.thermo_ref} />
            <ProvenanceBlock provenance={record.provenance ?? null} thermoRef={record.thermo_ref} />
            <GroupAdditivityBlock groupAdditivity={record.group_additivity ?? null} thermoRef={record.thermo_ref} />
        </article>
    )
}

function TemperatureCoverageBlock({ coverage, thermoRef }: {
    coverage: ThermoRecord["temperature_coverage"] | null
    thermoRef: string
}) {
    return (
        <section aria-labelledby={`coverage-${thermoRef}`}>
            <h4 className="model-block-heading" id={`coverage-${thermoRef}`}>Temperature coverage</h4>
            {coverage ? (
                <dl className="kv-list">
                    <div>
                        <dt>Record range (K)</dt>
                        <dd>{coverage.record_min_k ?? "not recorded"}–{coverage.record_max_k ?? "not recorded"}</dd>
                    </div>
                    <div>
                        <dt>Requested range (K)</dt>
                        <dd>
                            {coverage.requested_min_k == null && coverage.requested_max_k == null
                                ? "No temperature filter applied"
                                : `${coverage.requested_min_k ?? "?"}–${coverage.requested_max_k ?? "?"}`}
                        </dd>
                    </div>
                    <div><dt>Covers requested range</dt><dd>{coverage.covers_requested_range ? "Yes" : "No"}</dd></div>
                    <div><dt>Extrapolation distance (K)</dt><dd>{coverage.extrapolation_distance_k}</dd></div>
                </dl>
            ) : <p className="empty-projection">No temperature coverage computed for this record.</p>}
        </section>
    )
}

function NasaBlock({ nasa, thermoRef }: { nasa: ThermoRecord["nasa"] | null; thermoRef: string }) {
    return (
        <section aria-labelledby={`nasa7-${thermoRef}`}>
            <h4 className="model-block-heading" id={`nasa7-${thermoRef}`}>NASA-7 polynomial</h4>
            {nasa ? (
                <>
                    <dl className="kv-list">
                        <div><dt>T low (K)</dt><dd>{nasa.t_low ?? "not recorded"}</dd></div>
                        <div><dt>T mid (K)</dt><dd>{nasa.t_mid ?? "not recorded"}</dd></div>
                        <div><dt>T high (K)</dt><dd>{nasa.t_high ?? "not recorded"}</dd></div>
                    </dl>
                    <div className="table-scroll">
                        <table className="stage-table" aria-label={`NASA-7 coefficients for ${thermoRef}`}>
                            <thead>
                                <tr>
                                    <th scope="col">Range</th>
                                    {Array.from({ length: 7 }, (_, index) => <th scope="col" key={`a${index + 1}`}>a{index + 1}</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td data-label="Range">Low</td>
                                    {(nasa.low_temperature_coefficients ?? []).map((coefficient, index) => (
                                        <td data-label={`a${index + 1}`} key={`low-${index}`}>{coefficient ?? "not recorded"}</td>
                                    ))}
                                </tr>
                                <tr>
                                    <td data-label="Range">High</td>
                                    {(nasa.high_temperature_coefficients ?? []).map((coefficient, index) => (
                                        <td data-label={`a${index + 1}`} key={`high-${index}`}>{coefficient ?? "not recorded"}</td>
                                    ))}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </>
            ) : <p className="empty-projection">No NASA-7 polynomial recorded for this record.</p>}
        </section>
    )
}

function Nasa9Block({ nasa9, thermoRef }: { nasa9: ThermoRecord["nasa9"] | null; thermoRef: string }) {
    return (
        <section aria-labelledby={`nasa9-${thermoRef}`}>
            <h4 className="model-block-heading" id={`nasa9-${thermoRef}`}>NASA-9 polynomial</h4>
            {nasa9 && nasa9.length > 0 ? (
                <div className="table-scroll">
                    <table className="stage-table" aria-label={`NASA-9 intervals for ${thermoRef}`}>
                        <thead>
                            <tr>
                                <th scope="col">Interval</th>
                                <th scope="col">T min (K)</th>
                                <th scope="col">T max (K)</th>
                                {Array.from({ length: 9 }, (_, index) => <th scope="col" key={`a${index + 1}`}>a{index + 1}</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {nasa9.map((interval) => (
                                <tr key={`nasa9-${thermoRef}-${interval.interval_index}`}>
                                    <td data-label="Interval">{interval.interval_index}</td>
                                    <td data-label="T min (K)">{interval.t_min_k}</td>
                                    <td data-label="T max (K)">{interval.t_max_k}</td>
                                    <td data-label="a1">{interval.a1}</td>
                                    <td data-label="a2">{interval.a2}</td>
                                    <td data-label="a3">{interval.a3}</td>
                                    <td data-label="a4">{interval.a4}</td>
                                    <td data-label="a5">{interval.a5}</td>
                                    <td data-label="a6">{interval.a6}</td>
                                    <td data-label="a7">{interval.a7}</td>
                                    <td data-label="a8">{interval.a8}</td>
                                    <td data-label="a9">{interval.a9}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">No NASA-9 polynomial recorded for this record.</p>}
        </section>
    )
}

function WilhoitBlock({ wilhoit, thermoRef }: { wilhoit: ThermoRecord["wilhoit"] | null; thermoRef: string }) {
    return (
        <section aria-labelledby={`wilhoit-${thermoRef}`}>
            <h4 className="model-block-heading" id={`wilhoit-${thermoRef}`}>Wilhoit form</h4>
            {wilhoit ? (
                <dl className="kv-list">
                    <div><dt>Cp0 (J/mol/K)</dt><dd>{wilhoit.cp0_j_mol_k}</dd></div>
                    <div><dt>Cp∞ (J/mol/K)</dt><dd>{wilhoit.cp_inf_j_mol_k}</dd></div>
                    <div><dt>B (K)</dt><dd>{wilhoit.b_k}</dd></div>
                    <div><dt>a0 / a1 / a2 / a3</dt><dd>{wilhoit.a0}, {wilhoit.a1}, {wilhoit.a2}, {wilhoit.a3}</dd></div>
                    <div><dt>H0 (kJ/mol)</dt><dd>{wilhoit.h0_kj_mol ?? "not recorded"}</dd></div>
                    <div><dt>S0 (J/mol/K)</dt><dd>{wilhoit.s0_j_mol_k ?? "not recorded"}</dd></div>
                </dl>
            ) : <p className="empty-projection">No Wilhoit fit recorded for this record.</p>}
        </section>
    )
}

function PointsBlock({ points, thermoRef }: { points: ThermoRecord["points"] | null; thermoRef: string }) {
    return (
        <section aria-labelledby={`points-${thermoRef}`}>
            <h4 className="model-block-heading" id={`points-${thermoRef}`}>Evaluated points</h4>
            {points && points.length > 0 ? (
                <details>
                    <summary>{points.length} temperature point{points.length === 1 ? "" : "s"}</summary>
                    <div className="table-scroll table-scroll--compact">
                        <table className="stage-table" aria-label={`Evaluated thermo points for ${thermoRef}`}>
                            <thead>
                                <tr>
                                    <th scope="col">T (K)</th>
                                    <th scope="col">Cp (J/mol/K)</th>
                                    <th scope="col">H (kJ/mol)</th>
                                    <th scope="col">S (J/mol/K)</th>
                                    <th scope="col">G (kJ/mol)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {points.map((point, index) => (
                                    <tr key={`${thermoRef}-point-${index}`}>
                                        <td data-label="T (K)">{point.temperature_k}</td>
                                        <td data-label="Cp (J/mol/K)">{point.cp_j_mol_k ?? "not recorded"}</td>
                                        <td data-label="H (kJ/mol)">{point.h_kj_mol ?? "not recorded"}</td>
                                        <td data-label="S (J/mol/K)">{point.s_j_mol_k ?? "not recorded"}</td>
                                        <td data-label="G (kJ/mol)">{point.g_kj_mol ?? "not recorded"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            ) : <p className="empty-projection">No evaluated points recorded for this record.</p>}
        </section>
    )
}

function EvidenceCompletenessBlock({ completeness, thermoRef }: {
    completeness: ThermoRecord["evidence_completeness"] | null
    thermoRef: string
}) {
    // Consistent with `NasaBlock`/`Nasa9Block`/`WilhoitBlock`/`PointsBlock`:
    // an absent block renders its own heading and an explicit "not
    // recorded" line, never a silently missing section. In practice this
    // field is always present on the wire (`evidence_completeness:
    // EvidenceCompletenessBreakdown`, not `| None`, per
    // `scientific_thermo.py`) — this branch is defensive, not a real
    // absence this client expects to see.
    if (!completeness) {
        return (
            <section aria-labelledby={`completeness-${thermoRef}`}>
                <h4 className="model-block-heading" id={`completeness-${thermoRef}`}>Evidence completeness</h4>
                <p className="empty-projection">No evidence-completeness breakdown recorded for this record.</p>
            </section>
        )
    }
    return (
        <section aria-labelledby={`completeness-${thermoRef}`}>
            <h4 className="model-block-heading" id={`completeness-${thermoRef}`}>
                Evidence completeness ({completeness.score} / {completeness.max})
            </h4>
            <ul className="checklist">
                {Object.entries(completeness.checklist).map(([key, value]) => (
                    <li key={key}>{value ? "Present" : "Absent"} — {key.replaceAll("_", " ")}</li>
                ))}
            </ul>
        </section>
    )
}

function ProvenanceBlock({ provenance, thermoRef }: {
    provenance: ThermoRecord["provenance"] | null
    thermoRef: string
}) {
    // Same consistency rule as `EvidenceCompletenessBlock` above — always
    // present on the wire per `ThermoProvenance` (not `| None`), so this
    // branch is defensive.
    if (!provenance) {
        return (
            <section aria-labelledby={`provenance-${thermoRef}`}>
                <h4 className="model-block-heading" id={`provenance-${thermoRef}`}>Provenance</h4>
                <p className="empty-projection">No provenance block recorded for this record.</p>
            </section>
        )
    }
    return (
        <section aria-labelledby={`provenance-${thermoRef}`}>
            <h4 className="model-block-heading" id={`provenance-${thermoRef}`}>Provenance</h4>
            <dl className="kv-list">
                <div>
                    <dt>Level of theory</dt>
                    <dd>{provenance.level_of_theory ? lotLabel(provenance.level_of_theory) : "not recorded"}</dd>
                </div>
                <div><dt>Level of theory ref</dt><dd>{provenance.level_of_theory?.level_of_theory_ref ?? "not recorded"}</dd></div>
                <div>
                    <dt>Software</dt>
                    <dd>{softwareLabel(provenance.software) ?? "not recorded"}</dd>
                </div>
                <div>
                    <dt>Primary calculation</dt>
                    <dd>
                        {provenance.primary_calculation?.calculation_ref
                            ? <Link to={`/calculations/${provenance.primary_calculation.calculation_ref}`}>{provenance.primary_calculation.calculation_ref}</Link>
                            : "not recorded"}
                    </dd>
                </div>
                <div>
                    <dt>Frequency calculation</dt>
                    <dd>
                        {provenance.freq_calculation_ref
                            ? <Link to={`/calculations/${provenance.freq_calculation_ref}`}>{provenance.freq_calculation_ref}</Link>
                            : "not recorded"}
                    </dd>
                </div>
                <div>
                    <dt>Single-point calculation</dt>
                    <dd>
                        {provenance.sp_calculation_ref
                            ? <Link to={`/calculations/${provenance.sp_calculation_ref}`}>{provenance.sp_calculation_ref}</Link>
                            : "not recorded"}
                    </dd>
                </div>
                {/* No dedicated statmech detail page exists in this project (see the
                    module docstring), so this stays plain text rather than a dead link. */}
                <div><dt>Statmech ref</dt><dd>{provenance.statmech_ref ?? "not recorded"}</dd></div>
            </dl>
        </section>
    )
}

function GroupAdditivityBlock({ groupAdditivity, thermoRef }: {
    groupAdditivity: ThermoRecord["group_additivity"] | null
    thermoRef: string
}) {
    // `group_additivity` genuinely is `null` on the wire for any record
    // that isn't an estimated thermo with an attached GA breakdown (unlike
    // the two blocks above) — it is named in `_response.py`'s "absent
    // scientific fact" list alongside nasa/nasa9/wilhoit/points. Consistent
    // with those siblings: render the heading and an explicit "not
    // recorded" line rather than omitting the section entirely.
    if (!groupAdditivity) {
        return (
            <section aria-labelledby={`ga-${thermoRef}`}>
                <h4 className="model-block-heading" id={`ga-${thermoRef}`}>Group-additivity estimation</h4>
                <p className="empty-projection">No group-additivity estimation recorded for this record.</p>
            </section>
        )
    }
    return (
        <section aria-labelledby={`ga-${thermoRef}`}>
            <h4 className="model-block-heading" id={`ga-${thermoRef}`}>Group-additivity estimation</h4>
            <dl className="kv-list">
                <div>
                    <dt>Scheme</dt>
                    <dd>{groupAdditivity.scheme_name}{groupAdditivity.scheme_version ? ` (${groupAdditivity.scheme_version})` : ""}</dd>
                </div>
                <div><dt>Scheme ref</dt><dd>{groupAdditivity.scheme_ref}</dd></div>
                <div><dt>Code commit</dt><dd>{groupAdditivity.code_commit ?? "not recorded"}</dd></div>
            </dl>
            {groupAdditivity.components && groupAdditivity.components.length > 0 && (
                <div className="table-scroll table-scroll--compact">
                    <table className="stage-table" aria-label="Group-additivity components">
                        <thead>
                            <tr>
                                <th scope="col">Group</th>
                                <th scope="col">Kind</th>
                                <th scope="col">Count</th>
                                <th scope="col">H298 contribution (kJ/mol)</th>
                                <th scope="col">S298 contribution (J/mol/K)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groupAdditivity.components.map((component, index) => (
                                <tr key={`${thermoRef}-ga-${index}`}>
                                    <td data-label="Group">{component.group_label}</td>
                                    <td data-label="Kind">{component.component_kind}</td>
                                    <td data-label="Count">{component.count}</td>
                                    <td data-label="H298 contribution (kJ/mol)">{component.h298_contribution_kj_mol ?? "not recorded"}</td>
                                    <td data-label="S298 contribution (J/mol/K)">{component.s298_contribution_j_mol_k ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    )
}
