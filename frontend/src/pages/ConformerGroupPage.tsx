import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import type { ConformerGroup } from "../api/conformerGroupApi"
import { lotLabel } from "../api/scientificSchemas"
import { Disclosure } from "../components/Disclosure"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { useConformerGroup } from "../hooks/useConformerGroup"

const statusLabel = (status: string) => status.replaceAll("_", " ")
// The one canonical pill rule every record page follows now (item 6,
// design/foundations PR B) -- see `CalculationDetailPage.tsx`'s own copy
// of this helper for the fuller rationale.
const reviewPillClass = (status: string) => (status === "not_reviewed" ? "value-pill value-pill--muted" : "value-pill")
type Observation = NonNullable<ConformerGroup["observations"]>[number]
type GeometryLink = NonNullable<ConformerGroup["geometries"]>[number]

export default function ConformerGroupPage() {
    const { groupRef = "" } = useParams<{ groupRef: string }>()
    const state = useConformerGroup(groupRef)

    if (state.status === "ready") return <Ledger group={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={groupRef}
            kind="conformer basin"
            loadingDetail="Retrieving the conformer basin and its deposited evidence."
        />
    )
}

function Ledger({ group }: { group: ConformerGroup }) {
    const {
        conformer_group: basin,
        species,
        observations_summary: summary,
        evidence_summary: evidence,
    } = group
    const observations = group.observations ?? []
    const geometries = groupGeometries(group.geometries ?? [])

    return (
        <section className="conformer-page">
            {/* Every other record page in this app carries this breadcrumb --
                this was the only one missing it. Species/species-entry links
                come from this endpoint's own `species` context, same source
                the "Species entry" row in the identity block below already
                reads from. */}
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species/${species.species_ref}`}>Species</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species-entries/${species.species_entry_ref}`}>Species entry</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Conformer basin</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        {/* This page's data shape (`species` context: no charge/
                            multiplicity/InChIKey/formula) does not fit
                            `RecordIdentityHeader`'s `RecordIdentity` union, so it
                            renders the SAME kicker-row + h1 + identity `.kv-list`
                            markup that component renders for the other three
                            record pages, by hand (design/foundations PR B, item
                            1) -- `record-identity-header.css`'s
                            `.record-identity-kicker-row`/`.t-display-1` classes,
                            imported directly above rather than relying on load
                            order. Was titled by `basin.label` -- e.g. "conformer_1"
                            -- at this header's old 120px h1 size. That label is an
                            ARC-assigned producer string (the value this
                            component's own comment elsewhere calls out as "not
                            TCKDB semantics"), not a description of what this
                            record IS. The h1 now states what the record is;
                            the producer's own label, when one was deposited,
                            moves into the identity list below as a plain
                            secondary fact next to the stable ref -- same
                            treatment `species_entry_label` gets everywhere
                            else in this app. */}
                        <div className="record-identity-kicker-row">
                            <span className="t-kicker record-identity-kicker">Conformer basin · evidence ledger</span>
                            <span className={reviewPillClass(basin.review.status)}>{statusLabel(basin.review.status)}</span>
                        </div>
                        <h1 className="t-display-1 record-identity-title">Conformer basin</h1>
                        <p className="t-body section-intro">
                            One torsional basin, shown through its deposited observations. Calculation rows
                            are evidence attached to those observations; they are not separate conformers.
                        </p>
                        <dl className="kv-list">
                            {/* Plain text, not `<code>`: `getByText(..., {
                                selector: "dd" })` (this page's own test
                                suite) matches a node's DIRECT text-node
                                children only (`@testing-library/dom`'s
                                `getNodeText`), not text belonging to a
                                nested element -- wrapping this ref in
                                `<code>` would move its text one level
                                deeper and make that query find nothing.
                                `.kv-list dd`'s own font is already the
                                shared `--type-value` step; a bare ref
                                still reads cleanly without the extra
                                markup. */}
                            <div><dt>Group ref</dt><dd>{basin.conformer_group_ref}</dd></div>
                            {basin.label && (
                                <div><dt>Producer label</dt><dd>{basin.label}</dd></div>
                            )}
                            <div>
                                <dt>Species entry</dt>
                                <dd>
                                    <Link to={`/species-entries/${species.species_entry_ref}`}>
                                        {species.species_entry_label ?? species.species_entry_ref}
                                    </Link>
                                </dd>
                            </div>
                            <div><dt>Structure</dt><dd>{species.canonical_smiles ? <code>{species.canonical_smiles}</code> : "not projected"}</dd></div>
                        </dl>
                        {/* Identity-first header, matching the shared record-page
                            order (identity -> facets -> provenance). No facets tier
                            follows: this endpoint's `species` context carries
                            neither `species_entry_kind` nor `electronic_state_kind`
                            to build one from (unlike the species entry surface),
                            and no `submission_ref` provenance tier either -- both
                            omitted rather than fabricated. No `RefsDisclosure`
                            either: every ref on this header is either this
                            record's OWN stable identifier (the group ref, kept
                            visible per that component's own docstring) or
                            already a working link (the species entry) -- there
                            is no secondary machine-ref bundle here worth
                            collapsing behind one.

                            No rigid/basin rotor statement follows either, even
                            though the species-entry conformer picker
                            (`ConformerSelector.tsx`'s `ConformerCard`) shows one
                            for this same basin. That card's statement is built
                            from `conformer_group.fingerprint`, fetched only via
                            `conformers/search?include=fingerprints` -- a
                            different endpoint than this page's own
                            `loadConformerGroup` (`api/conformerGroupApi.ts`),
                            which does not request or type that field today.
                            Surfacing it here needs that fetch, which is outside
                            this change's file scope; flagged rather than
                            guessed at. */}
                    </header>
                )}
            >
            <section className="ledger-summary" aria-label="Basin evidence summary">
                <Metric label="Deposited observations" value={summary.total} />
                <Metric
                    label="Calculation rows"
                    value={evidence.calculation_count}
                    detail={`${evidence.optimization_chain_count} optimisation chains`}
                />
                <Metric label="Distinct stored geometries" value={evidence.geometry_count} />
                {/* `.card--derived` -- a COMPUTED coverage summary across every
                    deposited observation, the same `--derived` rule
                    `CalculationDetailPage`'s evidence checklist follows (item 8
                    in this PR's body: `--derived` marks an aggregate, plain
                    `.card` marks a single record). */}
                <div className="card card--derived coverage-card">
                    <span className="t-label">Observation coverage</span>
                    <strong>
                        opt {evidence.evidence_coverage.opt}/{summary.total} · freq
                        {` ${evidence.evidence_coverage.freq}/${summary.total}`} · sp
                        {` ${evidence.evidence_coverage.sp}/${summary.total}`}
                    </strong>
                    <p className="note">Coverage says which observations have a stage, not whether methods are comparable.</p>
                </div>
            </section>
            <EvidenceDisclosure observations={observations} />
            <section className="ledger-section" aria-labelledby="geometry-ledger">
                <SectionHeading
                    id="geometry-ledger"
                    kicker="Stored coordinates"
                    intro="These are stored geometry objects linked from calculation output. Their count is not a conformer count."
                >
                    Geometry records
                </SectionHeading>
                {geometries.length ? (
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
                    <p className="empty-projection">
                        No stored geometry links were returned for this conformer basin.
                    </p>
                )}
            </section>
            </PageShell>
        </section>
    )
}

