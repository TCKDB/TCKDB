import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { TransitionStateBrowseRecord } from "../api/browseApi"
import { TransitionStateBrowseRow } from "./TransitionStateBrowseRow"

afterEach(cleanup)

function renderRow(record: TransitionStateBrowseRecord) {
    return render(
        <MemoryRouter>
            <ul>
                <TransitionStateBrowseRow record={record} />
            </ul>
        </MemoryRouter>,
    )
}

function record(overrides: Partial<TransitionStateBrowseRecord> = {}): TransitionStateBrowseRecord {
    return {
        transition_state_entry: {
            transition_state_entry_ref: "tse_one",
            charge: 0,
            multiplicity: 2,
            status: "optimized",
            unmapped_smiles: null,
            created_at: "2026-08-05T14:04:16.914780",
            review: { status: "not_reviewed" },
        },
        transition_state: {
            transition_state_ref: "ts_one",
            label: "TS0",
            note: null,
            review: { status: "not_reviewed" },
        },
        reaction: {
            reaction_ref: "rxn_one",
            reaction_entry_ref: "rxe_deposit_one",
            equation: "A <=> B",
            reversible: true,
            family: "R_Addition_MultipleBond",
        },
        evidence_summary: {
            calculation_count: 4,
            has_opt: true,
            has_freq: true,
            has_sp: true,
            has_irc: true,
            has_path_search: false,
            has_geometry_validation: false,
            has_scf_stability: false,
            levels_of_theory: {
                opt: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                freq: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                sp: [{ method: "MRCI+Davidson", basis: "aug-cc-pV(T+d)Z", display: "MRCI+Davidson/aug-cc-pV(T+d)Z" }],
            },
            software: {
                opt: [{ software: "orca", version: "5.0.4" }],
                freq: [{ software: "orca", version: "5.0.4" }],
                sp: [{ software: "molpro", version: "2022.1" }],
            },
        },
        ...overrides,
    } as TransitionStateBrowseRecord
}

// Item 3: the pill carries the status alone, never fused with the
// depositor's own label ("TS0 · optimized").
//
// The depositor's `transition_state.label` is a house-rule-banned
// depositor-typed label (widened 2026-09: "no labels on the front end,
// they make no sense like TS0 and TS1") and must never render anywhere on
// this row, regardless of whether one was deposited -- there is no
// fallback text naming the label's absence either, unlike the fields this
// row DOES claim to state (e.g. "family not recorded"), because the
// archive is not making a claim about a label at all any more.
describe("TransitionStateBrowseRow: pill is status only, no depositor label anywhere on the row", () => {
    it("renders the status alone in the classification pill, and never the depositor label", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(row).toBeTruthy()

        const kindPill = within(row).getByText("optimized").closest(".value-pill")
        expect(kindPill).toBeTruthy()
        expect(kindPill).toHaveClass("browse-entry-kind-pill")
        // The old fused text is gone from the pill.
        expect(within(row).queryByText("TS0 · optimized")).not.toBeInTheDocument()

        const reviewPill = within(row).getByText("not reviewed").closest(".value-pill")
        expect(reviewPill).toBeTruthy()
        expect(reviewPill).toHaveClass("browse-entry-review")
        expect(reviewPill).toHaveClass("value-pill--muted")
        expect(kindPill).not.toBe(reviewPill)

        // The depositor label is not rendered anywhere on the row, deposited
        // ("TS0") or not.
        expect(within(row).queryByText(/TS0/)).not.toBeInTheDocument()
        expect(within(row).queryByText(/Unlabeled transition state/)).not.toBeInTheDocument()
    })

    it("renders nothing label-shaped when no label was deposited either -- same row either way", () => {
        renderRow(record({ transition_state: { transition_state_ref: "ts_one", label: null, note: null, review: { status: "not_reviewed" } } }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).queryByText(/Unlabeled transition state/)).not.toBeInTheDocument()
        expect(within(row).getByText("optimized")).toBeVisible()
    })
})

