import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { ConformerSelector } from "./ConformerSelector"

afterEach(cleanup)

// `ConformerSelector` renders `RefsDisclosure`, which links to
// `/conformer-groups/:ref` -- needs a router context to render at all.
function renderSelector(conformers: ConformerProjection[]) {
    return render(
        <MemoryRouter>
            <ConformerSelector conformers={conformers} selectedRef={null} onSelect={() => {}} />
        </MemoryRouter>,
    )
}

function conformer(overrides: Partial<ConformerProjection> = {}): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1,
            optimization_chain_count: 1,
            geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 0, sp: 0 },
            levels_of_theory: {},
        },
        observations: [],
        calculations: [{ calculation_ref: "c1", type: "opt" }],
        geometries: [],
        ...overrides,
    } as ConformerProjection
}

describe("ConformerSelector card", () => {
    it("uses singular wording for exactly ONE calculation row -- not '1 calculation rows'", () => {
        renderSelector([conformer()])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(card).getByText("1 observation · 1 calculation row (1 opt)")).toBeVisible()
        expect(within(card).queryByText(/1 calculation rows\b/)).not.toBeInTheDocument()
    })

    it("uses plural wording for more than one calculation row", () => {
        const many = conformer({
            evidence_summary: {
                calculation_count: 2, optimization_chain_count: 1, geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 1, sp: 0 }, levels_of_theory: {},
            },
            calculations: [{ calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "freq" }],
        })
        renderSelector([many])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(card).getByText("1 observation · 2 calculation rows (1 opt · 1 freq)")).toBeVisible()
    })
})
