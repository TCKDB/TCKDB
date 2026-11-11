import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { RecordMap } from "./RelationshipMap"
import { geometryNodes } from "../domain/recordMapNodes"

afterEach(cleanup)

describe("RecordMap list text", () => {
    it("says each geometry calculation node once, with its count not repeated", () => {
        const nodes = geometryNodes({
            owner: null,
            producedBy: [{ calculation_ref: "calc_1", calculation_type: "opt" }],
            usedAsInputBy: [{ calculation_ref: "calc_2", calculation_type: "freq" }, { calculation_ref: "calc_3", calculation_type: "sp" }],
            typeLabel: (type) => type,
        })
        const { container } = render(<MemoryRouter><RecordMap selfKind="geometry" selfLabel="This geometry" nodes={nodes} /></MemoryRouter>)
        const items = within(container.querySelector(".rel-map-list") as HTMLElement).getAllByRole("listitem").map((item) => item.textContent?.trim())
        expect(items).toEqual(["Producing calculationProduced this geometry, opt", "Calculations using itTook it as input, 2 found"])
        expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true")
    })
})