/**
 * The observation-scoped evidence ledger, made collapsible. Previously an
 * always-open `<section>` -- with one `ObservationCard` per deposited
 * observation, each carrying its own `CalculationTable`, this was the
 * single largest block on the page and the owner's own "big box"
 * complaint. Composes the shared `Disclosure` primitive
 * (`components/Disclosure.tsx`, design/foundations PR B item 5) rather
 * than a page-local `<details className="ledger-section">` recipe --
 * every disclosure on this app now renders through the one canonical
 * `.disclosure` chrome (box, chevron, summary typography).
 *
 * Default OPEN on this page (a later reviewer pass reversed the original
 * "default closed, matching every other disclosure" call below). This is
 * the SINGLE deposited-evidence section this whole record page has to
 * offer -- unlike `ConformerObservationPage`'s "Curation selections"
 * disclosure, which is one optional extra among several always-open
 * sections, collapsing the only substantive content on a conformer-basin
 * record left visitors looking at a metrics strip and nothing else until
 * they clicked. The metrics strip above still answers "a number"
 * (observation count, calculation-row count, coverage) at a glance; the
 * `<summary>` remains a real toggle a reader can still close.
 *
 * `Disclosure` is deliberately UNCONTROLLED (see its own docstring): once
 * mounted, native `<details>` toggling manages `open` on its own, and the
 * component never fights it with a re-render. `defaultOpen` sets the
 * INITIAL state only. Keyboard operability (Enter/Space toggles a
 * focused `<summary>`) and focus-visible styling both come for free from
 * `<summary>` being a real native interactive element.
 *
 * `SectionHeading` lives INSIDE the `summary` prop, so it stays mounted --
 * and therefore stays registered in the page's table of contents --
 * whether the disclosure is open or closed. Only its own native
 * open/hidden rendering changes; React never unmounts it. That is a
 * deliberate choice, not an accident of reusing the pattern: collapsing
 * this section is a reader's OWN choice to declutter the page, not a
 * decision that the section doesn't exist -- it should stay one click
 * away from the ToC exactly as before, not vanish from it.
 *
 * When there is nothing behind it (zero deposited observations), this
 * renders a plain, always-open `<section>` instead -- the same "nothing
 * to disclose" convention `CalculationDetailPage`'s `LazySection` uses for
 * an unavailable section. Offering a "click to expand" control over an
 * empty result would tell a reader there might be something to find.
 */
