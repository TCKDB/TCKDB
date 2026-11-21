import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { pathSearchProfile } from "../domain/pathProfile"
import { PathSparkline } from "./PathSearchCard"

afterEach(() => cleanup())

const summary = { method: "neb", stored_point_count: 3, ts_guess_count: 1, climbing_image_count: 0 }

describe("PathSparkline", () => {
    it("draws energies as a line with the guess filled", () => {
        const profile = pathSearchProfile({
            summary,
            points: [
                { point_index: 0, path_coordinate: 0, relative_energy_kj_mol: 0 },
                { point_index: 1, path_coordinate: 1, relative_energy_kj_mol: 80, is_ts_guess: true },
                { point_index: 2, path_coordinate: 2, relative_energy_kj_mol: -20 },
            ],
        })
        const { container } = render(<PathSparkline profile={profile} />)
        expect(screen.getByRole("img", { name: "3 points along the path; highest +80.0 kJ/mol." })).toBeInTheDocument()
        expect(container.querySelector(".path-sparkline-line")).not.toBeNull()
        expect(container.querySelector(".path-sparkline-rule")).toBeNull()
        const circles = [...container.querySelectorAll("circle")]
        expect(circles.map((circle) => circle.classList.contains("path-sparkline-point--marked"))).toEqual([false, true, false])
        expect(Number(circles[1].getAttribute("cy"))).toBeLessThan(Number(circles[0].getAttribute("cy")))
    })

    it("puts every point on one rule when no energy was deposited", () => {
        const profile = pathSearchProfile({ summary, points: [0, 1, 2].map((index) => ({ point_index: index, path_coordinate: index })) })
        const { container } = render(<PathSparkline profile={profile} />)
        expect(container.querySelector(".path-sparkline-line")).toBeNull()
        expect(container.querySelector(".path-sparkline-rule")).not.toBeNull()
        expect(new Set([...container.querySelectorAll("circle")].map((circle) => circle.getAttribute("cy"))).size).toBe(1)
    })
})
