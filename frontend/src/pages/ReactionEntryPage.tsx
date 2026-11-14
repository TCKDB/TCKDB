import { useEffect, useMemo, useState } from "react"
import { ParticipantChips } from "../components/ParticipantChips"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../data-table.css"
import "../reaction-entry.css"
import type { NetworkMembership, ReactionEntrySpeciesParticipant, ReactionFullRecord } from "../api/reactionEntryApi"
import { loadReactionEntry, loadReactionEntryNetworksFallback } from "../api/reactionEntryApi"
import { SpeciesFace } from "../components/Formula"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { EnergyProfile } from "../components/EnergyProfile"
import { Lede } from "../components/Lede"
import { RecordGlyph } from "../components/RecordGlyph"
import { ReactionScheme } from "../components/ReactionScheme"
import { RelationshipMap } from "../components/RelationshipMap"
import { ReviewChip } from "../components/ReviewChip"
import { useReactionEntryNeighbours } from "../hooks/useNeighbours"
import { reactionEntryLede } from "../domain/reactionLede"
import { ReactionEquation } from "../components/ReactionEquation"
import { ReactionKineticsSection } from "../components/ReactionKineticsSection"
import { ReactionTransitionStatesSection } from "../components/ReactionTransitionStatesSection"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { PageShell } from "../components/PageShell"
import { ReactionNetworkRail } from "../components/ReactionNetworkRail"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { RecordLink } from "../components/RecordLink"
import { CopyButton, RefsDisclosure, type RefEntry } from "../components/RefsDisclosure"
import { formatQuantity } from "../domain/quantityFormat"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { buildEquationSides, equationNeedsStructure, type EquationParticipant } from "../domain/reactionEquation"
import { participantChips } from "../domain/participantFacets"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { equationText } from "../domain/reactionEquation"

