import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MachineReviewSettingsPanel } from "./MachineReviewSettingsPanel"
import * as fx from "../test/machineReviewFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const SETTINGS = "/api/v1/admin/machine-review/settings"

function serve(initial: Record<string, unknown> = fx.settings()) {
    let current = initial
    const patches: unknown[] = []
    server.use(
        http.get(SETTINGS, () => HttpResponse.json(current)),
        http.patch(SETTINGS, async ({ request }) => {
            const body = (await request.json()) as Record<string, unknown>
            patches.push(body)
            current = { ...current, ...body }
            return HttpResponse.json(current)
        }),
    )
    return patches
}

describe("the machine-review switches", () => {
    it("send only the switch that was changed, and say what was saved", async () => {
        const patches = serve()
        const user = userEvent.setup()
        render(<MachineReviewSettingsPanel />)
        await user.click(await screen.findByRole("checkbox", { name: "Curators may request reviews" }))
        await waitFor(() => expect(patches).toEqual([{ curator_requests_enabled: false }]))
        expect(await screen.findByRole("status", { name: "machine review settings saved" })).toHaveTextContent("Curators may no longer request reviews.")
        expect(screen.getByRole("checkbox", { name: "Curators may request reviews" })).not.toBeChecked()
        await user.click(screen.getByRole("checkbox", { name: "Machine review on" }))
        await waitFor(() => expect(patches).toEqual([{ curator_requests_enabled: false }, { machine_review_enabled: false }]))
    })

    it("disables the curator switch, with the reason, while machine review is off", async () => {
        serve(fx.settings({ machine_review_enabled: false }))
        render(<MachineReviewSettingsPanel />)
        const box = await screen.findByRole("checkbox", { name: "Curators may request reviews" })
        expect(box).toBeDisabled()
        expect(box).toHaveAccessibleDescription("Machine review is off, so this switch has nothing to decide.")
    })

    it("says when the deployment setting overrides a stored switch that is on", async () => {
        serve(fx.settings({ curator_allowed: false }))
        render(<MachineReviewSettingsPanel />)
        expect(await screen.findByText(/the deployment setting overrides it, so curators cannot request reviews/)).toBeInTheDocument()
        cleanup()
        serve(fx.settings({ review_allowed: false, curator_allowed: false, assistant_mode: "off" }))
        render(<MachineReviewSettingsPanel />)
        expect(await screen.findByText(/the deployment setting overrides it, so no review can be requested/)).toBeInTheDocument()
    })

    it("reports a refused save", async () => {
        serve()
        server.use(http.patch(SETTINGS, () => HttpResponse.json({ detail: "no" }, { status: 500 })))
        const user = userEvent.setup()
        render(<MachineReviewSettingsPanel />)
        await user.click(await screen.findByRole("checkbox", { name: "Machine review on" }))
        expect(await screen.findByRole("alert")).toHaveTextContent("The setting could not be saved.")
    })
})
