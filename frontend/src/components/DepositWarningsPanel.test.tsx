import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { DepositWarningsPanel } from "./DepositWarningsPanel"
import type { DepositWarningItem, DepositWarnings } from "../api/depositWarningsSchema"
import { DEPOSITOR_SENTINEL, expectDepositorTextOnlyInNotes, expectNoDepositorText } from "../test/depositorSentinel"

afterEach(cleanup)

const ITEM: DepositWarningItem = {
    code: "missing_irc_for_ts",
    message: "This transition state has no IRC calculation.",
    field: "calculations[*]",
    register_entry: 19,
    context: { calculation_type: "irc" },
    check_version: 1,
}

const warnings = (items: DepositWarningItem[], extra: Partial<DepositWarnings> = {}, status = "recorded"): DepositWarnings => ({
    record_warnings: [{ record_ref: "calc_a", status, items }],
    deposit_warnings: [],
    ...extra,
})

describe("DepositWarningsPanel", () => {
    it("renders nothing when every block is empty", () => {
        const { container } = render(<DepositWarningsPanel warnings={{
            record_warnings: [{ record_ref: "calc_a", status: "recorded", items: [] }, { record_ref: "calc_b", status: "not_recorded", items: [] }],
            deposit_warnings: [{ items: [] }],
        }} />)
        expect(container).toBeEmptyDOMElement()
        expect(screen.queryByText("Deposit warnings")).toBeNull()
    })

    it("shows the message, the field, then the register entry and the code", () => {
        render(<DepositWarningsPanel warnings={warnings([ITEM])} recordRef="calc_a" />)
        const item = screen.getByRole("listitem")
        const paragraphs = Array.from(item.querySelectorAll("p")).map((p) => p.textContent)
        expect(paragraphs[0]).toBe("This transition state has no IRC calculation.")
        expect(paragraphs[1]).toBe("calculations[*]")
        expect(paragraphs[2]).toBe("Check register entry 19 · missing_irc_for_ts")
        expect(screen.getByRole("heading", { name: "Deposit warnings" })).toBeInTheDocument()
        expect(screen.getByText("This record")).toBeInTheDocument()
    })

    it("shows the code alone when there is no register entry", () => {
        render(<DepositWarningsPanel warnings={warnings([{ ...ITEM, register_entry: null }])} />)
        expect(screen.queryByText(/Check register entry/)).toBeNull()
        expect(screen.getByText("missing_irc_for_ts")).toBeInTheDocument()
    })

    it("says an unrecorded block was added later", () => {
        render(<DepositWarningsPanel warnings={warnings([ITEM], {}, "not_recorded")} />)
        expect(screen.getByText("Warnings from the original deposit were not recorded; the items below were added later.")).toBeInTheDocument()
    })

    it("lists the whole-deposit block with its submission ref when served", () => {
        render(<DepositWarningsPanel warnings={{ record_warnings: [], deposit_warnings: [{ submission_ref: "sub_abc", items: [ITEM] }, { items: [ITEM] }] }} />)
        expect(screen.getAllByText("About the deposit as a whole")).toHaveLength(2)
        expect(screen.getAllByText("sub_abc")).toHaveLength(1)
    })

    it("labels another record's block through labelForRef", () => {
        const both: DepositWarnings = {
            record_warnings: [
                { record_ref: "calc_other", status: "recorded", items: [ITEM] },
                { record_ref: "calc_a", status: "recorded", items: [ITEM] },
            ],
            deposit_warnings: [],
        }
        render(<DepositWarningsPanel warnings={both} recordRef="calc_a" labelForRef={(ref) => `Calculation ${ref}`} />)
        const labels = Array.from(document.querySelectorAll(".deposit-warnings-owner")).map((n) => n.textContent)
        expect(labels).toEqual(["This record", "Calculation calc_other"])
    })

    it("shows depositor text only inside As deposited notes for an owner or curator", () => {
        const typed = {
            ...ITEM,
            depositor_message: `${DEPOSITOR_SENTINEL}_message`,
            depositor_field: `${DEPOSITOR_SENTINEL}_field`,
            context: { depositor_supplied: `${DEPOSITOR_SENTINEL}_supplied` },
        }
        render(<DepositWarningsPanel warnings={warnings([typed])} recordRef="calc_a" />)
        const notes = document.querySelectorAll(".depositor-note blockquote")
        expect(Array.from(notes).map((n) => n.textContent)).toEqual([
            `${DEPOSITOR_SENTINEL}_message`, `${DEPOSITOR_SENTINEL}_field`, `${DEPOSITOR_SENTINEL}_supplied`,
        ])
        expect(screen.getAllByLabelText(/As deposited/).length).toBe(3)
        expectDepositorTextOnlyInNotes()
    })

    it("shows no depositor text to anyone else, even with depositor_supplied planted", () => {
        const typed = { ...ITEM, context: { depositor_supplied: `${DEPOSITOR_SENTINEL}_supplied`, other: `${DEPOSITOR_SENTINEL}_other` } }
        render(<DepositWarningsPanel warnings={warnings([typed])} recordRef="calc_a" />)
        expect(document.querySelector(".depositor-note")).toBeNull()
        expectNoDepositorText()
    })

    it("marks a retired item for a curator, with its date and the reason in words", () => {
        render(<DepositWarningsPanel warnings={warnings([
            { ...ITEM, depositor_message: "x", retired_at: "2026-10-04T10:00:00", retired_reason: "artifact_attached" },
            { ...ITEM, code: "other", depositor_message: "y", retired_at: "2026-10-05T10:00:00", retired_reason: "some_new_reason" },
        ])} />)
        expect(screen.getAllByText("Retired")).toHaveLength(2)
        expect(screen.getByText("2026-10-04, an artifact was attached after the deposit")).toBeInTheDocument()
        expect(screen.getByText("2026-10-05, some new reason")).toBeInTheDocument()
    })

    it("renders an h4 in block variant", () => {
        render(<DepositWarningsPanel warnings={warnings([ITEM])} variant="block" headingId="dw-thm_1" />)
        const region = screen.getByRole("region", { name: "Deposit warnings" })
        expect(within(region).getByRole("heading", { level: 4 })).toHaveAttribute("id", "dw-thm_1")
    })
})
