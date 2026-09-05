import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import type { ConformerObservation } from "../api/conformerObservationApi"
import { lotLabel } from "../api/scientificSchemas"
import { Disclosure } from "../components/Disclosure"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { SpeciesEntryLink } from "../components/SpeciesEntryLink"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { useConformerObservation } from "../hooks/useConformerObservation"

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")
const originTitle = (origin?: string | null) => (
    origin ? `${origin.charAt(0).toUpperCase()}${origin.slice(1)} observation` : "Conformer observation"
)
type CalculationEntry = NonNullable<ConformerObservation["calculations"]>[number]
type GeometryLink = NonNullable<ConformerObservation["geometries"]>[number]
type SiblingObservation = NonNullable<ConformerObservation["observations"]>[number]

// Three states an include-gated section can be in, kept distinct per the
// house rule: absence describes the request, null describes the data.
// - "not-requested": the key was absent from the payload. This client
//   always requests every section, so in practice this should never
//   fire — but the type says `T[] | null | undefined`, and a section
//   that silently vanished from a future response must not be reported
//   as "returned and empty".
// - "empty": the key was present, and null or [] — the archive was
//   asked and had nothing to say.
// - "populated": at least one item came back.
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

export default function ConformerObservationPage() {
    const { observationRef = "" } = useParams<{ observationRef: string }>()
    const state = useConformerObservation(observationRef)

    if (state.status === "ready") return <ObservationDetail observation={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={observationRef}
            kind="conformer observation"
            loadingDetail="Retrieving the deposited observation and its provenance boundary to derived calculations."
        />
    )
}

