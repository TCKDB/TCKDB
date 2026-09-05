import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import type { ConformerGroup } from "../api/conformerGroupApi"
import { lotLabel } from "../api/scientificSchemas"
import { Disclosure } from "../components/Disclosure"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { refWithBreaks } from "../domain/refBreaks"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { useConformerGroup } from "../hooks/useConformerGroup"

const statusLabel = (status: string) => status.replaceAll("_", " ")
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
                        {/* SHOULD-FIX-7 (PR B review): wrapped in the SAME
                            `.record-identity-header` div `RecordIdentityHeader`
                            itself renders, not left as bare siblings -- an
                            earlier version of this markup skipped that
                            wrapper, so this header had no `gap` between its
                            kicker row and h1 (`.record-identity-header`'s own
                            `display: grid; gap: var(--s-3)` never applied),
                            reading roughly 1px apart instead of the ~13px
                            gap every other record page's header has. */}
                        <div className="record-identity-header">
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
                                {/* SHOULD-FIX-4 (PR B review): this ref is an
                                    identifier and gets `<code className="data">`
                                    like every other ref on these five pages --
                                    the markup is not shaped around what the
                                    test suite happens to find easiest to query;
                                    the TEST queries the `code` element instead
                                    (an earlier version of this file did the
                                    reverse: kept the ref as plain `dd` text
                                    specifically so `getByText(..., { selector:
                                    "dd" })` kept matching, which is the test
                                    shape driving the markup, backwards). */}
                                <div><dt>Group ref</dt><dd><code className="data">{refWithBreaks(basin.conformer_group_ref)}</code></dd></div>
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
                        </div>
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
 * The observation-scoped evidence ledger, with its own list of
 * observation cards made collapsible. Previously an always-open
 * `<section>` -- with one `ObservationCard` per deposited observation,
 * each carrying its own `CalculationTable`, this was the single largest
 * block on the page and the owner's own "big box" complaint.
 *
 * The `SectionHeading` (an h2) is a plain, always-rendered heading
 * OUTSIDE the `Disclosure` (BLOCKING-3 fix, PR B review) -- an earlier
 * version of this component put the whole heading inside `Disclosure`'s
 * `summary` prop, which put a 28px serif h2 with its own UA margins
 * inside a 13px summary row; the brief's "no heading inside a summary"
 * rule holds for every disclosure on this app, including this one.
 * `Disclosure` wraps only the observation-card LIST, with a plain-text
 * summary (the deposited-observation count) -- collapsing that list is a
 * reader's own choice to declutter the page, not a reason to hide the
 * section's own heading, which now stays visible (and registered in the
 * page's table of contents) regardless of open/closed state, the same as
 * every other section on this page.
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
 * When there is nothing behind it (zero deposited observations), this
 * renders a plain, always-open `<section>` with no `Disclosure` at all --
 * the same "nothing to disclose" convention `CalculationDetailPage`'s
 * `LazySection` uses for an unavailable section. Offering a "click to
 * expand" control over an empty result would tell a reader there might
 * be something to find.
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

    const countLabel = `${count} deposited observation${count === 1 ? "" : "s"}`
    return (
        <section className="ledger-section" aria-labelledby="observation-ledger">
            {/* BLOCKING-3 fix (PR B review): the section heading is a plain
                `SectionHeading` OUTSIDE the disclosure now, never an h2
                nested inside `Disclosure`'s `<summary>` -- a 28px serif
                heading with its own UA margins never belonged inside a
                13px summary row, and the brief's "no headings inside a
                summary" rule holds for every disclosure on these five
                pages, not just the ones that started as a page-local
                `<details>`. `Disclosure`'s own `summary` prop below is
                plain text (the count), not `SectionHeading` -- collapsing
                is the READER'S choice to declutter the observation list,
                not a reason to hide the section's own heading (it stays
                registered in the ToC and visible either way). */}
            <SectionHeading
                id="observation-ledger"
                kicker="Deposited provenance"
                intro="Methods remain on their actual calculation rows so differing levels stay visible."
                label={`Observation-scoped evidence (${countLabel})`}
            >
                Observation-scoped evidence
            </SectionHeading>
            <Disclosure id="observation-ledger-disclosure" defaultOpen summary={countLabel}>
                <div className="observation-list">
                    {observations.map((observation) => (
                        <ObservationCard
                            key={observation.conformer_observation.conformer_observation_ref}
                            observation={observation}
                        />
                    ))}
                </div>
            </Disclosure>
        </section>
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
        <div className="card metric">
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
                    {/* SHOULD-FIX-12 ("record-page residuals" re-review):
                        was `.t-kicker` -- same mono/uppercase shape as
                        every other field label on this page, but `.t-
                        kicker` carries no colour rule of its own, so this
                        one inherited plain `--ink` instead of the `--muted`
                        every OTHER label uses (`.kv-list dt`, `.metric
                        span`, ...), MEASURED as the one outlier. `.t-label`
                        is the same face/size/tracking; the colour comes
                        from the scoped rule below. */}
                    <span className="t-label">Observation</span>
                    <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                        {refWithBreaks(core.conformer_observation_ref)}
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
