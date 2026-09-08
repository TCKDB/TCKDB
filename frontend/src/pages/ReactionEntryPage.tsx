import { useEffect, useState } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
// `record-identity-header.css` -- this page hand-builds its identity
// header markup (see `RecordIdentityHeader`'s own docstring: that shared
// component has no branch for a `reaction_entry` identity, only
// `species_entry`/`transition_state_entry`/`ambiguous`/`absent`) rather
// than composing `RecordIdentityHeader`, so unlike every OTHER import
// below it is not already brought in by a component this page uses --
// hence the direct import here, matching the PR 0 mock's own explicit
// `<link>`. `evidence-checklist.css`/`refs-disclosure.css`/
// `calculation-dependency-graph.css` are each already self-imported by
// the component that needs them (`EvidenceChecklist.tsx`/
// `RefsDisclosure.tsx`/`CalculationDependencyGraph.tsx`) -- see
// `Disclosure.tsx`'s own file-header comment for why importing them again
// here would risk a second, later-loaded copy reordering the cascade.
import "../record-identity-header.css"
import "../reaction-entry.css"
import type { NetworkMembership, ReactionEntrySpeciesParticipant, ReactionFullRecord } from "../api/reactionEntryApi"
import { loadReactionEntry, loadReactionEntryNetworksFallback } from "../api/reactionEntryApi"
import { Formula } from "../components/Formula"
import { ReactionEquation } from "../components/ReactionEquation"
import { ReactionKineticsSection } from "../components/ReactionKineticsSection"
import { ReactionTransitionStatesSection } from "../components/ReactionTransitionStatesSection"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton, RefsDisclosure, type RefEntry } from "../components/RefsDisclosure"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { useScientificRecord } from "../hooks/useScientificRecord"

/**
 * Reaction entry -- the record page for one deposited `rxe_...` (plan §2).
 * `frontend/mocks/` (deleted by this PR, per the plan's own PR 2 slice)
 * held the design-review mocks this page's section order/classes/wording
 * are built from; its README explained the mocks in one paragraph, kept
 * here verbatim rather than lost with the directory:
 *
 * > `reaction-entry.html`, `reaction-overview.html` and
 * > `reaction-entry-network-only.html` were static, hand-written pages
 * > built for design review of `docs/plans/reaction-entry-page.md` before
 * > any product code existed -- every value on them was either the
 * > literal response from a live `/full`/`reactions/search`/
 * > `networks/search` fetch, or explicitly labelled as computed for the
 * > mock where the live API did not yet serve a field. They linked the
 * > real `src/*.css` stylesheets by relative path and copied the real
 * > components' exact markup shapes so they rendered through the
 * > production design system with no styling of their own. See
 * > `docs/plans/reaction-entry-page.md` (§6 "PR 0") for the full mock
 * > brief and the design-review verdict.
 */
export default function ReactionEntryPage() {
    const { entryRef = "" } = useParams<{ entryRef: string }>()
    const state = useScientificRecord(entryRef, loadReactionEntry)

    if (state.status === "ready") return <EntryDetail record={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="reaction entry"
            loadingDetail="Retrieving the deposited reaction entry, its participants, kinetics and transition states."
        />
    )
}

function statusLabel(status: string): string {
    return status.replaceAll("_", " ")
}

type NetworksState =
    | { status: "loading" }
    | { status: "ready"; networks: NetworkMembership[] }
    | { status: "error" }

/**
 * Resolves this entry's network membership: the server's own `networks`
 * section when `/full` served it (§3C, additive), else the documented
 * one-request fallback (`networks/search?reaction_entry_ref=...
 * &include=reactions`) against a pre-deployment API that does not yet
 * recognise the `networks` include token. While the fallback is in
 * flight (or the section was never resolved at all) the Network section
 * renders nothing rather than guess -- see `NetworkSection` below, which
 * reads this hook's tri-state output directly.
 */
