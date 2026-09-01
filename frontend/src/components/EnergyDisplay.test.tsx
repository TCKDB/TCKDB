import { describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach } from "vitest"
import { EnergyDisplay } from "./EnergyDisplay"

afterEach(cleanup)

describe("EnergyDisplay", () => {
    it("defaults to the stored unit (hartree)", () => {
        render(<EnergyDisplay valueHartree={-76.1234567} />)
        expect(screen.getByTestId("energy-display-value")).toHaveTextContent("-76.123457 hartree")
        expect(screen.getByRole("button", { name: "hartree" })).toHaveAttribute("aria-pressed", "true")
    })

    it("carries the unit for every displayed value -- checked for two different units", () => {
        render(<EnergyDisplay valueHartree={-76.1234567} />)
        fireEvent.click(screen.getByRole("button", { name: "kJ/mol" }))
        expect(screen.getByTestId("energy-display-value")).toHaveTextContent("kJ/mol")
        fireEvent.click(screen.getByRole("button", { name: "eV" }))
        expect(screen.getByTestId("energy-display-value")).toHaveTextContent("eV")
    })

    it("round-trips losslessly: switching away from hartree and back reproduces the exact original string", () => {
        render(<EnergyDisplay valueHartree={-76.1234567} />)
        const original = screen.getByTestId("energy-display-value").textContent
        fireEvent.click(screen.getByRole("button", { name: "cm⁻¹" }))
        expect(screen.getByTestId("energy-display-value").textContent).not.toBe(original)
        fireEvent.click(screen.getByRole("button", { name: "hartree" }))
        expect(screen.getByTestId("energy-display-value").textContent).toBe(original)
    })

    it("states plainly that the non-hartree units are display conversions only", () => {
        render(<EnergyDisplay valueHartree={-76.1234567} />)
        expect(screen.getByText(/display conversions only/)).toBeVisible()
        expect(screen.getByText(/Always stored in hartree/)).toBeVisible()
    })

    it("renders an absent value distinctly, never as a bare toggle over nothing", () => {
        render(<EnergyDisplay valueHartree={null} />)
        expect(screen.getByText("not recorded")).toBeVisible()
        expect(screen.queryByRole("button", { name: "hartree" })).not.toBeInTheDocument()
    })
})