function ObservationDetail({ observation }: { observation: ConformerObservation }) {
    const {
        conformer_observation: core,
        conformer_group: group,
        species,
        evidence_summary: evidence,
        available_sections: available,
    } = observation

    const calculationsAvailability = sectionAvailability(observation.calculations)
    const calculations = observation.calculations ?? []

    const geometriesAvailability = sectionAvailability(observation.geometries)
    const geometries = groupGeometries(observation.geometries ?? [])

    const observationsAvailability = sectionAvailability(observation.observations)
    const siblings = (observation.observations ?? [])
        .filter((sibling) => sibling.conformer_observation.conformer_observation_ref !== core.conformer_observation_ref)

    const reviewAvailability = sectionAvailability(observation.review_history)
    const reviewHistory = observation.review_history ?? []

    // has_selections is hardcoded false on this surface even when
    // selections were returned (backend `get_conformer_observation`
    // always passes `selection_count=0`), so it cannot gate anything —
    // the array itself is the only trustworthy signal here.
    const selections = observation.selections ?? []

    // A real timestamped or annotated status change, as opposed to a
    // placeholder row the archive returns even when nothing has happened
    // yet (status mirroring the observation's current status, with
    // `reviewed_at`/`note` both null). Only the former counts as an
    // "event" worth a table -- see the review-ledger section below, whose
    // owner-reported defect was a one-row table saying nothing beyond what
    // the hero badge already says.
    const hasReviewEvents = reviewHistory.some((entry) => entry.reviewed_at != null || entry.note != null)
    // Whether the "Review history" table will actually render below. The
    // intro sentence describes that table -- it should not appear when
    // there is no table to describe (see the `<p>` right above the
    // review-history section for the full history of this fix).
    const showReviewTable = reviewAvailability === "populated" && hasReviewEvents

    return (
        <section className="conformer-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species/${species.species_ref}`}>Species</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species-entries/${species.species_entry_ref}`}>Species entry</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/conformer-groups/${group.conformer_group_ref}`}>Conformer basin</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Observation</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        {/* Same shared kicker-row/h1/identity `.kv-list` markup
                            `RecordIdentityHeader` renders for the other record
                            pages, rendered by hand -- see `ConformerGroupPage.tsx`'s
                            identical comment for why this page cannot use that
                            component directly (its `species` context carries no
                            charge/multiplicity/InChIKey/formula in the shape
                            `RecordIdentity` needs). */}
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Conformer observation · deposited evidence</span>
                                <span className={reviewPillClass(core.review.status)}>{statusLabel(core.review.status)}</span>
                            </div>
                            <h1 className="t-display-1 record-identity-title">{originTitle(core.scientific_origin)}</h1>
                            <p className="t-body section-intro">
                                One deposition of evidence for this torsional basin, and the provenance boundary to the
                                calculations and geometries derived from it. This is one observation, not the basin
                                itself.
                            </p>
                            <dl className="kv-list">
                                {/* SHOULD-FIX-4 (PR B review): these refs are
                                    identifiers and get `<code className="data">`
                                    like every other ref on these five pages --
                                    see `ConformerGroupPage.tsx`'s identical
                                    comment on its own "Group ref" row for why
                                    an EARLIER version of this file kept them
                                    as plain `dd` text instead (to keep a test
                                    query passing) and why that was backwards.
                                    The tests below now query the `code`
                                    element. */}
                                <div><dt>Observation ref</dt><dd><code className="data">{core.conformer_observation_ref}</code></dd></div>
                                <div><dt>Scientific origin</dt><dd>{core.scientific_origin ?? "not recorded"}</dd></div>
                                <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                                <div>
                                    <dt>Conformer basin</dt>
                                    <dd>
                                        <Link to={`/conformer-groups/${group.conformer_group_ref}`}>
                                            {group.label ?? group.conformer_group_ref}
                                        </Link>
                                    </dd>
                                </div>
                                {/* Separate ref row only when the link above is showing
                                    something OTHER than the ref -- see
                                    `CalculationDetailPage.tsx`'s `OwnerCard` for the
                                    measured defect (species_entry_label null on every
                                    sampled entry) this same shape was fixed for. */}
                                {group.label && (
                                    <div><dt>Group ref</dt><dd><code className="data">{group.conformer_group_ref}</code></dd></div>
                                )}
                                <div>
                                    <dt>Species entry</dt>
                                    <dd>
                                        {/* Item 4/5 ("record-page residuals" re-review): no
                                            longer shows `species_entry_label` ALONE as
                                            link text -- see `SpeciesEntryLink`'s own
                                            docstring for why that field is a computed
                                            discriminator, not depositor free text, and why
                                            the fix is to always pair it with the formula
                                            rather than suppress it. This endpoint's
                                            `species` context carries no `formula` field at
                                            all, so the base text always falls back to the
                                            literal "Species entry"; the label, when
                                            present, still rides along after it. */}
                                        <SpeciesEntryLink
                                            speciesEntryRef={species.species_entry_ref}
                                            speciesEntryLabel={species.species_entry_label}
                                        />
                                    </dd>
                                </div>
                                <div><dt>Species ref</dt><dd><code className="data">{species.species_ref}</code></dd></div>
                                <div><dt>Structure</dt><dd>{species.canonical_smiles ? <code>{species.canonical_smiles}</code> : "not projected"}</dd></div>
                                {/* InChIKey and charge/multiplicity complete this page's
                                    identity tier -- served here (unlike the conformer
                                    basin surface, which does not carry them), so shown
                                    rather than left off for consistency with a
                                    thinner sibling endpoint. No classification-facet
                                    tier follows: this endpoint's `species` context has
                                    no `species_entry_kind`/`electronic_state_kind` to
                                    build one from, and no `submission_ref` provenance
                                    tier either -- both omitted rather than fabricated. */}
                                {species.inchi_key && <div><dt>InChIKey</dt><dd><code>{species.inchi_key}</code></dd></div>}
                                {(species.charge !== null && species.charge !== undefined
                                    && species.multiplicity !== null && species.multiplicity !== undefined) && (
                                    <div><dt>Charge / multiplicity</dt><dd>{species.charge} / {species.multiplicity}</dd></div>
                                )}
                            </dl>
                        </div>
                        {core.note && <p className="observation-note">{core.note}</p>}
                    </header>
                )}
            >
            <section className="ledger-summary" aria-label="Observation evidence summary">
                <Metric label="Calculation rows" value={evidence.calculation_count} />
                <Metric label="Distinct stored geometries" value={evidence.geometry_count} />
                <Metric label="Other observations in this basin" value={siblings.length} />
                {/* `.card--derived` -- see `ConformerGroupPage.tsx`'s identical
                    comment on its own coverage card (item 8). */}
                <div className="card card--derived coverage-card">
                    <span className="t-label">Evidence present on this observation</span>
                    <strong>
                        opt {evidence.has_opt ? "yes" : "no"} · freq {evidence.has_freq ? "yes" : "no"} · sp
                        {` ${evidence.has_sp ? "yes" : "no"}`} · geometry validation
                        {` ${evidence.has_geometry_validation ? "recorded" : "not recorded"}`} · SCF stability
                        {` ${evidence.has_scf_stability ? "recorded" : "not recorded"}`}
                    </strong>
                    <p className="note">
                        Presence says this observation carries that check, not that the result was favourable —
                        "SCF stability recorded" means a stability test ran, not that the wavefunction was stable.
                    </p>
                </div>
            </section>

            {/* The "Levels of theory by stage" section that used to sit here
                showed exactly the same (stage, level of theory) pairs as the
                "Stage" and "Level of theory" columns of the calculation
                table immediately below -- the owner flagged this as the
                same fact stated twice back to back. The table is the more
                complete of the two (it also carries software/workflow,
                review, and the calculation's own record link per row), so
                it is the one that stays. */}
            <section className="ledger-section" aria-labelledby="calc-ledger">
                <SectionHeading
                    id="calc-ledger"
                    kicker="Machine detail"
                    intro="Rows are listed in the order the archive returned them. No dependency ordering is drawn between rows here — that relationship is only shown when explicit dependency data backs it."
                >
                    Calculation evidence
                </SectionHeading>
                {calculationsAvailability === "populated" ? (
                    <CalculationTable calculations={calculations} observationRef={core.conformer_observation_ref} />
                ) : (
                    <SectionEmptyMessage
                        availability={calculationsAvailability}
                        emptyText="No calculation rows were returned for this observation."
                        contradicted={calculationsAvailability === "empty" && available.has_calculations}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="geometry-ledger">
                <SectionHeading
                    id="geometry-ledger"
                    kicker="Stored coordinates"
                    intro="These are stored geometry objects linked from this observation's calculation output. Their count is not a conformer count and is tracked separately from the calculation-row count above."
                >
                    Geometry records
                </SectionHeading>
                {geometriesAvailability === "populated" ? (
                    <div className="geometry-links">
                        {geometries.map(({ geometry, calculationRefs }) => (
                            <div className="geometry-link" key={geometry.geometry_ref}>
                                <Link to={`/geometries/${geometry.geometry_ref}`}>
                                    {geometry.geometry_ref}
                                </Link>
                                <span>
                                    produced by {calculationRefs.join(", ")}
                                    {geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}
                                </span>
                            </div>
                        ))}
                    </div>
                ) : (
                    <SectionEmptyMessage
                        availability={geometriesAvailability}
                        emptyText="No stored geometry links were returned for this observation."
                        contradicted={geometriesAvailability === "empty" && available.has_geometries}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="sibling-ledger">
                <SectionHeading
                    id="sibling-ledger"
                    kicker="Deposited provenance"
                    intro="Each sibling is an independent deposition; none of them is this observation. Review status is shown only where it differs from this observation's."
                >
                    Sibling observations
                </SectionHeading>
                {observationsAvailability === "populated" && siblings.length > 0 ? (
                    <ul className="observation-list">
                        {siblings.map((sibling) => (
                            <SiblingRow
                                key={sibling.conformer_observation.conformer_observation_ref}
                                sibling={sibling}
                                currentStatus={core.review.status}
                            />
                        ))}
                    </ul>
                ) : (
                    <SectionEmptyMessage
                        availability={observationsAvailability === "not-requested" ? "not-requested" : "empty"}
                        emptyText="No other deposited observations were returned for this basin."
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="review-ledger">
                <SectionHeading
                    id="review-ledger"
                    kicker="Review & trust"
                    // Used to restate the current status here as prose
                    // ("The current status is not reviewed.") -- the hero
                    // badge above already carries it, and the owner
                    // counted this page showing review status in eight
                    // places (hero pill, this sentence, every calculation
                    // row, every sibling pill, and the history table
                    // itself). This intro describes the TABLE now,
                    // without repeating the status value -- and only
                    // renders when a table follows; when there is no
                    // table, the single empty-state line below speaks for
                    // itself.
                    intro={showReviewTable ? "This is the record of how the current status was reached." : undefined}
                >
                    Review history
                </SectionHeading>
                {showReviewTable ? (
                    <div className="table-scroll">
                        <table className="data-table" aria-label={`Review history for ${core.conformer_observation_ref}`}>
                            <thead>
                                <tr>
                                    <th scope="col">Status</th>
                                    <th scope="col">Reviewed at</th>
                                    <th scope="col">Note</th>
                                </tr>
                            </thead>
                            <tbody>
                                {reviewHistory.map((entry, index) => (
                                    <tr key={`review-entry-${index}`}>
                                        <td data-label="Status">{statusLabel(entry.status)}</td>
                                        <td data-label="Reviewed at">{isoDate(entry.reviewed_at)}</td>
                                        <td data-label="Note">{entry.note ?? "not recorded"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    // No table when review history carries no real events --
                    // a one-row table of "not reviewed / not recorded / not
                    // recorded" said nothing the hero badge hadn't already
                    // said. `reviewAvailability` is remapped to "empty" for
                    // the populated-but-eventless case so this renders the
                    // same single line as a genuinely empty response.
                    <SectionEmptyMessage
                        availability={reviewAvailability === "populated" ? "empty" : reviewAvailability}
                        emptyText="No review events are recorded for this observation."
                        contradicted={reviewAvailability === "empty" && available.has_review}
                    />
                )}
            </section>

            {selections.length > 0 && (
                <section className="ledger-section">
                    {/* BLOCKING-3 fix (PR B review): the heading is a plain
                        `SectionHeading` OUTSIDE the `Disclosure` now, never
                        an h2 nested inside its `<summary>` -- see the
                        identical fix (and fuller rationale) on
                        `ConformerGroupPage.tsx`'s `EvidenceDisclosure`. The
                        heading itself still carries the count in its own
                        text (it is a fixed fact about the section, not
                        state that changes with open/closed); `Disclosure`'s
                        own `summary` is separate, plain text. */}
                    {/* Post-review pass (item 4): this was the one section
                        on this page with no kicker while its four
                        siblings all had one (calc-ledger/geometry-ledger/
                        sibling-ledger/review-ledger) -- MEASURED as the
                        same "either every section on a page has a
                        category kicker or none does" inconsistency
                        flagged on `GeometryDetailPage`. "Derived
                        selections" fits for real: this section is the
                        curation system's OWN picks over the deposited
                        observations, not deposited evidence itself (the
                        register `Machine detail`/`Deposited provenance`
                        above cover) -- and it is not a prefix of "Curation
                        selections (N)", so it does not trip the
                        near-restatement guard either. */}
                    <SectionHeading id="curation-selections" kicker="Derived selections" label={`Curation selections (${selections.length})`}>
                        Curation selections ({selections.length})
                    </SectionHeading>
                    <Disclosure
                        id="curation-selections-disclosure"
                        summary={`${selections.length} selection${selections.length === 1 ? "" : "s"}`}
                    >
                        <ul>
                            {selections.map((selection, index) => (
                                <li key={`${selection.selection_kind}-${index}`}>
                                    {selection.selection_kind}
                                    {selection.assignment_scheme ? ` · ${selection.assignment_scheme.name}` : ""}
                                </li>
                            ))}
                        </ul>
                    </Disclosure>
                </section>
            )}
            </PageShell>
        </section>
    )
}

function SiblingRow({ sibling, currentStatus }: { sibling: SiblingObservation; currentStatus: string }) {
    const core = sibling.conformer_observation
    // Review status is shown once, in this record's own hero pill. A
    // sibling only gets its own pill when its status genuinely differs --
    // otherwise this list would repeat the same "not reviewed" the hero
    // already said, once per sibling.
    const statusDiffers = core.review.status !== currentStatus
    return (
        <li>
            <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                {core.conformer_observation_ref}
            </Link>
            {statusDiffers && <span className={reviewPillClass(core.review.status)}>{statusLabel(core.review.status)}</span>}
        </li>
    )
}

function SectionEmptyMessage({ availability, emptyText, contradicted }: {
    availability: SectionAvailability
    emptyText: string
    contradicted?: boolean
}) {
    if (availability === "not-requested") {
        return <p className="empty-projection">This section was not requested for this view.</p>
    }
    return (
        <p className="empty-projection">
            {emptyText}
            {contradicted
                ? " The archive marks this observation as having recorded evidence here; this view did not return it."
                : ""}
        </p>
    )
}

function groupGeometries(links: GeometryLink[]) {
    const byRef = new Map<string, {
        geometry: GeometryLink["geometry"]
        calculationRefs: string[]
    }>()

    for (const { calculation_ref: calculationRef, geometry } of links) {
        const current = byRef.get(geometry.geometry_ref)
        if (current) {
            if (!current.calculationRefs.includes(calculationRef)) current.calculationRefs.push(calculationRef)
        } else {
            byRef.set(geometry.geometry_ref, { geometry, calculationRefs: [calculationRef] })
        }
    }
    return [...byRef.values()]
}

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="card metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

function CalculationTable({ calculations, observationRef }: {
    calculations: CalculationEntry[]
    observationRef: string
}) {
    // No "Review" column here. These calculations are this observation's
    // own; the observation's review status is the hero pill above, shown
    // once. A per-row review column repeated that same value once per
    // calculation (four extra "not reviewed"s on the record that surfaced
    // this) without adding any calculation-specific fact -- calculations
    // are not independently reviewed in this archive today.
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label={`Calculations for ${observationRef}`}>
                <thead>
                    <tr>
                        <th scope="col">Stage</th>
                        <th scope="col">Level of theory</th>
                        <th scope="col">Software / workflow</th>
                        <th scope="col">Record</th>
                    </tr>
                </thead>
                <tbody>
                    {calculations.map((calculation) => (
                        <tr key={calculation.calculation_ref}>
                            <td data-label="Stage">{calculation.type}</td>
                            <td data-label="Level of theory">
                                {calculation.level_of_theory
                                    ? lotLabel(calculation.level_of_theory)
                                    : "not recorded"}
                            </td>
                            <td data-label="Software / workflow">
                                {calculation.software_release?.software ?? "not recorded"}
                                {calculation.workflow_tool_release?.workflow_tool
                                    ? ` · ${calculation.workflow_tool_release.workflow_tool}`
                                    : ""}
                            </td>
                            <td data-label="Record">
                                <Link to={`/calculations/${calculation.calculation_ref}`}>
                                    {calculation.calculation_ref}
                                </Link>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
