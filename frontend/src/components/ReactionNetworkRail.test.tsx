import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import { ReactionNetworkRail } from "./ReactionNetworkRail"

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function state(hash: string, label: string, smiles: string) {
    return {
        composition_hash: hash,
        kind: "well",
        label: null,
        participant_count: 1,
        composition: {
            participants: [{ species_entry_ref: `spe_${hash}`, species_ref: `sp_${hash}`, canonical_smiles: smiles, stoichiometry: 1 }],
            participant_count_total: 1,
            participants_truncated: false,
            state_label: label,
        },
    }
}

function serveNetwork() {
    server.use(http.get("/api/v1/scientific/networks/net_rail1", () => HttpResponse.json({
        review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
        record: {
            network: { network_ref: "net_rail1", review: { status: "not_reviewed" } },
            evidence_summary: {
                species_count: 2, reaction_count: 1, state_count: 2, channel_count: 1, solve_count: 0,
                kinetics_count: 0, source_calculation_count: 0, has_chebyshev: false, has_plog: false, has_point_kinetics: false,
            },
            states: [state("h1", "NN", "NN"), state("h2", "[NH-][NH3+]", "[NH-][NH3+]")],
            channels: [{
                channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
                source_state_composition_hash: "h2", sink_state_composition_hash: "h1", has_kinetics: false,
                microreactions: [{ reaction_entry_ref: "rxe_here", transition_state_entry_ref: null, path_kind: "saddle_point" }],
            }],
        },
    })))
}

function rail(networkRefs: string[]) {
    return render(
        <MemoryRouter>
            <ReactionNetworkRail networkRefs={networkRefs} reactionEntryRef="rxe_here" />
        </MemoryRouter>,
    )
}

describe("ReactionNetworkRail", () => {
    it("shows a small map of the network, this reaction's channel highlighted, and a link to the network", async () => {
        serveNetwork()
        const { container } = rail(["net_rail1"])
        expect(await screen.findByRole("heading", { name: "Pressure-dependent network" })).toBeInTheDocument()
        await waitFor(() => expect(container.querySelectorAll("svg.net-map-svg").length).toBe(1))
        expect(container.querySelectorAll("path.net-map-edge--here")).toHaveLength(1)
        expect(screen.getByRole("link", { name: "Open the network" })).toHaveAttribute("href", "/networks/net_rail1")
        expect(container.textContent).toContain("1 of 1 channel is this reaction (thick line).")
        expect(container.textContent).toContain("Dashed lines have no rate deposited.")
    })

    it("renders no heading when the network cannot be read", async () => {
        server.use(http.get("/api/v1/scientific/networks/net_rail1", () => HttpResponse.json({}, { status: 500 })))
        const { container } = rail(["net_rail1"])
        await new Promise((resolve) => setTimeout(resolve, 200))
        expect(container.firstChild).toBeNull()
    })

    it("renders nothing when the reaction belongs to no network", () => {
        const { container } = rail([])
        expect(container.firstChild).toBeNull()
    })
})
