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

    // Post-review fix: when there is no formula (ConformerObservationPage's
    // species context never serves one), the base text falls back to the
    // entry's own ref as `<code className="data">` -- NEVER the literal
    // words "Species entry", which would repeat the enclosing <dt> ("Species
    // entry / Species entry · R enantiomer") and say nothing new.
    it("falls back to the entry ref, styled as a data code run, when there is no formula and no label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: null, speciesEntryLabel: null })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123")
        const code = link.querySelector("code")
        expect(code).not.toBeNull()
        expect(code).toHaveClass("data")
        expect(code!.textContent).toBe("se_abc123")
        // Never the redundant literal text this fallback used to render.
        expect(link.textContent).not.toContain("Species entry")
    })

    it("falls back to the entry ref as the base text, still followed by an expanded label, when only the label is served", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: null, speciesEntryLabel: "R" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123 · R enantiomer")
        expect(link.querySelector("code")).toHaveClass("data")
        expect(link.textContent).not.toContain("Species entry")
    })

    it("treats an empty-string formula the same as absent -- falls back to the ref", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", formula: "" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123")
    })
})
