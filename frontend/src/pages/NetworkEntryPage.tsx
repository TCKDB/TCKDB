import { Link, useParams } from "react-router-dom"
// See `ReactionEntryPage.tsx`'s own import-header comment for why these two
// are imported directly here rather than relied on transitively: this page
// hand-builds its identity-header markup (no `record_entry`-shaped branch
// on the shared `RecordIdentityHeader` component) the same way that page
// does, and `evidence-checklist.css`/`refs-disclosure.css` are each already
// self-imported by the component that needs them.
import "../conformer-group.css"
import "../record-identity-header.css"
import type { NetworkChannel, NetworkFullRecord, ReactionEntryLite } from "../api/networkEntryApi"
import { loadNetworkEntry } from "../api/networkEntryApi"
import { Disclosure } from "../components/Disclosure"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton, RefsDisclosure, type RefEntry } from "../components/RefsDisclosure"
import { computeSpeciesEnergyCoverage } from "../domain/networkEnergyCoverage"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { useScientificRecord } from "../hooks/useScientificRecord"

/**
 * `/networks/:networkRef` -- PR 2 of
 * `docs/plans/pressure-dependent-network-surface.md` (§3: identity,
 * evidence incl. the live-computed energy-coverage fact, reactions,
 * review). No diagram, no k(T,P) chart -- those are PR 3/PR 4; this page
 * leaves their sections out entirely rather than stub them, so `SectionHeading`
 * never registers a heading for content that does not exist yet (a stubbed
 * empty section would show up in the page's own table of contents pointing
 * at nothing). Built to the reviewed design mock,
 * `docs/plans/mocks/network-entry.html` (branch `network-page-mock`).
 */
export default function NetworkEntryPage() {
    const { networkRef = "" } = useParams<{ networkRef: string }>()
    const state = useScientificRecord(networkRef, loadNetworkEntry)

    if (state.status === "ready") return <EntryDetail record={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={networkRef}
            kind="pressure-dependent network"
            loadingDetail="Retrieving the deposited network, its states, channels, solve and reaction membership."
        />
    )
}

function statusLabel(status: string): string {
    return status.replaceAll("_", " ")
}

function formatKjMol(value: number): string {
    return `${value.toFixed(3)} kJ/mol`
}

function softwareText(release: { software: string; version?: string | null } | null): string {
    if (!release) return ""
    return release.version ? `${release.software} ${release.version}` : release.software
}