function EvidenceDisclosure({ observations }: { observations: Observation[] }) {
    const count = observations.length

    if (count === 0) {
        return (
            <section className="ledger-section" aria-labelledby="observation-ledger">
                <SectionHeading
                    id="observation-ledger"
                    kicker="Deposited provenance"
                    intro="Methods remain on their actual calculation rows so differing levels stay visible."
                >
                    Observation-scoped evidence
                </SectionHeading>
                <p className="empty-projection">
                    No deposited observations were returned for this conformer basin.
                </p>
            </section>
        )
    }

    const summaryLabel = `Observation-scoped evidence (${count} deposited observation${count === 1 ? "" : "s"})`
    return (
        <Disclosure
            id="observation-ledger-disclosure"
            defaultOpen
            summary={<SectionHeading id="observation-ledger" label={summaryLabel}>{summaryLabel}</SectionHeading>}
        >
            <p className="note">Methods remain on their actual calculation rows so differing levels stay visible.</p>
            <div className="observation-list">
                {observations.map((observation) => (
                    <ObservationCard
                        key={observation.conformer_observation.conformer_observation_ref}
                        observation={observation}
                    />
                ))}
            </div>
        </Disclosure>
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

function Metric({ label, value, detail }: { label: string; value: number; detail?: string }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
            {detail && <small>{detail}</small>}
        </div>
    )
}

function ObservationCard({ observation }: { observation: Observation }) {
    const core = observation.conformer_observation
    const calculations = observation.calculations ?? []
    const geometries = observation.geometries ?? []

    return (
        <article className="card observation-card">
            <header>
                <div>
                    <span className="t-kicker">Observation</span>
                    <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                        {core.conformer_observation_ref}
                    </Link>
                </div>
                <div>
                    <span className={reviewPillClass(core.review.status)}>{statusLabel(core.review.status)}</span>
                    <small>{core.scientific_origin ?? "origin not recorded"}</small>
                </div>
            </header>
            {core.note && <p className="observation-note">{core.note}</p>}
            {calculations.length ? (
                <CalculationTable
                    calculations={calculations}
                    observationRef={core.conformer_observation_ref}
                />
            ) : (
                <p className="empty-stage">No calculation rows were returned for this observation.</p>
            )}
            {geometries.length > 0 && (
                <p className="observation-geometries">
                    Geometry output: {geometries.map(({ calculation_ref: calculationRef, geometry }) => (
                        <span key={`${calculationRef}:${geometry.geometry_ref}`}>
                            <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                            {` from ${calculationRef}`}
                        </span>
                    ))}
                </p>
            )}
        </article>
    )
}

function CalculationTable({ calculations, observationRef }: {
    calculations: NonNullable<Observation["calculations"]>
    observationRef: string
}) {
    return (
        <div className="table-scroll observation-card-table-scroll">
            <table className="data-table" aria-label={`Calculations for ${observationRef}`}>
                <thead>
                    <tr>
                        <th scope="col">Stage</th>
                        <th scope="col">Level of theory</th>
                        <th scope="col">Software / workflow</th>
                        <th scope="col">Review</th>
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
                            <td data-label="Review">
                                {calculation.review
                                    ? statusLabel(calculation.review.status)
                                    : "not recorded"}
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
