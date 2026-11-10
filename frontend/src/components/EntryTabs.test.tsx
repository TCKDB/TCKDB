import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryTabs } from "./EntryTabs"

afterEach(() => cleanup())

function tabs(availability: { has_conformers: boolean; has_thermo: boolean; has_statmech: boolean; has_transport: boolean }) {
    return render(
        <MemoryRouter>
            <EntryTabs entryRef="spe_one" activeSection="geometry" conformerQuery="" availability={availability} />
        </MemoryRouter>,
    )
}

describe("EntryTabs", () => {
    it("marks a populated tab with a visible dot, using the entry's own availability flags", () => {
        tabs({ has_conformers: true, has_thermo: true, has_statmech: false, has_transport: false })

        const geometryTab = screen.getByRole("tab", { name: "Geometry" })
        expect(geometryTab.querySelector(".entry-tab-dot")).not.toBeNull()
        expect(geometryTab).toHaveAttribute("data-has-content", "true")

        const spTab = screen.getByRole("tab", { name: "Single-point energy" })
        expect(spTab.querySelector(".entry-tab-dot")).not.toBeNull()

        const thermoTab = screen.getByRole("tab", { name: "Thermochemistry" })
        expect(thermoTab.querySelector(".entry-tab-dot")).not.toBeNull()
    })

    it("leaves an empty tab undotted and says in hidden text that it has no data", () => {
        tabs({ has_conformers: true, has_thermo: true, has_statmech: false, has_transport: false })

        const statmechTab = screen.getByRole("tab", { name: /^Statistical mechanics/ })
        expect(statmechTab).toHaveAccessibleName("Statistical mechanics (no data)")
        expect(statmechTab.querySelector(".entry-tab-dot")).toBeNull()
        expect(statmechTab).toHaveAttribute("data-has-content", "false")

        const transportTab = screen.getByRole("tab", { name: /^Transport/ })
        expect(transportTab.querySelector(".entry-tab-dot")).toBeNull()
        expect(transportTab.querySelector(".molecule-page-visually-hidden")).toHaveTextContent("(no data)")
        expect(transportTab).not.toHaveAttribute("title")
    })

    it("puts aria-controls on the active tab only, since the other panels are not in the page", () => {
        tabs({ has_conformers: true, has_thermo: true, has_statmech: true, has_transport: true })
        expect(screen.getByRole("tab", { name: "Geometry" })).toHaveAttribute("aria-controls", "panel-geometry")
        expect(screen.getByRole("tab", { name: "Transport" })).not.toHaveAttribute("aria-controls")
    })

    it("keys geometry and single-point energy off has_conformers -- neither has its own availability flag", () => {
        tabs({ has_conformers: false, has_thermo: true, has_statmech: true, has_transport: true })

        expect(screen.getByRole("tab", { name: /^Geometry/ })).toHaveAttribute("data-has-content", "false")
        expect(screen.getByRole("tab", { name: /^Single-point energy/ })).toHaveAttribute("data-has-content", "false")
        // Entry-scoped sections stay marked even when there are no conformer
        // basins at all -- they don't depend on `has_conformers`.
        expect(screen.getByRole("tab", { name: "Thermochemistry" })).toHaveAttribute("data-has-content", "true")
        expect(screen.getByRole("tab", { name: "Statistical mechanics" })).toHaveAttribute("data-has-content", "true")
        expect(screen.getByRole("tab", { name: "Transport" })).toHaveAttribute("data-has-content", "true")
    })

    it("keeps the tab reachable by its section label when every tab is empty", () => {
        tabs({ has_conformers: false, has_thermo: false, has_statmech: false, has_transport: false })
        expect(screen.getByRole("tab", { name: /^Geometry/ })).toBeInTheDocument()
        expect(screen.getByRole("tab", { name: /^Transport/ })).toBeInTheDocument()
    })
})
