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
 * never let it stand alone: link text is `SpeciesFace` (SMILES leads,
 * formula follows in parentheses), with the label (expanded via
 * `stereoChip`) appended when present, and the ref as a data run only
 * when there is neither SMILES nor formula.
 */
function renderLink(props: { speciesEntryRef: string; smiles?: string | null; formula?: string | null; speciesEntryLabel?: string | null }) {
    return render(
        <MemoryRouter>
            <SpeciesEntryLink {...props} />
        </MemoryRouter>,
    )
}

describe("SpeciesEntryLink", () => {
    it("link text leads with SMILES, formula (subscripted like every other formula on the app) following in parentheses, when there is no label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "CC1=CC=CC=C1", formula: "C9H9" })
        const link = container.querySelector("a")
        expect(link).not.toBeNull()
        expect(link!.getAttribute("href")).toBe("/species-entries/se_abc123")
        expect(link!.textContent).toBe("CC1=CC=CC=C1 (C9H9)")
        expect(link!.querySelector("sub")?.textContent).toBe("9")
        expect(link!.querySelector("code.data")?.textContent).toBe("CC1=CC=CC=C1")
    })

    // The owner's own reported defect, applied to this component too:
    // two DIFFERENT species entries (sharing a formula) must render
    // different link text.
    it("two species entries sharing a formula render different link text ([CH2]SO vs OC[S])", () => {
        const reactant = renderLink({ speciesEntryRef: "se_reactant", smiles: "[CH2]SO", formula: "CH3OS" })
        const reactantText = reactant.container.querySelector("a")!.textContent
        reactant.unmount()

        const product = renderLink({ speciesEntryRef: "se_product", smiles: "OC[S]", formula: "CH3OS" })
        const productText = product.container.querySelector("a")!.textContent

        expect(reactantText).not.toBe(productText)
        expect(reactantText).toBe("[CH2]SO (CH3OS)")
        expect(productText).toBe("OC[S] (CH3OS)")
    })

    it("a stereo-descriptor label ('R') is expanded via stereoChip and appended after the SpeciesFace, never as the sole text", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "C1=CC=CC=C1C", formula: "C9H9", speciesEntryLabel: "R" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("C1=CC=CC=C1C (C9H9) · R enantiomer")
        // The raw, unexpanded label alone is never the whole story here --
        // it always rides along with the SMILES/formula.
        expect(link.textContent).not.toBe("R")
    })

    it("an 'S' label expands to 'S enantiomer', 'E'/'Z' to 'isomer' -- the shared stereoChip expansion, not a bespoke one", () => {
        const s = renderLink({ speciesEntryRef: "se_1", smiles: "CC(C)C", formula: "C4H8", speciesEntryLabel: "S" })
        expect(s.container.querySelector("a")!.textContent).toBe("CC(C)C (C4H8) · S enantiomer")
        s.unmount()

        const e = renderLink({ speciesEntryRef: "se_2", smiles: "CC=CC", formula: "C4H8", speciesEntryLabel: "E" })
        expect(e.container.querySelector("a")!.textContent).toBe("CC=CC (C4H8) · E isomer")
    })

    it("a label stereoChip does not recognise still rides along with the SpeciesFace, unchanged, never suppressed", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "C1=CC=CC=C1C", formula: "C9H9", speciesEntryLabel: "T1" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("C1=CC=CC=C1C (C9H9) · T1")
    })

    it("no formula served: the SMILES stands alone (no parenthesised formula), still in a data-face code element", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "[N-]=[NH2+]", formula: null })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("[N-]=[NH2+]")
        expect(link.querySelector("code.data")?.textContent).toBe("[N-]=[NH2+]")
    })

    // Post-review fix: when there is neither a SMILES nor a formula (any
    // caller, on an entry whose species SMILES does not parse), the base
    // text falls back to the entry's own ref as `<code className="data">`
    // -- NEVER the literal words "Species entry", which would repeat the
    // enclosing <dt> ("Species entry / Species entry · R enantiomer") and
    // say nothing new.
    it("falls back to the entry ref, styled as a data code run, when there is no SMILES, no formula, and no label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: null, formula: null, speciesEntryLabel: null })
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
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: null, formula: null, speciesEntryLabel: "R" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123 · R enantiomer")
        expect(link.querySelector("code")).toHaveClass("data")
        expect(link.textContent).not.toContain("Species entry")
    })

    it("treats an empty-string SMILES and formula the same as absent -- falls back to the ref", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "", formula: "" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123")
    })

    it("no SMILES served but a formula is: falls back to the formula alone (subscripted), matching SpeciesFace's own fallback", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: null, formula: "C9H9" })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("C9H9")
        expect(link.querySelector("sub")?.textContent).toBe("9")
        expect(link.querySelector("code.data")).toBeNull()
    })
})