// Job 1: the fact that actually distinguishes several TS deposits of the
// SAME reaction is which reaction deposit each belongs to
// (`reaction.reaction_entry_ref`) -- MEASURED live: level of theory,
// software and the deposited date (truncated to a plain date) are not
// enough on their own, since real same-reaction deposits share all three.
describe("TransitionStateBrowseRow: the reaction deposit ref distinguishes same-reaction rows", () => {
    it("renders 'from reaction entry <ref>' in the footer", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const deposit = row.querySelector(".browse-row-deposit") as HTMLElement
        expect(deposit).toBeTruthy()
        expect(deposit.textContent).toContain("from reaction entry")
        expect(within(deposit).getByText("rxe_deposit_one")).toBeVisible()
    })

    it("renders nothing deposit-shaped when the archive gave no reaction entry ref", () => {
        renderRow(record({ reaction: { reaction_ref: "rxn_one", reaction_entry_ref: null, equation: "A <=> B", reversible: true, family: "R_Addition_MultipleBond" } }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(row.querySelector(".browse-row-deposit")).toBeNull()
    })

    it("gives two rows that would otherwise be identical (same LOT, software, status, deposited date) a distinct deposit ref", () => {
        const shared = record()
        const other = record({
            transition_state_entry: {
                ...shared.transition_state_entry,
                transition_state_entry_ref: "tse_two",
            },
            transition_state: { transition_state_ref: "ts_two", label: "TS1", note: null, review: { status: "not_reviewed" } },
            reaction: { ...shared.reaction, reaction_entry_ref: "rxe_deposit_two" },
        })
        const { unmount } = renderRow(shared)
        const rowA = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(rowA).getByText("rxe_deposit_one")).toBeVisible()
        unmount()
        renderRow(other)
        const rowB = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(rowB).getByText("rxe_deposit_two")).toBeVisible()
        expect(within(rowB).queryByText("rxe_deposit_one")).not.toBeInTheDocument()
    })
})

