import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { SpeciesEntryLink } from "./SpeciesEntryLink"

afterEach(() => cleanup())

/**
 * Owner report ("record-page residuals" re-review, item 4): the
 * "Species entry" fact used to render `species_entry_label` ALONE as
 * the link text -- a bare "R" on a sampled live entry, no context.
 *
 * Correction mid-fix (see `SpeciesEntryLink.tsx`'s own docstring):
 * `species_entry_label` is not depositor free text, it is a computed
 * discriminator (a stereo descriptor, an electronic state, a term
 * symbol, an isotope key). The fix is not to suppress it -- it is to
 * never let it stand alone: link text is the formula, with the label
 * (expanded via `stereoChip`) appended when present, and "Species
 * entry" as the base text only when there is neither.
 */
function renderLink(props: { speciesEntryRef: string; formula?: string | null; speciesEntryLabel?: string | null }) {
    return render(
        <MemoryRouter>
            <SpeciesEntryLink {...props} />
        </MemoryRouter>,
    )
}

describe("SpeciesEntryLink", () => {
    it("link text is the formula, subscripted like every other formula on the app, when there is no label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: "C9H9" })
        const link = container.querySelector("a")
        expect(link).not.toBeNull()
        expect(link!.getAttribute("href")).toBe("/species-entries/se_abc123")
        expect(link!.textContent).toBe("C9H9")
        expect(link!.querySelector("sub")?.textContent).toBe("9")
    })

    it("a stereo-descriptor label ('R') is expanded via stereoChip and appended after the formula, never as the sole text", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: "C9H9", speciesEntryLabel: "R" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("C9H9 · R enantiomer")
        // The raw, unexpanded label alone is never the whole story here --
        // it always rides along with the formula.
        expect(link.textContent).not.toBe("R")
    })

    it("an 'S' label expands to 'S enantiomer', 'E'/'Z' to 'isomer' -- the shared stereoChip expansion, not a bespoke one", () => {
        const s = renderLink({ speciesEntryRef: "se_1", formula: "C4H8", speciesEntryLabel: "S" })
        expect(s.container.querySelector("a")!.textContent).toBe("C4H8 · S enantiomer")
        s.unmount()

        const e = renderLink({ speciesEntryRef: "se_2", formula: "C4H8", speciesEntryLabel: "E" })
        expect(e.container.querySelector("a")!.textContent).toBe("C4H8 · E isomer")
    })

    it("a label stereoChip does not recognise still rides along with the formula, unchanged, never suppressed", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: "C9H9", speciesEntryLabel: "T1" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("C9H9 · T1")
    })

    it("falls back to the literal text 'Species entry' when there is neither a formula nor a label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: null, speciesEntryLabel: null })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("Species entry")
    })

    it("falls back to 'Species entry' as the base text, still followed by an expanded label, when only the label is served", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: null, speciesEntryLabel: "R" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("Species entry · R enantiomer")
    })

    it("treats an empty-string formula the same as absent", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: "" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("Species entry")
    })
})
