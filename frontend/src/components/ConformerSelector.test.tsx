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
    it("uses singular wording for exactly ONE calc -- not '1 calcs'", () => {
        renderSelector([conformer()])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(card).getByText("1 obs · 1 calc (1 opt)")).toBeVisible()
        expect(within(card).queryByText(/1 calcs\b/)).not.toBeInTheDocument()
    })

    it("uses plural wording for more than one calc", () => {
        const many = conformer({
            evidence_summary: {
                calculation_count: 2, optimization_chain_count: 1, geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 1, sp: 0 }, levels_of_theory: {},
            },
            calculations: [{ calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "freq" }],
        })
        renderSelector([many])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(card).getByText("1 obs · 2 calcs (1 opt · 1 freq)")).toBeVisible()
    })

    // `species-entry.css`'s `.conformer-list` column floor (34rem) is
    // sized to fit this EXACT string on one line, unclipped -- the
    // longest real meta line measured in the live archive, 69 characters
    // ("4 obs · 16 calcs (4 opt · 4 freq · 4 sp · 4
    // scan)"). A previous pass clipped this with an ellipsis at a
    // narrower column width; the owner rejected that ("do not do
    // ellipsis for texts that go longer than the boxes. the boxes need
    // to be longer in width") and the fix widened the column instead --
    // see `species-entry.css.test.ts` for the CSS-level assertions that
    // `text-overflow: ellipsis` is gone. This test pins the DOM content
    // itself: the full string renders as the visible text (not a
    // JS-side truncation), and the same string also backs the `title`
    // tooltip as a redundant, never-drifting affordance.
    it("renders the full meta line as visible text, never truncated, with the identical string on the title tooltip", () => {
        const many = conformer({
            evidence_summary: {
                calculation_count: 16, optimization_chain_count: 4, geometry_count: 4,
                evidence_coverage: { opt: 4, freq: 4, sp: 4 }, levels_of_theory: {},
            },
            observations_summary: { total: 4 },
            calculations: [
                { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" },
                { calculation_ref: "c3", type: "opt" }, { calculation_ref: "c4", type: "opt" },
                { calculation_ref: "c5", type: "freq" }, { calculation_ref: "c6", type: "freq" },
                { calculation_ref: "c7", type: "freq" }, { calculation_ref: "c8", type: "freq" },
                { calculation_ref: "c9", type: "sp" }, { calculation_ref: "c10", type: "sp" },
                { calculation_ref: "c11", type: "sp" }, { calculation_ref: "c12", type: "sp" },
                { calculation_ref: "c13", type: "scan" }, { calculation_ref: "c14", type: "scan" },
                { calculation_ref: "c15", type: "scan" }, { calculation_ref: "c16", type: "scan" },
            ],
        })
        renderSelector([many])
        const card = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        const meta = within(card).getByText("4 obs · 16 calcs (4 opt · 4 freq · 4 sp · 4 scan)")
        expect(meta).toHaveAttribute("title", "4 obs · 16 calcs (4 opt · 4 freq · 4 sp · 4 scan)")
        const coverage = within(card).getByText("opt 4/4 obs · freq 4/4 obs · sp 4/4 obs")
        expect(coverage).toHaveAttribute("title", "opt 4/4 obs · freq 4/4 obs · sp 4/4 obs")
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
    // ROUND-TRIP ANCHOR: `{ rotor_count: 0, bin_width_deg: 15, torsions: [] }`
    // is not an invented shape -- it is the LITERAL fingerprint object the
    // fixed backend endpoint now serves for a real zero-rotor group
    // (`backend/tests/api/scientific/test_api_scientific_conformers.py::
    // test_cg_detail_include_fingerprints_zero_rotor_group_serves_object_not_null`
    // asserts the exact same JSON off a real HTTP response). Before that
    // backend fix (`_build_group_fingerprint`,
    // `backend/app/services/scientific_read/conformers.py`), this endpoint
    // answered `fingerprint: null` for every one of the archive's 37 (of
    // 66 measured) rigid groups -- indistinguishable on the wire from a
    // group that never had a fingerprint computed at all -- so this
    // component's "no rotatable bonds" branch, though correctly written
    // and covered right here, could never fire against real production
    // data. A true single cross-language test isn't practical (separate
    // pytest/vitest runners, no shared fixture file) -- this comment plus
    // the identical literal object on both ends is the closest available
    // substitute, and it is what would go stale first if either side's
    // shape ever drifted.
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

    it("each card shows its own (identical) 'no rotatable bonds' statement when two groups share a fingerprint", () => {
        const identicalFingerprint = { rotor_count: 0, bin_width_deg: 15, torsions: [] }
        const one = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: identicalFingerprint },
        })
        const two = conformer({
            conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2", fingerprint: identicalFingerprint },
        })
        renderSelector([one, two])
        expect(screen.getAllByText(/No rotatable bonds recorded/)).toHaveLength(2)
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

// Item 5 of the design brief: "How these basins differ" (the cross-group
// comparison table, `ConformerBasinDifferences` in a previous revision of
// `ConformerSelector.tsx`) is gone -- redundant once each card shows its
// own basin, per the owner. Asserted on a fixture that would have MOUNTED
// the removed table under the old code (two groups sharing a rotor that
// lands in different bins) so this can only pass because the feature is
// actually gone, not because nothing would have rendered it anyway. The
// second half asserts POSITIVELY what each card still shows -- a test that
// only checked absence could pass equally well if the whole card failed
// to render.
describe("the basin differences comparison is removed (item 5)", () => {
    it("no 'How these basins differ' text or .conformer-basin-differences element, even when two groups' basins genuinely differ", () => {
        const one = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: GROUP_1_FINGERPRINT },
        })
        const two = conformer({
            conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2", fingerprint: GROUP_2_FINGERPRINT },
        })
        renderSelector([one, two])

        expect(screen.queryByText("How these basins differ")).not.toBeInTheDocument()
        expect(screen.queryByRole("table", { name: "Basin differences by rotor" })).not.toBeInTheDocument()
        expect(document.querySelector(".conformer-basin-differences")).toBeNull()

        // Positive: each card still shows its OWN basin range, unaffected
        // by the removal -- this is the per-card display the owner said
        // made the comparison table redundant.
        const card1 = screen.getByText("Conformer Group 1", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        const card2 = screen.getByText("Conformer Group 2", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        const rotor1 = within(card1).getByText("atoms 8–10").closest(".conformer-basin-rotor") as HTMLElement
        const rotor2 = within(card2).getByText("atoms 8–10").closest(".conformer-basin-rotor") as HTMLElement
        expect(within(rotor1).getByText(/^basin /)).toHaveTextContent("basin 345–360°")
        expect(within(rotor2).getByText(/^basin /)).toHaveTextContent("basin 210–225°")
    })
})

// `species-entry.css`'s `@supports (grid-template-rows: subgrid)` block
// pins `.conformer-card > .refs-disclosure` to an EXPLICIT row line
// (`grid-row: 5`), specifically so a card that renders less content above
// it -- a 1-rotor basin box instead of a 7-rotor one, or (the archive's
// own majority case) no basin element at all -- still lines its
// references toggle up with every sibling card's. That CSS only works if
// `.refs-disclosure` really is a DIRECT child of `.conformer-card`, in a
// stable structural position, regardless of what rendered above it --
// jsdom cannot lay the page out to show the pixels lining up (see
// `species-entry.css.test.ts` for what CAN be checked about the CSS
// itself without a browser), but it CAN show that the DOM the CSS acts on
// stays uniform across 1 rotor, 7 rotors, and 0.
describe("conformer card DOM structure feeds the CSS row-track pinning (design/conformer-card-alignment)", () => {
    function rotorFingerprint(count: number) {
        return {
            rotor_count: count,
            bin_width_deg: 15,
            torsions: Array.from({ length: count }, (_, i) => ({
                rotor_key: `R_${i + 1}_${i + 2}`,
                quantized_bin: i,
                raw_torsion_deg: i * 10,
                folded_torsion_deg: i * 10,
            })),
        }
    }

    it("a 1-rotor card and a 7-rotor card both place References as .conformer-card's 3rd direct child, right after the basin element -- the shape a same-content fixture could never catch", () => {
        const one = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: rotorFingerprint(1) },
        })
        const seven = conformer({
            conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2", fingerprint: rotorFingerprint(7) },
        })
        renderSelector([one, seven])

        for (const label of ["Conformer Group 1", "Conformer Group 2"]) {
            const card = screen.getByText(label, { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
            // Exactly 3 direct children regardless of rotor count: the
            // select button, the basin identity box, and the references
            // disclosure -- the same shape the CSS's fixed 5-row subgrid
            // (button spans rows 1-3, basin is row 4, references is row 5)
            // relies on to keep every card's references toggle on the same
            // line, whether the basin box above it holds 1 rotor or 7.
            expect(Array.from(card.children).map((el) => el.className)).toEqual([
                "conformer-card-select", "conformer-basin-identity", "refs-disclosure",
            ])
        }

        const oneCard = screen.getByText("Conformer Group 1", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        const sevenCard = screen.getByText("Conformer Group 2", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        // The rotor COUNTS genuinely differ (this is what a fixture where
        // every card has identical content cannot exercise) -- only the
        // number and identity of .conformer-card's own DIRECT children,
        // the CSS's row-track anchor points, stay equal.
        expect(oneCard.querySelectorAll(".conformer-basin-rotor")).toHaveLength(1)
        expect(sevenCard.querySelectorAll(".conformer-basin-rotor")).toHaveLength(7)
        expect(oneCard.children.length).toBe(sevenCard.children.length)
    })

    it("a zero-rotor card (fingerprint present, empty torsions -- the archive's OWN 'rigid' shape) still places References as .conformer-card's 3rd direct child, occupying the basin row's slot rather than collapsing it away", () => {
        const rigid = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1", fingerprint: rotorFingerprint(0) },
        })
        renderSelector([rigid])
        const card = screen.getByText("Conformer Group 1", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        expect(Array.from(card.children).map((el) => el.className)).toEqual([
            "conformer-card-select", "conformer-basin-rigid", "refs-disclosure",
        ])
    })

    // FLAGGED, not fixed on this branch (CSS-scoped work; see the
    // row-track-alignment comment in `species-entry.css`): a group with NO
    // fingerprint at all on the wire renders NEITHER basin variant, so
    // `.refs-disclosure` becomes `.conformer-card`'s 2nd direct child here,
    // not its 3rd -- unlike the zero-rotor-with-fingerprint case just
    // above. The CSS's explicit `grid-row: 5` pin on `.conformer-card >
    // .refs-disclosure` still lands it on the shared references row
    // regardless of which direct-child position it occupies in the DOM --
    // row 4 (the basin row) is simply empty for this card, reserved by the
    // grid's own row-track sizing, not by an element sitting inside it.
    // This documents that DOM shape rather than asserting a 3rd child that
    // does not exist for it.
    it("a no-fingerprint card has References as its 2nd (not 3rd) direct child -- the CSS's explicit row pin, not DOM position, is what keeps it aligned with siblings that DO render a basin element", () => {
        const noFingerprint = conformer({
            conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        })
        renderSelector([noFingerprint])
        const card = screen.getByText("Conformer Group 1", { selector: ".conformer-card-label" }).closest(".conformer-card") as HTMLElement
        expect(Array.from(card.children).map((el) => el.className)).toEqual(["conformer-card-select", "refs-disclosure"])
    })
})