// Item 3: "family not recorded" must read as an absence, not a real family
// name -- the muted/italic `.absent` register the rest of the archive
// already uses for a missing value (`QuantityValue.tsx`), not plain text
// indistinguishable from "R_Addition_MultipleBond".
describe("TransitionStateBrowseRow: absent family uses the muted/absent register", () => {
    it("renders a real family as plain text, not in the absent register", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const meta = row.querySelector(".browse-row-meta") as HTMLElement
        expect(meta.textContent).toContain("R Addition MultipleBond")
        expect(meta.querySelector(".absent")).toBeNull()
    })

    it("renders 'family not recorded' in an absent-styled element, not indistinguishable plain text", () => {
        renderRow(record({ reaction: { reaction_ref: "rxn_one", reaction_entry_ref: "rxe_one", equation: "A <=> B", reversible: true, family: null } }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const absent = within(row).getByText("family not recorded")
        expect(absent.className).toMatch(/absent/)
    })
})

// Item 1: level of theory + software + deposit date must be visible on the
// row, not hidden behind a second request -- the wire already carries
// `evidence_summary.levels_of_theory` (confirmed against the live API),
// this component just has to read it, and the backend now also serves
// `evidence_summary.software`.
describe("TransitionStateBrowseRow: provenance line (level of theory, software, deposit date)", () => {
    it("shows the opt and sp levels of theory together", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/opt wb97xd\/def2tzvp/)).toBeVisible()
        expect(within(row).getByText(/sp MRCI\+Davidson\/aug-cc-pV\(T\+d\)Z/)).toBeVisible()
    })

    it("states software distinctly per stage when it differs (opt on orca, sp on molpro)", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/orca 5\.0\.4/)).toBeVisible()
        expect(within(row).getByText(/molpro 2022\.1/)).toBeVisible()
    })

    it("states software once when every selected stage shares it", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 2,
                has_opt: true, has_freq: false, has_sp: true, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {
                    opt: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                    sp: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                },
                software: {
                    opt: [{ software: "gaussian", version: "16" }],
                    sp: [{ software: "gaussian", version: "16" }],
                },
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const provenance = row.querySelector(".browse-row-provenance") as HTMLElement
        expect(provenance.textContent).toContain("gaussian 16")
        // Only stated once -- not "opt gaussian 16 · sp gaussian 16".
        expect(provenance.textContent?.match(/gaussian 16/g)?.length).toBe(1)
    })

    it("states 'software not recorded' when a calculation exists but names no software release", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 1,
                has_opt: true, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {
                    opt: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                },
                software: { opt: [] },
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/software not recorded/)).toBeVisible()
    })

    it("states 'level of theory not recorded' / 'software not recorded' when there is no evidence at all, on a CURRENT API response (software key present, empty)", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 0,
                has_opt: false, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                software: {},
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/level of theory not recorded/)).toBeVisible()
        expect(within(row).getByText(/software not recorded/)).toBeVisible()
    })

    it("joins every distinct level of theory for a stage, not just the first, when a stage genuinely carries more than one", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 2,
                has_opt: false, has_freq: false, has_sp: true, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {
                    sp: [
                        { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                        { method: "CCSD(T)-F12", basis: "cc-pVTZ-F12", display: "CCSD(T)-F12/cc-pVTZ-F12" },
                    ],
                },
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const provenance = row.querySelector(".browse-row-provenance") as HTMLElement
        expect(provenance.textContent).toContain("b3lyp/def2tzvp")
        expect(provenance.textContent).toContain("CCSD(T)-F12/cc-pVTZ-F12")
    })

    it("renders NOTHING for software when evidence_summary.software is undefined (an API version that never served the field), never 'software not recorded'", () => {
        // Same evidence_summary as the 'no evidence at all' fixture above,
        // but WITHOUT a `software` key at all -- this is what an older API
        // response (or the TS-entry-detail builder, which does not
        // populate this field) looks like on the wire. "software not
        // recorded" is a claim the CURRENT API makes about the DATA; an
        // absent field is a claim about the WIRE VERSION and must not be
        // rendered as if the archive had asserted anything about software.
        renderRow(record({
            evidence_summary: {
                calculation_count: 0,
                has_opt: false, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/level of theory not recorded/)).toBeVisible()
        expect(within(row).queryByText(/software/)).not.toBeInTheDocument()
    })

    it("renders NOTHING for software when it is undefined even though levels_of_theory IS present", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 1,
                has_opt: true, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {
                    opt: [{ method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
                },
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/opt wb97xd\/def2tzvp/)).toBeVisible()
        expect(within(row).queryByText(/software/)).not.toBeInTheDocument()
    })

    it("shows the deposit date from transition_state_entry.created_at", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/deposited 2026-08-05/)).toBeVisible()
    })
})

