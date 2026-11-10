import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MolPoster } from "./MolPoster"
import posterCss from "../mol-poster.css?raw"

afterEach(cleanup)

const ATOMS = [
    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
    { atom_index: 2, element: "O", x: 1.4, y: 0, z: 0 },
    { atom_index: 3, element: "H", x: -0.6, y: 0.9, z: 0 },
]

describe("MolPoster", () => {
    it("is one labelled image with fixed width and height and no links", () => {
        const { container } = render(<MolPoster atoms={ATOMS} formula="CH2O" />)
        const image = screen.getByRole("img", { name: "Ball-and-stick picture of CH2O" })
        expect(image).toHaveAttribute("width", "240")
        expect(image).toHaveAttribute("height", "240")
        expect(container.querySelector("a")).toBeNull()
        expect(container.querySelectorAll("circle")).toHaveLength(3)
        expect(container.querySelectorAll("line")).toHaveLength(2)
    })

    it("colours atoms from the atom tokens, not literals", () => {
        for (const token of ["--atom-C", "--atom-H", "--atom-O", "--atom-N", "--atom-other", "--bond"]) {
            expect(posterCss).toContain(`var(${token})`)
        }
    })
})
