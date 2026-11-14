import { Link } from "react-router-dom"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import { channelRowId, stateRowId } from "../domain/networkMap"
import { Disclosure } from "./Disclosure"

export function NetworkStatesTable({ states }: { states: NetworkState[] }) {
    return (
        <Disclosure summary="States" count={states.length} defaultOpen>
            <div className="table-scroll">
                <table className="data-table" aria-label="States in this network">
                    <thead>
                        <tr>
                            <th scope="col">State</th>
                            <th scope="col">Kind</th>
                            <th scope="col">Molecules</th>
                        </tr>
                    </thead>
                    <tbody>
                        {states.map((state) => (
                            <tr key={state.composition_hash} id={stateRowId(state.composition_hash)} data-composition-hash={state.composition_hash}>
                                <td data-label="State">{state.composition.state_label || "unnamed state"}</td>
                                <td data-label="Kind"><span className="value-pill">{state.kind}</span></td>
                                <td data-label="Molecules">
                                    <ul className="net-participant-list">
                                        {state.composition.participants.map((participant, index) => (
                                            <li key={`${participant.species_entry_ref}-${index}`}>
                                                {participant.stoichiometry > 1 ? `${participant.stoichiometry}× ` : ""}
                                                <Link to={`/species-entries/${participant.species_entry_ref}`}>
                                                    <code className="data">{participant.canonical_smiles}</code>
                                                </Link>
                                                {" "}
                                                <code className="data">{participant.species_entry_ref}</code>
                                            </li>
                                        ))}
                                    </ul>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Disclosure>
    )
}

export function NetworkChannelsTable({ channels, states }: { channels: NetworkChannel[]; states: NetworkState[] }) {
    const stateLabelByHash = new Map(states.map((state) => [state.composition_hash, state.composition.state_label || "unnamed state"]))

    return (
        <Disclosure summary="Channels" count={channels.length} defaultOpen>
            <div className="table-scroll">
                <table className="data-table" aria-label="Channels in this network">
                    <thead>
                        <tr>
                            <th scope="col">Channel</th>
                            <th scope="col">Kind</th>
                            <th scope="col">Mechanism</th>
                            <th scope="col">From</th>
                            <th scope="col">To</th>
                            <th scope="col">Rate</th>
                        </tr>
                    </thead>
                    <tbody>
                        {channels.map((channel, index) => (
                            <tr key={`${channel.channel_key ?? "unkeyed"}-${index}`} id={channelRowId(index)}>
                                <td data-label="Channel"><code className="data">{index + 1}</code></td>
                                <td data-label="Kind"><span className="value-pill">{channel.kind}</span></td>
                                <td data-label="Mechanism">{channel.mechanism}</td>
                                <td data-label="From">{stateLabelByHash.get(channel.source_state_composition_hash) ?? "unresolved state"}</td>
                                <td data-label="To">{stateLabelByHash.get(channel.sink_state_composition_hash) ?? "unresolved state"}</td>
                                <td data-label="Rate">
                                    {channel.has_kinetics
                                        ? <span className="value-pill">deposited</span>
                                        : <span className="value-pill value-pill--muted">not deposited</span>}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Disclosure>
    )
}
