import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import type { NetworkChannel, NetworkFullRecord, NetworkState } from "../api/networkEntryApi"
import { NetworkKtpHeatmap } from "./NetworkKtpHeatmap"

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const NETWORK = {
    network_ref: "net_a",
    solve_temperature_min_k: 300,
    solve_temperature_max_k: 2000,
    solve_pressure_min_bar: 0.01,
    solve_pressure_max_bar: 100,
    review: { status: "not_reviewed" },
} as unknown as NetworkFullRecord["network"]

function state(hash: string, label: string): NetworkState {
    return {
        composition_hash: hash,
        kind: "well",
        label: null,
        participant_count: 0,
        composition: { participants: [], participant_count_total: 0, participants_truncated: false, state_label: label },
    }
}

const STATES = [state("h1", "AA"), state("h2", "BB")]
const CHANNELS: NetworkChannel[] = [{
    channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
    source_state_composition_hash: "h1", sink_state_composition_hash: "h2", has_kinetics: true, microreactions: [],
}]

function serve(ref: string, modelKind: string, factor: number) {
    server.use(http.post(`/api/v1/scientific/networks/${ref}/kinetics/evaluate`, async ({ request }) => {
        const body = await request.json() as { temperature_k: number[]; pressure_bar: number[] }
        return HttpResponse.json({
            network_ref: ref,
            fits: [{
                network_kinetics_ref: `nk_${ref}`, channel_key: "channel_1", channel_kind: "isomerization",
                source_state_composition_hash: "h1", sink_state_composition_hash: "h2", network_solve_ref: "ns_1",
                model_kind: modelKind, k_units: "per_s", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100,
                points: body.temperature_k.flatMap((t) => body.pressure_bar.map((p) => ({ temperature_k: t, pressure_bar: p, k: factor * t, in_range: true }))),
            }],
        })
    }))
}

function mount(ref: string) {
    return (
        <MemoryRouter>
            <NetworkKtpHeatmap networkRef={ref} network={{ ...NETWORK, network_ref: ref }} channels={CHANNELS} states={STATES} />
        </MemoryRouter>
    )
}

describe("NetworkKtpHeatmap", () => {
    it("moving to another network shows loading, then only that network's records", async () => {
        serve("net_a", "chebyshev", 2)
        serve("net_b", "plog", 3)
        const { rerender } = render(mount("net_a"))
        const select = await screen.findByLabelText("Rate record")
        await userEvent.selectOptions(select, "nk_net_a")
        expect(screen.getByRole("option", { name: "AA to BB, Chebyshev" })).toBeInTheDocument()

        rerender(mount("net_b"))
        expect(screen.getByText("Asking the archive for the stored rate records.")).toBeInTheDocument()
        expect(screen.queryByRole("option", { name: "AA to BB, Chebyshev" })).not.toBeInTheDocument()
        expect(await screen.findByRole("option", { name: "AA to BB, PLOG" })).toBeInTheDocument()
        expect((screen.getByLabelText("Rate record") as HTMLSelectElement).value).toBe("nk_net_b")
    })

    it("PageUp and PageDown move a control by two, and the plus and minus buttons by one", async () => {
        serve("net_a", "chebyshev", 2)
        render(mount("net_a"))
        const pressure = await screen.findByRole("slider", { name: "Pressure" })
        const user = userEvent.setup()
        expect(pressure).toHaveAttribute("aria-valuetext", "1 bar")
        pressure.focus()
        await user.keyboard("{PageUp}")
        expect(pressure).toHaveAttribute("aria-valuetext", "100 bar")
        await user.keyboard("{PageDown}")
        expect(pressure).toHaveAttribute("aria-valuetext", "1 bar")
        await user.click(screen.getByRole("button", { name: "Lower pressure" }))
        expect(pressure).toHaveAttribute("aria-valuetext", "0.1 bar")
        await user.click(screen.getByRole("button", { name: "Raise pressure" }))
        await waitFor(() => expect(pressure).toHaveAttribute("aria-valuetext", "1 bar"))
    })

    it("hides the cell text from assistive technology and points to the table", async () => {
        serve("net_a", "chebyshev", 2)
        const { container } = render(mount("net_a"))
        await screen.findByRole("slider", { name: "Pressure" })
        for (const value of Array.from(container.querySelectorAll(".ktp-value"))) expect(value).toHaveAttribute("aria-hidden", "true")
        expect(container.querySelector(".ktp-caption")!.textContent).toContain("The table has the same values.")
    })
})
