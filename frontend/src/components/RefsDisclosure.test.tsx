import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { CopyButton, RefsDisclosure } from "./RefsDisclosure"

/**
 * `CopyButton` is the riskiest edit in the "Unavailable" copy-state diff
 * (see `RefsDisclosure.tsx`'s docstring) and, before this file, had ZERO
 * tests of its own on either the "copied" or "unavailable" branch —
 * `GeometryDetailPage.test.tsx`'s "raw XYZ copy button" tests only ever
 * check that a button with the right accessible name exists, never that
 * clicking it does the right thing. That gap is why "successful copy
 * reports 'Unavailable' everywhere" and "shared `srLabel` default changed
 * -> every entry-page aria-label changes" both survived full-suite
 * mutation review. This file exercises the component directly, with a
 * stubbed `navigator.clipboard` (jsdom does not provide a real one).
 */

afterEach(() => {
    cleanup()
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
})

describe("CopyButton", () => {
    it("writes the given value to the clipboard, announces success via a status region, and never rewrites its own accessible name", async () => {
        const writeText = vi.fn().mockResolvedValue(undefined)
        Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })

        render(<CopyButton value="the-copied-value" label="SMILES" />)
        const button = screen.getByRole("button", { name: "Copy SMILES reference" })
        // The status region exists (empty) from the very first render —
        // queried by role up front, scoped to, rather than a page-wide
        // `findByText("Copied")`, which would ambiguously match BOTH this
        // region and the button's own visible label once both read
        // "Copied" at once.
        const status = screen.getByRole("status")
        fireEvent.click(button)

        expect(writeText).toHaveBeenCalledWith("the-copied-value")
        await waitFor(() => expect(status).toHaveTextContent("Copied"))
        expect(button).toHaveTextContent("Copied")

        // Accessible name is stable by design — MEASURED (the finding
        // this fixes): an earlier version of this component set
        // `aria-label` to the state-dependent button text, so the name
        // read "Copy SMILES reference" whether the click had succeeded,
        // failed, or not happened yet at all — a screen-reader user got
        // no signal anything changed. Announce the state change via the
        // separate `role="status"` region instead of rewriting the name
        // out from under an assistive-tech user mid-interaction.
        expect(button).toHaveAttribute("aria-label", "Copy SMILES reference")
        // The house rule: a status region holds a MESSAGE, never the
        // payload — never the copied value itself.
        expect(status).not.toHaveTextContent("the-copied-value")
    })

    it("announces a distinct failure message when the clipboard write is rejected (permission denied)", async () => {
        const writeText = vi.fn().mockRejectedValue(new Error("denied"))
        Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })

        render(<CopyButton value="v" label="SMILES" />)
        fireEvent.click(screen.getByRole("button", { name: "Copy SMILES reference" }))

        expect(await screen.findByText("Unavailable")).toBeVisible()
        expect(screen.getByRole("status")).toHaveTextContent("Copy unavailable")
        // Never reads as success.
        expect(screen.getByRole("status")).not.toHaveTextContent("Copied")
    })

    it("announces unavailable immediately, with no clipboard write attempted, when the Clipboard API itself is absent (insecure context)", () => {
        // `navigator.clipboard` is undefined outside a secure context
        // (plain http://, not localhost) — this must not throw trying to
        // call `.writeText` on it.
        Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
        render(<CopyButton value="v" label="SMILES" />)
        fireEvent.click(screen.getByRole("button", { name: "Copy SMILES reference" }))
        expect(screen.getByText("Unavailable")).toBeVisible()
        expect(screen.getByRole("status")).toHaveTextContent("Copy unavailable")
    })

    it("uses the caller's srLabel in the accessible name when given, and the 'reference' default otherwise", () => {
        // Pins the shared default so a mutation changing `srLabel`'s
        // default (which would silently change every existing call
        // site's aria-label, entry-page included) is observable here,
        // not just wherever the default happens to be exercised.
        render(<CopyButton value="v" label="SMILES" />)
        expect(screen.getByRole("button", { name: "Copy SMILES reference" })).toBeVisible()
        cleanup()
        render(<CopyButton value="v" label="raw XYZ" srLabel="text" />)
        expect(screen.getByRole("button", { name: "Copy raw XYZ text" })).toBeVisible()
    })
})

// `inset` -- retiring the third disclosure box style by naming it
// ("header copy and inset disclosure" PR): a caller whose disclosure
// sits inside an already-boxed card (`ConformerSelector.tsx`'s
// `.conformer-card`, the one real consumer today) passes this to get the
// shared `.disclosure--inset` modifier instead of a page-scoped CSS
// override. See `ConformerSelector.test.tsx`'s "double-border fix still
// holds" describe block for the rendered computed-style proof that this
// class actually changes the box's borders.
describe("RefsDisclosure inset prop", () => {
    function renderRefs(inset?: boolean) {
        return render(
            <MemoryRouter>
                <RefsDisclosure inset={inset} refs={[{ label: "Conformer group", value: "cg_demo", to: "/conformer-groups/cg_demo" }]} />
            </MemoryRouter>,
        )
    }

    it("adds disclosure--inset alongside the base classes when inset is true", () => {
        renderRefs(true)
        const details = document.querySelector("details") as HTMLDetailsElement
        expect(details.className.split(" ")).toEqual(["disclosure", "refs-disclosure", "disclosure--inset"])
    })

    it("omits disclosure--inset by default -- the entry-hero's own usage never sets it", () => {
        renderRefs()
        const details = document.querySelector("details") as HTMLDetailsElement
        expect(details.className.split(" ")).toEqual(["disclosure", "refs-disclosure"])
    })
})
