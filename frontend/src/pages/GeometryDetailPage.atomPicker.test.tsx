import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"

// A fake 3Dmol: enough for the viewer to reach "ready"; picking is done through the keyboard form.
vi.mock("3dmol", () => ({
    createViewer: (...args: unknown[]) => {
        const container = args[0] as HTMLElement
        container.appendChild(document.createElement("canvas"))
        return {
            addModel: () => {}, setStyle: () => {}, zoomTo: () => {}, render: () => {}, clear: () => {},
            rotate: () => {}, zoom: () => {}, getView: () => [0, 0, 0, 1, 0, 0, 0, 1], setView: () => {},
            addLabel: () => ({}), removeAllLabels: () => {}, removeLabel: () => {}, setClickable: () => {},
            addSphere: () => ({}), addCylinder: () => ({}), removeShape: () => {},
        }
    },
}))

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 320 })
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 320 })
})
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const OH = {
    geometry_ref: "geom_oh",
    natoms: 2,
    geom_hash: "ab12cd34".repeat(8),
    format: "cartesian",
    coordinate_units: "angstrom",
    symbols: ["O", "H"],
    coords: [[0, 0, 0], [0, 0, 0.98]],
    atoms: [{ atom_index: 1, element: "O", x: 0, y: 0, z: 0 }, { atom_index: 2, element: "H", x: 0, y: 0, z: 0.98 }],
    xyz_text: "2\n\nO 0.000000 0.000000 0.000000\nH 0.000000 0.000000 0.980000",
    created_at: "2026-07-21T12:06:50.748258",
    provenance: {},
}

describe("GeometryDetailPage: keyboard atom picker", () => {
    it("opens the 3D view and measures a distance with the keyboard alone, in the page's unit", async () => {
        const user = userEvent.setup()
        server.use(http.get("/api/v1/scientific/geometries/geom_oh", () => HttpResponse.json(OH)))
        render(
            <MemoryRouter initialEntries={["/geometries/geom_oh"]}>
                <Routes><Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} /></Routes>
            </MemoryRouter>,
        )
        await screen.findByRole("heading", { level: 1, name: /^Geometry of/ })

        const tabTo = async (matches: (element: Element | null) => boolean) => {
            for (let presses = 0; presses < 60 && !matches(document.activeElement); presses += 1) await user.tab()
            expect(matches(document.activeElement)).toBe(true)
        }
        const labelled = (text: string) => (element: Element | null) => (
            element instanceof HTMLSelectElement && element.labels?.[0]?.textContent?.startsWith(text) === true
        )

        await tabTo((element) => element === screen.getByRole("button", { name: "Show in 3D" }))
        await user.keyboard("{Enter}")
        const form = await screen.findByRole("group", { name: "Measure with the keyboard" })

        await tabTo(labelled("Atom 1"))
        await user.selectOptions(document.activeElement as HTMLSelectElement, "0")
        await user.tab()
        expect(labelled("Atom 2")(document.activeElement)).toBe(true)
        await user.selectOptions(document.activeElement as HTMLSelectElement, "1")

        await tabTo((element) => element === within(form).getByRole("button", { name: "Measure" }))
        await user.keyboard("{Enter}")

        await waitFor(() => expect(document.querySelector(".measurement-list")).not.toBeNull())
        const list = document.querySelector(".measurement-list") as HTMLElement
        expect(within(list).getByText(/Distance O1–H2/).parentElement).toHaveTextContent("0.9800 Å")

        // The coordinate table's unit toggle drives the measurement too.
        await user.click(screen.getByRole("button", { name: "bohr" }))
        await waitFor(() => expect(within(document.querySelector(".measurement-list") as HTMLElement).getByText(/Distance O1–H2/).parentElement).toHaveTextContent(/1\.8519\d* bohr/))
    })
})