/**
 * Reaction entry: the record page for one deposited `rxe_` entry. Its identity header builds its own markup because
 * `RecordIdentityHeader` has no branch for a reaction entry.
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

function mapParticipant(participant: ReactionEntrySpeciesParticipant) {
    return { entryRef: participant.species_entry_ref, formula: participant.formula, smiles: participant.smiles }
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
    useDocumentTitle("Reaction entry", equationText(species.reactants, species.products, entry.reversible))
    const kinetics = record.kinetics ?? []
    const transitionStates = record.transition_states ?? []
    const networksState = useReactionEntryNetworks(entry.reaction_entry_ref, record.networks)
    const formulaOnly = !equationNeedsStructure(species.reactants, species.products)
    const equation = <ReactionEquation reactants={species.reactants} products={species.products} reversible={entry.reversible} linkParticipants={false} formulaOnly={formulaOnly} />
    const mapReactants = useMemo(() => species.reactants.map(mapParticipant), [species.reactants])
    const mapProducts = useMemo(() => species.products.map(mapParticipant), [species.products])
    const neighbours = useReactionEntryNeighbours({ reactionRef: entry.reaction_ref, reactants: mapReactants, products: mapProducts })
    const speciesLabels = useMemo(
        () => new Map([...species.reactants, ...species.products].map((participant) => [participant.species_entry_ref, participant.formula ?? participant.smiles])),
        [species.reactants, species.products],
    )

    // Related-refs disclosure: the reaction identity, every TS ref, and the level-of-theory / software /
    // workflow-tool refs of EVERY kinetics record, each distinct ref once. An entry can carry several kinetics
    // records (different levels, different fitters); naming only the first one's would present one record's
    // provenance as the entry's own.
    const provenanceRefs: RefEntry[] = []
    const seenProvenance = new Set<string>()
    const addProvenanceRef = (ref: RefEntry) => {
        const key = `${ref.label}|${ref.value}`
        if (seenProvenance.has(key)) return
        seenProvenance.add(key)
        provenanceRefs.push(ref)
    }
    for (const record of kinetics) {
        const lot = record.provenance.primary_level_of_theory?.level_of_theory_ref
        if (lot) addProvenanceRef({ label: "Level of theory", value: lot, to: levelOfTheoryPath(lot) })
        const primarySoftware = record.provenance.primary_software?.software_release_ref
        if (primarySoftware) addProvenanceRef({ label: "Software (opt / freq / sp / irc)", value: primarySoftware })
        const fitSoftware = record.provenance.software_release?.software_release_ref
        if (fitSoftware) addProvenanceRef({ label: "Software (kinetics fit)", value: fitSoftware })
        const tool = record.provenance.workflow_tool_release?.workflow_tool_release_ref
        if (tool) addProvenanceRef({ label: "Workflow tool", value: tool })
    }
    const refs: RefEntry[] = [
        { label: "Reaction identity", value: entry.reaction_ref, to: `/reactions/${entry.reaction_ref}` },
        ...transitionStates.flatMap((ts) => (ts.transition_state_ref ? [{ label: "Transition state", value: ts.transition_state_ref }] : [])),
        ...provenanceRefs,
    ]

    const hasPathSearch = transitionStates.some((ts) => ts.evidence_summary.has_path_search)
    const hasIrc = transitionStates.some((ts) => ts.evidence_summary.has_irc)

    return (
        <section className="conformer-page tse-page">
            <PageShell
                side={<RelationshipMap selfKind="reaction" selfLabel="This entry" state={neighbours}>
                    {networksState.status === "ready" && (
                        <ReactionNetworkRail networkRefs={networksState.networks.map((network) => network.network_ref)} reactionEntryRef={entry.reaction_entry_ref} />
                    )}
                </RelationshipMap>}
                identity={(
                    <>
                        <Breadcrumbs crumbs={[
                            { label: "Reactions", to: BROWSE_KIND_PATHS.reaction },
                            { label: equation, to: `/reactions/${entry.reaction_ref}` },
                            { label: "This deposited version" },
                        ]} />
                        <header className="basin-header">
                            <div className="record-identity-header">
                                <div className="record-identity-kicker-row">
                                    <RecordGlyph kind="reaction" size={16} />
                                    <span className="t-kicker record-identity-kicker">Reaction · one deposited version</span>
                                    <ReviewChip status={entry.review.status} />
                                </div>
                                <h1 className="t-display-2 record-identity-title">
                                    <ReactionEquation reactants={species.reactants} products={species.products} reversible={entry.reversible} formulaOnly={formulaOnly} />
                                </h1>
                                <Lede>{reactionEntryLede(entry, { kinetics: kinetics.length, transitionStates: transitionStates.length })}</Lede>
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
                                            <dt>All versions of this reaction</dt>
                                            <dd><RecordLink kind="reaction" to={`/reactions/${entry.reaction_ref}`} label="All versions" /></dd>
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
                                            <dd><code className="data">{equationWithEntries(entry.equation, entry.reversible, species.reactants, species.products)}</code></dd>
                                        </div>
                                    </dl>
                                </div>
                            </div>
                            <RefsDisclosure refs={refs} />
                        </header>
                    </>
                )}
            >
                <section className="ledger-section" aria-labelledby="participants-heading">
                    <SectionHeading id="participants-heading">Participants</SectionHeading>
                    <ReactionScheme reactants={species.reactants} products={species.products} reversible={entry.reversible} />
                    <ParticipantsTable label="Reactants" participants={species.reactants} />
                    <ParticipantsTable label="Products" participants={species.products} />
                </section>

                <EnergyProfile entryRef={entry.reaction_entry_ref} speciesLabels={speciesLabels} />

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
                    <SectionHeading id="kinetics-heading">Numbers for this entry</SectionHeading>
                    {/* Counts this entry's own rate records; the reaction-level count is not served here. */}
                    {kinetics.length >= 2 && <p><Link className="compare-link" to={`/reactions/${entry.reaction_ref}/compare`}>Compare these values</Link></p>}
                    <ReactionKineticsSection
                        kinetics={kinetics}
                        calculations={record.calculations}
                        transitionStates={transitionStates}
                        networksStatus={networksState.status === "ready" ? (networksState.networks.length ? "populated" : "empty") : "loading"}
                        networkRefs={networksState.status === "ready" ? networksState.networks.map((network) => network.network_ref) : undefined}
                    />
                </section>

                <section className="ledger-section" aria-labelledby="ts-heading">
                    <SectionHeading id="ts-heading">Transition states</SectionHeading>
                    <ReactionTransitionStatesSection
                        transitionStates={transitionStates}
                        calculations={record.calculations}
                    />
                </section>

                <section className="ledger-section" aria-labelledby="network-heading">
                    <SectionHeading id="network-heading">Pressure-dependent network</SectionHeading>
                    <NetworkSection state={networksState} />
                </section>

                <section className="ledger-section" aria-labelledby="review-heading">
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
                        // total; see `EvidenceChecklist`'s own docstring).
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

