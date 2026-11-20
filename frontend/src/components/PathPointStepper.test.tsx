import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { useState } from "react"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { initialPointIndex, pathSearchProfile } from "../domain/pathProfile"
import { PathPointStepper } from "./PathPointStepper"

const server = setupServer(
    http.get("/api/v1/scientific/geometries/:ref", ({ params }) => HttpResponse.json({
        geometry_ref: params.ref,
        natoms: 2,
        geom_hash: "h",
        format: "xyz",
        coordinate_units: "angstrom",
        created_at: "2026-11-09T00:00:00",
        atoms: [
            { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
            { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
        ],
    })),
)
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => cleanup())
afterAll(() => server.close())

const profile = pathSearchProfile({
    summary: { method: "gsm", stored_point_count: 4, ts_guess_count: 1, climbing_image_count: 0 },
    points: [0, 1, 2, 3].map((index) => ({
        point_index: index,
        path_coordinate: index * 1.5,
        is_ts_guess: index === 2,
        geometry_ref: `geom_${index}`,
        geometry_link: { geometry_ref: `geom_${index}`, natoms: 2 },
    })),
})

function Harness() {
    const [selected, setSelected] = useState(initialPointIndex(profile) ?? 0)
    return (
        <MemoryRouter>
            <p data-testid="selected">{selected}</p>
            <PathPointStepper profile={profile} selectedIndex={selected} onSelect={setSelected} label="path search" />
        </MemoryRouter>
    )
}

const selected = () => screen.getByTestId("selected").textContent

describe("PathPointStepper", () => {
    it("opens on the picked guess, with its facts and its geometry", async () => {
        render(<Harness />)
        expect(selected()).toBe("2")
        expect(screen.getByRole("slider")).toHaveValue("2")
        expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", "Point 2 (picked guess): 3.000 along the path, energy not deposited")
        expect(screen.getByText("2 · 3 of 4 in path order")).toBeInTheDocument()
        expect(await screen.findByRole("img", { name: "Ball-and-stick picture of path search, point 2" })).toBeInTheDocument()
    })

    it("steps to the previous and next point and stops at the ends", () => {
        render(<Harness />)
        fireEvent.click(screen.getByRole("button", { name: "Point 3 ›" }))
        expect(selected()).toBe("3")
        expect(screen.getByRole("button", { name: "Last point ›" })).toBeDisabled()
        fireEvent.click(screen.getByRole("button", { name: "‹ Point 2" }))
        fireEvent.click(screen.getByRole("button", { name: "‹ Point 1" }))
        fireEvent.click(screen.getByRole("button", { name: "‹ Point 0" }))
        expect(selected()).toBe("0")
        expect(screen.getByRole("button", { name: "‹ First point" })).toBeDisabled()
    })

    it("moves with the slider", () => {
        render(<Harness />)
        fireEvent.change(screen.getByRole("slider"), { target: { value: "1" } })
        expect(selected()).toBe("1")
        expect(screen.getByText("1 · 2 of 4 in path order")).toBeInTheDocument()
    })

    it("shows a lone point without a strip or a stepper", () => {
        const lone = pathSearchProfile({
            summary: { method: "neb", stored_point_count: 1, ts_guess_count: 1, climbing_image_count: 0 },
            points: [{ point_index: 0, is_ts_guess: true, geometry_ref: "geom_0", geometry_link: { geometry_ref: "geom_0", natoms: 2 } }],
        })
        render(<MemoryRouter><PathPointStepper profile={lone} selectedIndex={0} onSelect={() => {}} label="path search" /></MemoryRouter>)
        expect(screen.queryByRole("slider")).toBeNull()
        expect(screen.queryByRole("list", { name: "Points of the path" })).toBeNull()
        expect(screen.getByText("0, the only stored point")).toBeInTheDocument()
    })

    it("selects from the strip and marks the selected card and the guess", () => {
        render(<Harness />)
        const strip = screen.getByRole("list", { name: "Points of the path" })
        const cards = within(strip).getAllByRole("button")
        expect(cards.map((card) => card.getAttribute("aria-pressed"))).toEqual(["false", "false", "true", "false"])
        expect(within(strip).getByText("2 · guess")).toBeInTheDocument()
        fireEvent.click(cards[0])
        expect(selected()).toBe("0")
        expect(cards[0]).toHaveAttribute("aria-pressed", "true")
    })
})