function EntryDetail({ record }: { record: NetworkFullRecord }) {
    const { network } = record
    const solve = record.solves[0]

    const refs: RefEntry[] = [
        ...(solve ? [{ label: "Network solve", value: solve.network_solve_ref }] : []),
        ...(record.workflow_tool_release?.workflow_tool_release_ref
            ? [{ label: "Workflow tool release", value: record.workflow_tool_release.workflow_tool_release_ref }]
            : []),
    ]

    return (
        <section className="conformer-page tse-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Pressure-dependent network</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Pressure-dependent network · deposited scientific record</span>
                                <span className={reviewPillClass(network.review.status)}>{statusLabel(network.review.status)}</span>
                            </div>
                            {/* Depositor prose, displayed as-is (plan §7's ruling; the
                                mock's own disclosure label below is the "as named by
                                the depositor" qualifier -- no second inline caveat is
                                layered on top of it). */}
                            <h1 className="t-display-2 record-identity-title">{network.name ?? "not named"}</h1>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Network ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{network.network_ref}</code>
                                            <CopyButton value={network.network_ref} label="Network ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Solve temperature range</dt>
                                        <dd>
                                            {network.solve_temperature_min_k != null && network.solve_temperature_max_k != null
                                                ? <code className="data">{`${network.solve_temperature_min_k}–${network.solve_temperature_max_k} K`}</code>
                                                : <span className="record-identity-absent-inline">not recorded</span>}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Solve pressure range</dt>
                                        <dd>
                                            {network.solve_pressure_min_bar != null && network.solve_pressure_max_bar != null
                                                ? <code className="data">{`${network.solve_pressure_min_bar}–${network.solve_pressure_max_bar} bar`}</code>
                                                : <span className="record-identity-absent-inline">not recorded</span>}
                                        </dd>
                                    </div>
                                    <div><dt>ME method</dt><dd>{solve?.me_method ?? <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                    <div><dt>Interpolation model</dt><dd>{solve?.interpolation_model ?? <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                    <div><dt>Software (solve)</dt><dd>{record.software_release ? softwareText(record.software_release) : <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                    <div><dt>Workflow tool</dt><dd>{record.workflow_tool_release ? softwareText({ software: record.workflow_tool_release.workflow_tool, version: record.workflow_tool_release.version }) : <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                    <div><dt>Literature</dt><dd>{record.literature?.title ?? <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                </dl>
                            </div>
                        </div>
                        {network.description && (
                            <Disclosure summary="Description (depositor-authored)">
                                <p className="t-body" style={{ margin: 0 }}>{network.description}</p>
                            </Disclosure>
                        )}
                        <RefsDisclosure refs={refs} />
                    </header>
                )}
            >
                <section className="ledger-section" aria-labelledby="evidence-heading">
                    <p className="t-kicker section-kicker">What this network carries</p>
                    <SectionHeading id="evidence-heading">Evidence on this network</SectionHeading>
                    <EvidenceChecklist
                        heading="Evidence on this network"
                        rows={[
                            { label: "Participant species", value: record.evidence_summary.species_count ? `${record.evidence_summary.species_count} deposited` : "none deposited", tone: record.evidence_summary.species_count ? "pill" : "pill-muted" },
                            { label: "States", value: record.evidence_summary.state_count ? `${record.evidence_summary.state_count} deposited` : "none deposited", tone: record.evidence_summary.state_count ? "pill" : "pill-muted" },
                            { label: "Channels", value: record.evidence_summary.channel_count ? `${record.evidence_summary.channel_count} deposited` : "none deposited", tone: record.evidence_summary.channel_count ? "pill" : "pill-muted" },
                            {
                                label: "Elementary reactions (microreactions)",
                                value: record.evidence_summary.reaction_count ? `${record.evidence_summary.reaction_count} deposited` : "none deposited",
                                tone: record.evidence_summary.reaction_count ? "pill" : "pill-muted",
                                ...(record.evidence_summary.reaction_count ? { to: "#reactions-heading" } : {}),
                            },
                            { label: "Network solves", value: record.evidence_summary.solve_count ? `${record.evidence_summary.solve_count} deposited` : "none deposited", tone: record.evidence_summary.solve_count ? "pill" : "pill-muted" },
                            { label: "Kinetics fits", value: record.evidence_summary.kinetics_count ? `${record.evidence_summary.kinetics_count} deposited` : "none deposited", tone: record.evidence_summary.kinetics_count ? "pill" : "pill-muted" },
                            { label: "Source calculations", value: record.evidence_summary.source_calculation_count ? `${record.evidence_summary.source_calculation_count} deposited` : "none deposited", tone: record.evidence_summary.source_calculation_count ? "pill" : "pill-muted" },
                            { label: "Chebyshev model", value: record.evidence_summary.has_chebyshev ? "present" : "not deposited", tone: record.evidence_summary.has_chebyshev ? "pill" : "pill-muted" },
                            { label: "PLOG model", value: record.evidence_summary.has_plog ? "present" : "not deposited", tone: record.evidence_summary.has_plog ? "pill" : "pill-muted" },
                            { label: "Point kinetics (single-pressure Arrhenius)", value: record.evidence_summary.has_point_kinetics ? "present" : "none deposited", tone: record.evidence_summary.has_point_kinetics ? "pill" : "pill-muted" },
                        ]}
                        note={`Counts reflect what this network's own record carries today, not an assumption about the underlying chemistry. "None deposited" describes the archive, not the chemistry.`}
                    />
                    <EnergyCoverageCard record={record} />
                </section>

                <section className="ledger-section" aria-labelledby="reactions-heading">
                    <p className="t-kicker section-kicker">Elementary steps with a deposited reaction entry</p>
                    <SectionHeading id="reactions-heading">Reactions</SectionHeading>
                    <ReactionsSection channels={record.channels} reactionEntries={record.reactionEntries} totalChannels={record.evidence_summary.channel_count} />
                </section>

                <section className="ledger-section" aria-labelledby="review-heading">
                    <p className="t-kicker section-kicker">Curation status</p>
                    <SectionHeading id="review-heading">Review</SectionHeading>
                    <EvidenceChecklist
                        heading="Network review"
                        summary={`${record.review_summary.total} joined record${record.review_summary.total === 1 ? "" : "s"}`}
                        rows={[
                            { label: "Approved", value: record.review_summary.approved },
                            { label: "Under review", value: record.review_summary.under_review },
                            { label: "Not reviewed", value: record.review_summary.not_reviewed },
                            { label: "Deprecated", value: record.review_summary.deprecated },
                            { label: "Rejected", value: record.review_summary.rejected },
                            { label: "Total", value: record.review_summary.total },
                        ]}
                        note="This count reflects the network's own review record only. States, channels and kinetics fits carry no independent review status of their own today, so there is nothing further to add."
                    />
                </section>
            </PageShell>
        </section>
    )
}

// ---------------------------------------------------------------------------
// Energy coverage
// ---------------------------------------------------------------------------

function speciesThermoSentence(withThermo: number, total: number): string {
    if (total === 0) {
        return "This network's states name no participant species entries to check for thermochemistry."
    }
    if (withThermo === 0) {
        return `No thermochemistry is deposited for the species these states are built from — none of the ${total} participant species entries carry a thermo (H298) record. A thermal free-energy surface needs every participant covered, so it cannot be drawn from species-level data today.`
    }
    if (withThermo === total) {
        return `Every one of the ${total} participant species entries carries a thermo (H298) record.`
    }
    return `${withThermo} of the ${total} participant species entries carry a thermo (H298) record; the remaining ${total - withThermo} do not. A thermal free-energy surface needs every participant covered, so it cannot be drawn from species-level data today.`
}

function solveEnergySentence(meMethod: string | null | undefined, stateCovered: number, stateTotal: number, barrierCovered: number, channelTotal: number): string | null {
    if (stateCovered === 0 && barrierCovered === 0) return null
    const solverLabel = meMethod ?? "master equation"
    return `That is a different claim from "no energies are deposited on this network." This network's own solve separately carries a relative electronic-only energy, referenced to its own lowest state, for ${stateCovered} of ${stateTotal} states, and a forward/reverse electronic barrier for ${barrierCovered} of ${channelTotal} channels — real, deposited data. It is an internal ${solverLabel} solver input, not thermo-grade H298, and mixing the two conventions on one axis would misrepresent the surface — so it is shown below as its own disclosure, not folded into a potential-energy surface.`
}

function EnergyCoverageCard({ record }: { record: NetworkFullRecord }) {
    const speciesCoverage = computeSpeciesEnergyCoverage(record.speciesEntryRefs, record.speciesThermoPresence)
    const stateEnergies = record.stateEnergies ?? []
    const channelBarriers = record.channelBarriers ?? []
    const stateTotal = record.evidence_summary.state_count
    const channelTotal = record.evidence_summary.channel_count
    const solveSentence = solveEnergySentence(record.solves[0]?.me_method, stateEnergies.length, stateTotal, channelBarriers.length, channelTotal)

    const stateLabelByHash = new Map(record.states.map((state) => [state.composition_hash, state.composition.state_label]))
    const sortedStateEnergies = [...stateEnergies].sort((a, b) => a.energy_kj_mol - b.energy_kj_mol)

    return (
        <div className="card" style={{ marginTop: "1.25rem" }} id="energy-coverage">
            <span className="t-label">Energy coverage</span>
            <p className="t-body" style={{ margin: ".6rem 0 0" }}>{speciesThermoSentence(speciesCoverage.withThermo, speciesCoverage.total)}</p>
            {solveSentence && <p className="t-body" style={{ margin: ".6rem 0 0" }}>{solveSentence}</p>}
            {(stateEnergies.length > 0 || channelBarriers.length > 0) && (
                <Disclosure
                    className="disclosure--inset"
                    summary="Solve-internal state & channel energies (electronic-only, referenced to the lowest state)"
                >
                    {stateEnergies.length > 0 && (
                        <>
                            <p className="t-heading-2" style={{ font: "var(--type-heading-2-font)", margin: "0 0 .5rem" }}>{`States — ${stateEnergies.length} of ${stateTotal}`}</p>
                            <div className="table-scroll">
                                <table className="data-table" aria-label="Solve-internal state energies">
                                    <thead><tr><th scope="col">State</th><th scope="col">Relative energy</th><th scope="col">Source calculation</th></tr></thead>
                                    <tbody>
                                        {sortedStateEnergies.map((row) => (
                                            <tr key={row.state_composition_hash}>
                                                <td data-label="State">{stateLabelByHash.get(row.state_composition_hash) || "unresolved state"}</td>
                                                <td className="num" data-label="Relative energy">{formatKjMol(row.energy_kj_mol)}</td>
                                                <td data-label="Source calculation">
                                                    {row.source_calculation_ref
                                                        ? <Link to={`/calculations/${row.source_calculation_ref}`}><code className="data">{row.source_calculation_ref}</code></Link>
                                                        : <span className="record-identity-absent-inline">not recorded</span>}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                    {channelBarriers.length > 0 && (
                        <>
                            <p className="t-heading-2" style={{ font: "var(--type-heading-2-font)", margin: "1.1rem 0 .5rem" }}>{`Channel barriers — ${channelBarriers.length} of ${channelTotal}`}</p>
                            <div className="table-scroll">
                                <table className="data-table" aria-label="Solve-internal channel barriers">
                                    <thead><tr><th scope="col">Channel</th><th scope="col">Forward barrier</th><th scope="col">Reverse barrier</th><th scope="col">Source calculation</th></tr></thead>
                                    <tbody>
                                        {channelBarriers.map((row) => (
                                            <tr key={row.channel_key}>
                                                <td data-label="Channel"><code className="data">{row.channel_key}</code></td>
                                                <td className="num" data-label="Forward barrier">{formatKjMol(row.forward_barrier_kj_mol)}</td>
                                                <td className="num" data-label="Reverse barrier">{formatKjMol(row.reverse_barrier_kj_mol)}</td>
                                                <td data-label="Source calculation">
                                                    {row.source_calculation_ref
                                                        ? <Link to={`/calculations/${row.source_calculation_ref}`}><code className="data">{row.source_calculation_ref}</code></Link>
                                                        : <span className="record-identity-absent-inline">not recorded</span>}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </Disclosure>
            )}
        </div>
    )
}

// ---------------------------------------------------------------------------
// Reactions
// ---------------------------------------------------------------------------

type ReactionRow = {
    channelKey: string | null
    pathKind: string
    transitionStateEntryRef: string | null
    reactionEntryRef: string
    entry: ReactionEntryLite | undefined
}

function buildReactionRows(channels: NetworkChannel[], reactionEntries: Record<string, ReactionEntryLite>): ReactionRow[] {
    return channels.flatMap((channel) => channel.microreactions.map((micro) => ({
        channelKey: channel.channel_key ?? null,
        pathKind: micro.path_kind,
        transitionStateEntryRef: micro.transition_state_entry_ref ?? null,
        reactionEntryRef: micro.reaction_entry_ref,
        entry: reactionEntries[micro.reaction_entry_ref],
    })))
}

function ReactionsSection({ channels, reactionEntries, totalChannels }: {
    channels: NetworkChannel[]
    reactionEntries: Record<string, ReactionEntryLite>
    totalChannels: number
}) {
    const rows = buildReactionRows(channels, reactionEntries)
    const channelsWithReaction = channels.filter((channel) => channel.microreactions.length > 0).length
    const barrierlessCount = rows.filter((row) => row.pathKind === "barrierless").length
    const withoutReaction = totalChannels - channelsWithReaction

    if (rows.length === 0) {
        return <p className="empty-projection">No channel on this network names a deposited elementary reaction.</p>
    }

    return (
        <>
            <p className="t-body section-intro">
                {`${channelsWithReaction} of this network's ${totalChannels} channels carry a directly deposited elementary reaction/transition-state pair — ${barrierlessCount} of those are barrierless, so no transition state. ${withoutReaction > 0 ? `The remaining ${withoutReaction} channels have no elementary reaction or transition state on file — an archive gap, not something this page invents around.` : ""}`}
            </p>
            <div className="table-scroll">
                <table className="data-table" aria-label="Reaction entries admitted to this network">
                    <thead>
                        <tr>
                            <th scope="col">Equation (as deposited)</th>
                            <th scope="col">Channel</th>
                            <th scope="col">Path kind</th>
                            <th scope="col">Transition state</th>
                            <th scope="col">Review</th>
                            <th scope="col">Ref</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={`${row.channelKey ?? "unkeyed"}-${row.reactionEntryRef}`} id={`reaction-row-${row.reactionEntryRef}`}>
                                <td data-label="Equation">
                                    {row.entry ? <code className="data">{row.entry.equation}</code> : <span className="record-identity-absent-inline">not recorded</span>}
                                </td>
                                <td data-label="Channel">
                                    {row.channelKey ? <code className="data">{row.channelKey}</code> : <span className="record-identity-absent-inline">not recorded</span>}
                                </td>
                                <td data-label="Path kind">{statusLabel(row.pathKind)}</td>
                                <td data-label="Transition state">
                                    {row.transitionStateEntryRef
                                        ? <Link to={`/transition-state-entries/${row.transitionStateEntryRef}`}><code className="data">{row.transitionStateEntryRef}</code></Link>
                                        : <span className="record-identity-absent-inline">{row.pathKind === "barrierless" ? "none — barrierless" : "not recorded"}</span>}
                                </td>
                                <td data-label="Review">
                                    {row.entry
                                        ? <span className={reviewPillClass(row.entry.review.status)}>{statusLabel(row.entry.review.status)}</span>
                                        : <span className="record-identity-absent-inline">not recorded</span>}
                                </td>
                                <td data-label="Ref"><Link to={`/reaction-entries/${row.reactionEntryRef}`}><code className="data">{row.reactionEntryRef}</code></Link></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    )
}
