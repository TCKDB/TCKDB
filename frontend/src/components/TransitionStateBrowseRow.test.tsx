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
            reaction_entry_ref: "rxe_one",
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

// Item 3: the pill used to fuse the label onto the status ("TS0 ·
// optimized"). The label now lives on the meta line as plain text; the
// pill carries the status alone.
describe("TransitionStateBrowseRow: pill is status only, label moved to the meta line", () => {
    it("renders the status alone in the classification pill, and the label in the meta line instead", () => {
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

        // The label is visible as plain text in the meta line.
        const meta = row.querySelector(".browse-row-meta") as HTMLElement
        expect(meta).toBeTruthy()
        expect(within(meta).getByText(/TS0/)).toBeInTheDocument()
    })

    it("falls back to 'Unlabeled transition state' in the meta line when no label was deposited", () => {
        renderRow(record({ transition_state: { transition_state_ref: "ts_one", label: null, note: null, review: { status: "not_reviewed" } } }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        const meta = row.querySelector(".browse-row-meta") as HTMLElement
        expect(within(meta).getByText(/Unlabeled transition state/)).toBeVisible()
        // Still just "optimized" in the pill, not fused with the fallback label.
        expect(within(row).getByText("optimized")).toBeVisible()
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

    it("states 'level of theory not recorded' / 'software not recorded' when there is no evidence at all", () => {
        renderRow(record({
            evidence_summary: {
                calculation_count: 0,
                has_opt: false, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
            } as TransitionStateBrowseRecord["evidence_summary"],
        }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText(/level of theory not recorded/)).toBeVisible()
        expect(within(row).getByText(/software not recorded/)).toBeVisible()
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
    it("has an accessible name of just the equation (+ label), not the ref or the whole row", () => {
        renderRow(record())
        const link = screen.getByRole("link")
        const name = link.textContent ?? ""
        // Contains the equation and the label...
        expect(name).toMatch(/A <=> B/)
        expect(name).toMatch(/TS0/)
        // ...and nothing else: no ref, no review status, no evidence text,
        // no family/charge/spin -- the ~170-character accessible name the
        // old whole-row link produced is gone. 30 chars comfortably covers
        // "A <=> B (TS0)" with margin for formatting differences.
        expect(name.length).toBeLessThan(30)
        expect(link).not.toHaveAttribute("aria-label")
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_one")
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