// One "Species" column: `SpeciesFace` (`../components/
// Formula.tsx`) leads with SMILES and brackets the formula after
// it, so a dedicated SMILES column beside it would print the exact same
// SMILES string twice in one row. The Ref column is separate from the
// identity cell: this table never folds the species-entry ref or
// its copy button into the identity cell.
/**
 * The served `equation` string is the written form and is left as the server wrote it. When the participant rows
 * are loaded this line is rebuilt from them instead, so that each side's entries read apart: a Z to E isomerisation
 * is `CC=CC => CC=CC` in the served string (both participants are entries of one species) and
 * `CC=CC [Z] -> CC=CC [E]` here. Built from the same collapsed sides as the hero equation (one participant per
 * distinct entry, a coefficient only when it is greater than 1), with the server's arrows: `->` irreversible,
 * `<=>` reversible. Falls back to the served string when the participants are not loaded.
 */
function equationWithEntries(
    served: string,
    reversible: boolean,
    reactants: ReactionEntrySpeciesParticipant[],
    products: ReactionEntrySpeciesParticipant[],
): string {
    if (reactants.length === 0 || products.length === 0) return served
    const sides = buildEquationSides(reactants, products)
    const side = (participants: EquationParticipant[]) => participants
        .map((p) => `${p.coefficient > 1 ? `${p.coefficient} ` : ""}${p.smiles}${p.chips.length > 0 ? ` [${p.chips.join(", ")}]` : ""}`)
        .join(" + ")
    return `${side(sides.reactants)} ${reversible ? "<=>" : "->"} ${side(sides.products)}`
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
                            <th scope="col">Species</th>
                            <th scope="col">Review</th>
                        </tr>
                    </thead>
                    <tbody>
                        {participants.map((participant) => (
                            <tr key={participant.species_entry_ref}>
                                <td data-label="Species">
                                    <RecordLink kind="species-entry" to={`/species-entries/${participant.species_entry_ref}`} label={[participant.smiles, participant.formula && `(${participant.formula})`].filter(Boolean).join(" ") || "Species"}>
                                        <SpeciesFace smiles={participant.smiles} formula={participant.formula} />
                                    </RecordLink>
                                    {participantChips(participant).length > 0 && (
                                        <span className="reaction-equation-chip"><ParticipantChips chips={participantChips(participant)} /></span>
                                    )}
                                </td>
                                <td data-label="Review"><ReviewChip status={participant.review.status} explain={false} /></td>
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
                        <th scope="col">Solve T range</th>
                        <th scope="col">Solve P range</th>
                        <th scope="col">Channels</th>
                        <th scope="col">Review</th>
                    </tr>
                </thead>
                <tbody>
                    {state.networks.map((network, index) => (
                        <tr key={network.network_ref}>
                            <td data-label="Network">
                                <RecordLink kind="network" to={`/networks/${network.network_ref}`} label={state.networks.length > 1 ? `Network ${index + 1}` : "Pressure-dependent network"} />
                            </td>
                            <td className="num" data-label="Solve T range">
                                {network.solve_temperature_min_k != null && network.solve_temperature_max_k != null
                                    ? `${formatQuantity("thermo_temperature_k", network.solve_temperature_min_k)?.value}–${formatQuantity("thermo_temperature_k", network.solve_temperature_max_k)?.value} K`
                                    : "not recorded"}
                            </td>
                            <td className="num" data-label="Solve P range">
                                {network.solve_pressure_min_bar != null && network.solve_pressure_max_bar != null
                                    ? `${network.solve_pressure_min_bar}–${network.solve_pressure_max_bar} bar`
                                    : "not recorded"}
                            </td>
                            <td className="num" data-label="Channels">{network.channel_count}</td>
                            <td data-label="Review"><ReviewChip status={network.review.status} explain={false} /></td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
