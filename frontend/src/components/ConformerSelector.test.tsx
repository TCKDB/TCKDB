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

// The prompt's own measured fingerprint shapes: the 3-group species' first
// group, and its sibling. Reused below because the "different bins render
// differently" and "matched by key" assertions both need two groups that
// share rotor keys but genuinely differ in the numbers.
const GROUP_1_FINGERPRINT = {
    rotor_count: 2,
    bin_width_deg: 15,
    torsions: [
        { rotor_key: "R_8_10", quantized_bin: 23, raw_torsion_deg: 359.9994, folded_torsion_deg: 359.9994 },
        { rotor_key: "R_9_10", quantized_bin: 3, raw_torsion_deg: 59.8254, folded_torsion_deg: 59.8254 },
    ],
}

const GROUP_2_FINGERPRINT = {
    rotor_count: 2,
    bin_width_deg: 15,
    torsions: [
        { rotor_key: "R_8_10", quantized_bin: 14, raw_torsion_deg: 224.1937, folded_torsion_deg: 224.1937 },
        { rotor_key: "R_9_10", quantized_bin: 4, raw_torsion_deg: 60.4643, folded_torsion_deg: 60.4643 },
    ],
}

describe("ConformerSelector basin identity (item 3)", () => {
    it("renders the group's own basin RANGE (never the bin index) and representative angle, separately labelled, for a single-group entry", () => {
        const single = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: GROUP_1_FINGERPRINT },
        })
        renderSelector([single])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        const rotorRow = within(card).getByText("atoms 8–10").closest(".conformer-basin-rotor") as HTMLElement
        expect(rotorRow).toHaveAttribute("data-rotor-key", "R_8_10")
        // Basin (the definition: a degree RANGE, never the internal bin
        // index) and representative (one member's own measured angle) are
        // two distinct, separately labelled pieces of text -- never merged
        // into one number, and the bin index itself never appears anywhere.
        const basin = within(rotorRow).getByText(/^basin /)
        const representative = within(rotorRow).getByText(/^representative /)
        expect(basin).toHaveTextContent("basin 345–360°")
        expect(basin.textContent).not.toMatch(/bin\s*\d/)
        expect(representative).toHaveTextContent("representative 360°")
        expect(basin).not.toBe(representative)

        // A single-group entry has no sibling to differ from -- positively
        // asserted: no comparison table is rendered at all.
        expect(screen.queryByText("How these basins differ")).not.toBeInTheDocument()
    })

    it("renders two groups' basins with different numbers -- not the same row twice", () => {
        const one = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: GROUP_1_FINGERPRINT },
        })
        const two = conformer({
            conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2", fingerprint: GROUP_2_FINGERPRINT },
        })
        renderSelector([one, two])

        const card1 = screen.getByText("Conformer Group 1", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        const card2 = screen.getByText("Conformer Group 2", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        const rotor1 = within(card1).getByText("atoms 8–10").closest(".conformer-basin-rotor") as HTMLElement
        const rotor2 = within(card2).getByText("atoms 8–10").closest(".conformer-basin-rotor") as HTMLElement
        expect(within(rotor1).getByText(/^basin /)).toHaveTextContent("basin 345–360°")
        expect(within(rotor2).getByText(/^basin /)).toHaveTextContent("basin 210–225°")
        expect(rotor1.textContent).not.toBe(rotor2.textContent)

        // Two groups sharing rotors that differ -- the differences table
        // makes it legible in one place, keyed by group label per column,
        // and never names a bin index either.
        expect(screen.getByText("How these basins differ")).toBeVisible()
        const table = screen.getByRole("table", { name: "Basin differences by rotor" })
        const row = within(table).getByText("atoms 8–10").closest("tr") as HTMLElement
        expect(row).toHaveAttribute("data-rotor-key", "R_8_10")
        const cells = within(row).getAllByRole("cell")
        expect(cells[0]).toHaveTextContent("345–360°")
        expect(cells[1]).toHaveTextContent("210–225°")
        expect(cells[0].textContent).not.toMatch(/bin\s*\d/)
    })

    it("preserves rotor/angle pairing when rotor keys are not in sorted order", () => {
        const shuffled = conformer({
            conformer_group: {
                conformer_group_ref: "cg_one",
                label: "conformer_1",
                fingerprint: {
                    rotor_count: 3,
                    bin_width_deg: 10,
                    torsions: [
                        { rotor_key: "R_9_10", quantized_bin: 7, raw_torsion_deg: 70.5, folded_torsion_deg: 70.5 },
                        { rotor_key: "R_1_2", quantized_bin: 1, raw_torsion_deg: 12.2, folded_torsion_deg: 12.2 },
                        { rotor_key: "R_20_21", quantized_bin: 30, raw_torsion_deg: 305.9, folded_torsion_deg: 305.9 },
                    ],
                },
            },
        })
        renderSelector([shuffled])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        const row9_10 = within(card).getByText("atoms 9–10").closest(".conformer-basin-rotor") as HTMLElement
        const row1_2 = within(card).getByText("atoms 1–2").closest(".conformer-basin-rotor") as HTMLElement
        const row20_21 = within(card).getByText("atoms 20–21").closest(".conformer-basin-rotor") as HTMLElement
        expect(within(row9_10).getByText(/^basin /)).toHaveTextContent("basin 70–80°")
        expect(within(row9_10).getByText(/^representative /)).toHaveTextContent("representative 70.5°")
        expect(within(row1_2).getByText(/^basin /)).toHaveTextContent("basin 10–20°")
        expect(within(row1_2).getByText(/^representative /)).toHaveTextContent("representative 12.2°")
        expect(within(row20_21).getByText(/^basin /)).toHaveTextContent("basin 300–310°")
        expect(within(row20_21).getByText(/^representative /)).toHaveTextContent("representative 305.9°")
    })

    it("calls out folded coordinates on both the basin range and the representative when symmetry folding moved the angle", () => {
        const folded = conformer({
            conformer_group: {
                conformer_group_ref: "cg_one",
                label: "conformer_1",
                fingerprint: {
                    rotor_count: 1,
                    bin_width_deg: 15,
                    torsions: [{ rotor_key: "R_1_2", quantized_bin: 0, raw_torsion_deg: 370.0, folded_torsion_deg: 10.0 }],
                },
            },
        })
        renderSelector([folded])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        const row = within(card).getByText("atoms 1–2").closest(".conformer-basin-rotor") as HTMLElement
        expect(within(row).getByText(/^basin /)).toHaveTextContent("basin 0–15° (folded coordinates)")
        expect(within(row).getByText(/^representative /)).toHaveTextContent("representative 370° (folds to 10°)")
    })

    // The majority case, measured: 37 of 66 groups have no rotors at all.
    // Must render as a POSITIVE statement, not an empty section or a bare
    // dash that reads as missing data.
    it("renders a rigid-conformer statement -- not an empty section -- for a group with a fingerprint but zero rotors", () => {
        const rigid = conformer({
            conformer_group: {
                conformer_group_ref: "cg_one",
                label: "conformer_1",
                fingerprint: { rotor_count: 0, bin_width_deg: 15, torsions: [] },
            },
        })
        renderSelector([rigid])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(card).getByText(/No rotatable bonds recorded/)).toBeVisible()
        expect(card.querySelector(".conformer-basin-identity")).toBeNull()
    })

    // `spe_pv7f7evlv422ab54ackh7m4qnq`: two groups, identical fingerprints
    // (both zero-rotor, per the archive the same fingerprint_hash). The
    // page must not imply a distinction the archive does not record.
    // Chosen treatment: render both cards' own (identical) "no rotatable
    // bonds" statement, and mount no differences table at all -- rather
    // than inventing a "these groups are not distinguished" banner, the
    // absence of a comparison plus the matching text on both cards already
    // tells the honest story without new copy asserting equivalence.
    it("renders no fabricated difference between two groups with identical fingerprints", () => {
        const identicalFingerprint = { rotor_count: 0, bin_width_deg: 15, torsions: [] }
        const one = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: identicalFingerprint },
        })
        const two = conformer({
            conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2", fingerprint: identicalFingerprint },
        })
        renderSelector([one, two])
        expect(screen.getAllByText(/No rotatable bonds recorded/)).toHaveLength(2)
        expect(screen.queryByText("How these basins differ")).not.toBeInTheDocument()
        expect(document.querySelector(".conformer-basin-differences")).toBeNull()
    })

    it("renders nothing extra for a group with no fingerprint on the wire", () => {
        const noFingerprint = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        })
        renderSelector([noFingerprint])
        expect(document.querySelector(".conformer-basin-identity")).toBeNull()
        expect(screen.queryByText(/No rotatable bonds recorded/)).not.toBeInTheDocument()
    })
})
