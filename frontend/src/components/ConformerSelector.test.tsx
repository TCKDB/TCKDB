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

describe("ConformerSelector ordering", () => {
    // Measured live on spe_mbdqifmaclaakukr7agxbuq3wa: `conformers/search`
    // returned conformer_3, conformer_2, conformer_1 in that order (its own
    // review-rank/recency ranking) -- the page showed "3, 2, 1" until this
    // fix. Cards must read "1, 2, 3" regardless of what order the archive
    // returned them in.
    it("renders conformer cards in ascending numbered order, regardless of the archive's own ranking order", () => {
        const three = conformer({ conformer_group: { conformer_group_ref: "cg_three", label: "conformer_3" } })
        const two = conformer({ conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2" } })
        const one = conformer({ conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" } })
        renderSelector([three, two, one])
        const labels = screen.getAllByText(/^Conformer Group \d+$/).map((el) => el.textContent)
        expect(labels).toEqual(["Conformer Group 1", "Conformer Group 2", "Conformer Group 3"])
    })

    // The classic case a 1/2/3 fixture cannot catch: a lexicographic sort
    // of the LABEL passes 1/2/3 (string order already agrees with numeric
    // order there) and only fails once a two-digit numeral is in the mix
    // ("10" sorts before "2" as a string).
    it("sorts conformer_10 after conformer_9, not between conformer_1 and conformer_2 -- never a string sort", () => {
        const ten = conformer({ conformer_group: { conformer_group_ref: "cg_ten", label: "conformer_10" } })
        const two = conformer({ conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2" } })
        const nine = conformer({ conformer_group: { conformer_group_ref: "cg_nine", label: "conformer_9" } })
        const one = conformer({ conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" } })
        renderSelector([ten, two, nine, one])
        const labels = screen.getAllByText(/^Conformer Group \d+$/).map((el) => el.textContent)
        expect(labels).toEqual(["Conformer Group 1", "Conformer Group 2", "Conformer Group 9", "Conformer Group 10"])
    })
})

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
