import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { PathSearchPoint } from "../api/calculationPathsApi"
import { pathSearchCaption, pathSearchProfile, type PathProfile } from "../domain/pathProfile"
import { PathFigure } from "./PathFigure"

afterEach(() => cleanup())

const SUMMARY = {
    method: "neb",
    stored_point_count: 4,
    ts_guess_count: 1,
    climbing_image_count: 1,
    zero_energy_reference_hartree: null,
}

function point(index: number, overrides: Partial<PathSearchPoint> = {}): PathSearchPoint {
    return {
        point_index: index,
        path_coordinate: index * 1.25,
        geometry_ref: `geom_p${index}`,
        geometry_link: { geometry_ref: `geom_p${index}`, natoms: 9 },
        ...overrides,
    }
}

function renderFigure(profile: PathProfile, onSelect?: (index: number) => void, selectedIndex?: number) {
    return render(
        <MemoryRouter>
            <PathFigure profile={profile} label="Energy along the path" caption={pathSearchCaption(profile)} onSelect={onSelect} selectedIndex={selectedIndex} />
        </MemoryRouter>,
    )
}

const EMPTY = pathSearchProfile({
    summary: SUMMARY,
    points: [point(0), point(1), point(2, { is_ts_guess: true }), point(3)],
})

const WITH_ENERGY = pathSearchProfile({
    summary: SUMMARY,
    points: [
        point(0, { relative_energy_kj_mol: 0 }),
        point(1, { relative_energy_kj_mol: 40.5 }),
        point(2, { relative_energy_kj_mol: 90.25, is_ts_guess: true, is_climbing_image: true }),
        point(3, { relative_energy_kj_mol: -12 }),
    ],
})

function markers(container: HTMLElement) {
    return [...container.querySelectorAll<SVGGElement>(".path-figure-point")]
}

function markerCentre(group: SVGGElement): { x: number; y: number } {
    const shape = group.querySelector(".path-figure-marker") as SVGElement
    if (shape.tagName === "circle") return { x: Number(shape.getAttribute("cx")), y: Number(shape.getAttribute("cy")) }
    const match = /^M([\d.]+),([\d.]+)/.exec(shape.getAttribute("d") ?? "") as RegExpExecArray
    return { x: Number(match[1]), y: Number(match[2]) + 7 }
}

describe("PathFigure with no energies", () => {
    it("puts every point on the zero line at its own coordinate and draws no curve", () => {
        const { container } = renderFigure(EMPTY)
        const zero = container.querySelector(".path-figure-zero") as SVGLineElement
        const centres = markers(container).map(markerCentre)
        expect(centres).toHaveLength(4)
        expect(new Set(centres.map((c) => c.y))).toEqual(new Set([Number(zero.getAttribute("y1"))]))
        const xs = centres.map((c) => c.x)
        expect(xs[1] - xs[0]).toBeCloseTo(xs[2] - xs[1], 6)
        expect(container.querySelector(".path-figure-line")).toBeNull()
    })

    it("names the missing energies on the axis and in the caption", () => {
        renderFigure(EMPTY)
        expect(screen.getByText("kJ/mol relative to point 0: not deposited for this path")).toBeInTheDocument()
        expect(screen.getByText(/No energies were deposited for this path/)).toBeInTheDocument()
    })
})

describe("PathFigure markers", () => {
    it("fills only the picked guess and keeps the others hollow", () => {
        const { container } = renderFigure(EMPTY)
        const kinds = markers(container).map((group) => group.querySelector(".path-figure-marker")?.getAttribute("data-marker"))
        expect(kinds).toEqual(["point", "point", "guess", "point"])
        const filled = container.querySelectorAll(".path-figure-marker--filled")
        expect(filled).toHaveLength(1)
        expect(screen.getByText("2 · picked guess")).toBeInTheDocument()
    })

    it("draws a climbing image as a diamond, distinct in shape from a circle", () => {
        const { container } = renderFigure(WITH_ENERGY)
        const guess = markers(container)[2].querySelector(".path-figure-marker") as SVGElement
        expect(guess.tagName).toBe("path")
        expect(guess.getAttribute("data-marker")).toBe("guess-climbing")
        expect(screen.getByText("2 · picked guess, climbing image · +90.3")).toBeInTheDocument()
    })

    it("draws the curve through the energies, highest point highest", () => {
        const { container } = renderFigure(WITH_ENERGY)
        expect(container.querySelectorAll(".path-figure-line")).toHaveLength(1)
        const ys = markers(container).map(markerCentre).map((c) => c.y)
        expect(Math.min(...ys)).toBe(ys[2])
        expect(Math.max(...ys)).toBe(ys[3])
    })
})

describe("PathFigure table", () => {
    it("lists the same points, in the same order, with the same numbers as the picture", () => {
        const { container } = renderFigure(WITH_ENERGY)
        const pictured = markers(container).map((group) => ({
            index: group.getAttribute("data-point-index"),
            name: group.getAttribute("aria-label"),
        }))
        fireEvent.click(screen.getByRole("button", { name: "Table" }))
        expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "true")
        const rows = [...container.querySelectorAll("tbody tr")]
        expect(rows.map((row) => row.getAttribute("data-point-index"))).toEqual(pictured.map((p) => p.index))
        rows.forEach((row, position) => {
            const cells = within(row as HTMLElement).getAllByRole("cell").map((cell) => cell.textContent)
            const name = pictured[position].name ?? ""
            expect(name).toContain(`${cells[1]} along the path`)
            expect(name).toContain(`${cells[3]} kJ/mol`)
        })
        expect(within(rows[2] as HTMLElement).getByText("picked guess, climbing image")).toBeInTheDocument()
        expect(within(rows[0] as HTMLElement).getByRole("link", { name: /point 0/ })).toHaveAttribute("href", "/geometries/geom_p0")
    })

    it("writes 'not deposited' for every missing value, never a zero", () => {
        const { container } = renderFigure(EMPTY)
        fireEvent.click(screen.getByRole("button", { name: "Table" }))
        const first = container.querySelector("tbody tr") as HTMLElement
        const cells = within(first).getAllByRole("cell").map((cell) => cell.textContent)
        expect(cells.slice(2, 5)).toEqual(["not deposited", "not deposited", "not deposited"])
    })
})

describe("PathFigure keyboard and pointer", () => {
    it("has one tab stop and moves along the points with the arrow keys, showing a tooltip", () => {
        const { container } = renderFigure(EMPTY)
        const groups = markers(container)
        expect(groups.filter((group) => group.getAttribute("tabindex") === "0")).toHaveLength(1)
        groups[0].focus()
        fireEvent.keyDown(groups[0], { key: "ArrowRight" })
        expect(document.activeElement).toBe(groups[1])
        expect(container.querySelector(".path-figure-tooltip")?.textContent).toContain("1.250 along the path")
        fireEvent.keyDown(groups[1], { key: "End" })
        expect(document.activeElement).toBe(groups[3])
        expect(container.querySelector(".path-figure-crosshair")).not.toBeNull()
    })

    it("reports a chosen point when a picker is wired", () => {
        const onSelect = vi.fn()
        const { container } = renderFigure(EMPTY, onSelect, 2)
        const groups = markers(container)
        expect(groups[2]).toHaveAttribute("aria-pressed", "true")
        expect(groups[2]).toHaveAttribute("tabindex", "0")
        fireEvent.keyDown(groups[2], { key: "Enter" })
        expect(onSelect).toHaveBeenCalledWith(2)
    })
})