function useReactionEntryNetworks(reactionEntryRef: string, served: NetworkMembership[] | null | undefined): NetworksState {
    const [state, setState] = useState<NetworksState>(served !== undefined ? { status: "ready", networks: served ?? [] } : { status: "loading" })

    useEffect(() => {
        if (served !== undefined) {
            setState({ status: "ready", networks: served ?? [] })
            return
        }
        let mounted = true
        const controller = new AbortController()
        loadReactionEntryNetworksFallback(reactionEntryRef, controller.signal)
            .then((networks) => { if (mounted) setState({ status: "ready", networks }) })
            .catch(() => { if (mounted) setState({ status: "error" }) })
        return () => {
            mounted = false
            controller.abort()
        }
        // `served` is a stable reference for the lifetime of one `record`
        // (see `useScientificRecord`'s own cache) -- re-running only when
        // the entry ref itself changes or the served value's presence
        // flips is correct, not a missing-dependency gap.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reactionEntryRef, served === undefined])
    return state
}

function EntryDetail({ record }: { record: ReactionFullRecord }) {
    const entry = record.reaction_entry
    const species = record.species ?? { reactants: [], products: [] }
    const kinetics = record.kinetics ?? []
    const transitionStates = record.transition_states ?? []
    const networksState = useReactionEntryNetworks(entry.reaction_entry_ref, record.networks)

    // Related-refs disclosure: the reaction identity and every TS ref
    // always; the level-of-theory/software/workflow-tool refs from the
    // FIRST kinetics record only (a pragmatic simplification for a
    // multi-kinetics-record entry -- the live archive samples this page
    // was built against never carry more than one kinetics record per
    // entry; see the PR body for the follow-up this leaves open).
    const firstKinetics = kinetics[0]
    const refs: RefEntry[] = [
        { label: "Reaction identity", value: entry.reaction_ref, to: `/reactions/${entry.reaction_ref}` },
        ...transitionStates.map((ts) => ({ label: "Transition state", value: ts.transition_state_ref })),
        ...(firstKinetics?.provenance.primary_level_of_theory?.level_of_theory_ref
            ? [{ label: "Level of theory", value: firstKinetics.provenance.primary_level_of_theory.level_of_theory_ref }]
            : []),
        ...(firstKinetics?.provenance.primary_software?.software_release_ref
            ? [{ label: "Software (opt / freq / sp / irc)", value: firstKinetics.provenance.primary_software.software_release_ref }]
            : []),
        ...(firstKinetics?.provenance.software_release?.software_release_ref
            ? [{ label: "Software (kinetics fit)", value: firstKinetics.provenance.software_release.software_release_ref }]
            : []),
        ...(firstKinetics?.provenance.workflow_tool_release?.workflow_tool_release_ref
            ? [{ label: "Workflow tool", value: firstKinetics.provenance.workflow_tool_release.workflow_tool_release_ref }]
            : []),
    ]

    const hasPathSearch = transitionStates.some((ts) => ts.evidence_summary.has_path_search)
    const hasIrc = transitionStates.some((ts) => ts.evidence_summary.has_irc)

    return (
        <section className="conformer-page tse-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/reactions/${entry.reaction_ref}`}>Reactions</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Reaction entry</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Reaction entry · deposited scientific record</span>
                                <span className={reviewPillClass(entry.review.status)}>{statusLabel(entry.review.status)}</span>
                            </div>
                            <h1 className="t-display-2 record-identity-title">
                                <ReactionEquation reactants={species.reactants} products={species.products} reversible={entry.reversible} />
                            </h1>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Reaction entry ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{entry.reaction_entry_ref}</code>
                                            <CopyButton value={entry.reaction_entry_ref} label="Reaction entry ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Reaction identity</dt>
                                        <dd><Link to={`/reactions/${entry.reaction_ref}`}>{entry.reaction_ref}</Link></dd>
                                    </div>
                                    <div><dt>Family</dt><dd>{entry.family ? statusLabel(entry.family) : <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                    <div><dt>Reversible</dt><dd>{entry.reversible ? "yes" : "no"}</dd></div>
                                    <div>
                                        <dt>Atom map</dt>
                                        <dd>
                                            {entry.atom_maps.length === 0
                                                ? <span className="record-identity-absent-inline">no atom map deposited</span>
                                                : `${entry.atom_maps.length} deposited`}
                                        </dd>
                                    </div>
                                    <div className="record-identity-fact-wide">
                                        <dt>Equation (as deposited)</dt>
                                        <dd><code className="data">{entry.equation}</code></dd>
                                    </div>
                                </dl>
                            </div>
                        </div>
                        <RefsDisclosure refs={refs} />
                    </header>
                )}
            >
                <section className="ledger-section" aria-labelledby="participants-heading">
                    <p className="t-kicker section-kicker">Species on this entry</p>
                    <SectionHeading id="participants-heading">Participants</SectionHeading>
                    <ParticipantsTable label="Reactants" participants={species.reactants} />
                    <ParticipantsTable label="Products" participants={species.products} />
                </section>

                <section className="ledger-section" aria-labelledby="evidence-heading">
                    <SectionHeading id="evidence-heading">Evidence on this entry</SectionHeading>
                    <EvidenceChecklist
                        heading="Evidence on this reaction entry"
                        rows={[
                            {
                                label: "Kinetics records",
                                value: kinetics.length ? `${kinetics.length} deposited` : "none deposited",
                                tone: kinetics.length ? "pill" : "pill-muted",
                                // Only a row asserting PRESENCE ever links --
                                // "none deposited" stays plain text (see
                                // `EvidenceChecklist`'s own docstring: `to`
                                // on a muted row is ignored anyway, but this
                                // page doesn't even offer one).
                                ...(kinetics.length ? { to: "#kinetics-heading" } : {}),
                            },
                            {
                                label: "Transition-state entries",
                                value: transitionStates.length ? `${transitionStates.length} deposited` : "none deposited",
                                tone: transitionStates.length ? "pill" : "pill-muted",
                                ...(transitionStates.length ? { to: "#ts-heading" } : {}),
                            },
                            // Atom map / Path search / IRC evidence have no
                            // section of their own on this page -- per the
                            // rule, a presence-asserting row with nowhere to
                            // point stays plain rather than inventing a
                            // target.
                            { label: "Atom map", value: entry.atom_maps.length ? `${entry.atom_maps.length} deposited` : "none deposited", tone: entry.atom_maps.length ? "pill" : "pill-muted" },
                            { label: "Path search", value: hasPathSearch ? "present" : "none deposited", tone: hasPathSearch ? "pill" : "pill-muted" },
                            { label: "IRC evidence", value: hasIrc ? "present" : "none deposited", tone: hasIrc ? "pill" : "pill-muted" },
                            {
                                label: "Pressure-dependent network membership",
                                value: networksState.status === "ready"
                                    ? (networksState.networks.length ? `${networksState.networks.length} network${networksState.networks.length === 1 ? "" : "s"}` : "none deposited")
                                    : "checking…",
                                tone: networksState.status === "ready" && networksState.networks.length ? "pill" : "pill-muted",
                                ...(networksState.status === "ready" && networksState.networks.length ? { to: "#network-heading" } : {}),
                            },
                        ]}
                        note='Counts are of served arrays only. "None deposited" describes the archive, not the chemistry.'
                    />
                </section>

                <section className="ledger-section" aria-labelledby="kinetics-heading">
                    <p className="t-kicker section-kicker">Deposited rate expressions</p>
                    <SectionHeading id="kinetics-heading">Kinetics</SectionHeading>
                    <ReactionKineticsSection
                        kinetics={kinetics}
                        calculations={record.calculations}
                        transitionStates={transitionStates}
                        networksStatus={networksState.status === "ready" ? (networksState.networks.length ? "populated" : "empty") : "loading"}
                        networkRef={networksState.status === "ready" ? networksState.networks[0]?.network_ref : undefined}
                    />
                </section>

                <section className="ledger-section" aria-labelledby="ts-heading">
                    <p className="t-kicker section-kicker">Saddle points on this reaction</p>
                    <SectionHeading id="ts-heading">Transition states</SectionHeading>
                    <ReactionTransitionStatesSection
                        transitionStates={transitionStates}
                        calculations={record.calculations}
                    />
                </section>

                <section className="ledger-section" aria-labelledby="network-heading">
                    <p className="t-kicker section-kicker">Phenomenological rate context</p>
                    <SectionHeading id="network-heading">Pressure-dependent network</SectionHeading>
                    <NetworkSection state={networksState} />
                </section>

                <section className="ledger-section" aria-labelledby="review-heading">
                    <p className="t-kicker section-kicker">Curation status</p>
                    <SectionHeading id="review-heading">Review</SectionHeading>
                    <EvidenceChecklist
                        heading="Joined-record review counts"
                        // These rows carry no `tone` (they're counts, not a
                        // present/absent checklist), so `EvidenceChecklist`
                        // cannot compute a collapsed-summary roll-up on its
                        // own -- `summary` supplies the TRUE total from the
                        // same `review_summary.total` the "Total joined
                        // records" row itself reads, never the row COUNT
                        // (fixed at 6 category rows regardless of the real
                        // total; see `EvidenceChecklist`'s own docstring for
                        // the defect a bare row count used to cause here).
                        summary={`${record.review_summary.total} joined records`}
                        rows={[
                            { label: "Approved", value: record.review_summary.approved },
                            { label: "Under review", value: record.review_summary.under_review },
                            { label: "Not reviewed", value: record.review_summary.not_reviewed },
                            { label: "Deprecated", value: record.review_summary.deprecated },
                            { label: "Rejected", value: record.review_summary.rejected },
                            { label: "Total joined records", value: record.review_summary.total },
                        ]}
                        note="Counts cover every record joined into this page — the entry, its species participants, kinetics, and transition-state entries. The entry's own review pill is in the header above."
                    />
                </section>
            </PageShell>
        </section>
    )
}

function ParticipantsTable({ label, participants }: {
    label: "Reactants" | "Products"
    participants: ReactionEntrySpeciesParticipant[]
}) {
    return (
        <>
            <h3 className="t-heading-2">{label}</h3>
            <div className="table-scroll">
                <table className="data-table" aria-label={`${label === "Reactants" ? "Reactant" : "Product"} participants`}>
                    <thead>
                        <tr>
                            <th scope="col">Formula</th>
                            <th scope="col">SMILES</th>
                            <th scope="col">Ref</th>
                            <th scope="col">Review</th>
                        </tr>
                    </thead>
                    <tbody>
                        {participants.map((participant) => (
                            <tr key={participant.species_entry_ref}>
                                <td data-label="Formula">
                                    <Link to={`/species-entries/${participant.species_entry_ref}`}>
                                        {participant.formula ? <Formula value={participant.formula} /> : participant.smiles}
                                    </Link>
                                </td>
                                <td data-label="SMILES"><code className="data">{participant.smiles}</code></td>
                                <td data-label="Ref">
                                    <code className="data">{participant.species_entry_ref}</code>
                                    <CopyButton value={participant.species_entry_ref} label="Species entry" srLabel="reference" />
                                </td>
                                <td data-label="Review"><span className={reviewPillClass(participant.review.status)}>{statusLabel(participant.review.status)}</span></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    )
}

function NetworkSection({ state }: { state: NetworksState }) {
    if (state.status === "loading") return null
    if (state.status === "error") {
        return <p className="empty-projection">Could not load pressure-dependent network membership for this entry.</p>
    }
    if (state.networks.length === 0) {
        return <p className="empty-projection">No pressure-dependent network in this archive admits this reaction entry.</p>
    }
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label="Pressure-dependent networks admitting this reaction entry">
                <thead>
                    <tr>
                        <th scope="col">Network</th>
                        <th scope="col">Ref</th>
                        <th scope="col">Solve T range</th>
                        <th scope="col">Solve P range</th>
                        <th scope="col">Channels</th>
                        <th scope="col">Review</th>
                    </tr>
                </thead>
                <tbody>
                    {state.networks.map((network) => (
                        <tr key={network.network_ref}>
                            <td data-label="Network">{network.name ?? "not recorded"}</td>
                            <td data-label="Ref">
                                <code className="data">{network.network_ref}</code>
                                <CopyButton value={network.network_ref} label="Network" srLabel="reference" />
                            </td>
                            <td className="num" data-label="Solve T range">
                                {network.solve_temperature_min_k != null && network.solve_temperature_max_k != null
                                    ? `${network.solve_temperature_min_k}–${network.solve_temperature_max_k} K`
                                    : "not recorded"}
                            </td>
                            <td className="num" data-label="Solve P range">
                                {network.solve_pressure_min_bar != null && network.solve_pressure_max_bar != null
                                    ? `${network.solve_pressure_min_bar}–${network.solve_pressure_max_bar} bar`
                                    : "not recorded"}
                            </td>
                            <td className="num" data-label="Channels">{network.channel_count}</td>
                            <td data-label="Review"><span className={reviewPillClass(network.review.status)}>{statusLabel(network.review.status)}</span></td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