// Item 2: the link wraps ONLY the equation now -- not the ref, not the
// pills, not the evidence line. Reproduced here by checking those pieces
// sit OUTSIDE the link element, the inverse of the old test suite (which
// asserted they were INSIDE it).
describe("TransitionStateBrowseRow: link wraps only the equation", () => {
    it("has an accessible name of exactly the equation plus the reaction deposit, never the depositor label", () => {
        renderRow(record())
        const link = screen.getByRole("link")
        // Exact, not a length bound or a substring match: an aria-label
        // sets the accessible name precisely, so this is load-bearing --
        // it fails the moment the name gains or loses anything, unlike a
        // "<30 chars" bound that would still pass with unrelated content
        // swapped in. The ~170-character accessible name the old
        // whole-row link produced (equation + family + charge/spin + both
        // pills + evidence text + ref) is gone, and so is the depositor
        // label ("TS0") the accessible name used to carry.
        expect(link).toHaveAccessibleName("A <=> B (deposit rxe_deposit_one)")
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_one")
    })

    it("falls back to the bare equation for the accessible name when no reaction entry ref was served", () => {
        renderRow(record({ reaction: { reaction_ref: "rxn_one", reaction_entry_ref: null, equation: "A <=> B", reversible: true, family: "R_Addition_MultipleBond" } }))
        expect(screen.getByRole("link")).toHaveAccessibleName("A <=> B")
    })

    it("does not contain the ref, the pills, or the evidence line", () => {
        renderRow(record())
        const link = screen.getByRole("link")
        expect(within(link).queryByText("tse_one")).not.toBeInTheDocument()
        expect(within(link).queryByText("optimized")).not.toBeInTheDocument()
        expect(within(link).queryByText("not reviewed")).not.toBeInTheDocument()
        expect(within(link).queryByText(/Evidence:/)).not.toBeInTheDocument()
    })

    it("renders the ref, pills and evidence line as ordinary text OUTSIDE the link, in the same row", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        // Present in the row overall...
        expect(within(row).getByText("tse_one")).toBeVisible()
        expect(within(row).getByText("optimized")).toBeVisible()
        expect(within(row).getByText(/Evidence:/)).toBeVisible()
        // ...but not reachable via the link's own subtree (checked above),
        // and the ref is a plain <code>, never an anchor descendant, so a
        // drag-select over it cannot start a link drag.
        const refCode = within(row).getByText("tse_one")
        expect(refCode.closest("a")).toBeNull()
    })

    it("does not change WHERE the row links -- exactly transition_state_entry.transition_state_entry_ref, not the reaction ref", () => {
        renderRow(record({
            transition_state_entry: {
                transition_state_entry_ref: "tse_specific",
                charge: 0,
                multiplicity: 2,
                status: "optimized",
                unmapped_smiles: null,
                created_at: "2026-08-05T14:04:16.914780",
                review: { status: "not_reviewed" },
            },
            reaction: { reaction_ref: "rxn_specific", reaction_entry_ref: "rxe_x", equation: "X <=> Y", reversible: null, family: null },
        }))
        expect(screen.getByRole("link")).toHaveAttribute("href", "/transition-state-entries/tse_specific")
    })

    it("renders no link at all when the archive gave no transition-state entry ref -- the row is inert, matching the prior fallback", () => {
        renderRow(record({
            transition_state_entry: {
                transition_state_entry_ref: null as unknown as string,
                charge: 0,
                multiplicity: 2,
                status: "optimized",
                unmapped_smiles: null,
                created_at: "2026-08-05T14:04:16.914780",
                review: { status: "not_reviewed" },
            },
            reaction: { reaction_ref: "rxn_one", reaction_entry_ref: "rxe_one", equation: "Z <=> W", reversible: null, family: null },
        }))
        expect(screen.queryByRole("link")).not.toBeInTheDocument()
        expect(screen.getByText("Z <=> W")).toBeVisible()
    })
})

describe("TransitionStateBrowseRow: unchanged behaviour", () => {
    it("still renders charge/spin and the 'Equation not recorded' fallback", () => {
        renderRow(record({ reaction: { reaction_ref: null, reaction_entry_ref: null, equation: null, reversible: null, family: null } }))
        expect(screen.getByText("Equation not recorded")).toBeVisible()
        expect(screen.getByText(/charge 0 · spin doublet/)).toBeVisible()
        expect(screen.getByText(/Evidence: opt · freq · sp · irc \(4 calculations\)/)).toBeVisible()
    })
})

// PR D (design-system adoption on the index/record pages): the row's box
// (padding/border/radius/background) now comes from the shared `.card`
// primitive, and the ref renders through the shared `.data` step instead of
// its own one-off 11px mono run.
describe("TransitionStateBrowseRow: design-system primitive adoption", () => {
    it("the row carries the shared .card primitive alongside its own .ts-browse-row class", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(row).toHaveClass("card")
        expect(row).toHaveClass("browse-row")
    })

    it("the ref renders through .browse-ref and .data (the shared data step), not the retired .browse-row-ref", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const ref = within(row).getByText("tse_one")
        expect(ref).toHaveClass("browse-ref")
        expect(ref).toHaveClass("data")
        expect(ref).not.toHaveClass("browse-row-ref")
    })

    it("the evidence line carries its own .browse-row-evidence class", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const evidence = within(row).getByText(/Evidence:/)
        expect(evidence).toHaveClass("browse-row-evidence")
    })
})
