import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { SpeciesEntryLink } from "./SpeciesEntryLink"
import { ADVERSARIAL, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"

afterEach(() => cleanup())

/**
 * The "Species entry" fact never renders `species_entry_label` ALONE as
 * the link text (a bare "R", no context): link text is `SpeciesFace` (SMILES leads,
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

    // The distinguishing case:
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

    // Adversarial labels that pass every shape check: a typed electronic_state_label `R` on a ground-state
    // entry reads exactly like a stereo descriptor, so nothing from the joined label is ever printed.
    it.each(["R", "S", "E", "Z", "excited R,S,R,S,E,Z", "[2H]POOP", "PISS", "C1"])(
        "never prints anything from species_entry_label (%s), no stereo wording either",
        (label) => {
            const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "C1=CC=CC=C1C", formula: "C9H9", speciesEntryLabel: label } as never)
            const link = container.querySelector("a")!
            expect(link.textContent).toBe("C1=CC=CC=C1C (C9H9)")
            expect(link.textContent).not.toMatch(/enantiomer|isomer|allene|excited|POOP|PISS/)
        },
    )

    it("no formula served: the SMILES stands alone (no parenthesised formula), still in a data-face code element", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: "[N-]=[NH2+]", formula: null })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("[N-]=[NH2+]")
        expect(link.querySelector("code.data")?.textContent).toBe("[N-]=[NH2+]")
    })

    // When there is neither a SMILES nor a formula (any
    // caller, on an entry whose species SMILES does not parse), the base
    // text falls back to the entry's own ref as `<code className="data">`
    // -- NEVER the literal words "Species entry", which would repeat the
    // enclosing <dt> ("Species entry / Species entry · R enantiomer") and
    // say nothing new.
    it("falls back to the entry ref, styled as a data code run, when there is no SMILES, no formula, and no label", () => {
        const { container } = renderLink({ speciesEntryRef: "se_abc123", smiles: null, formula: null })
        const link = container.querySelector("a")!
        expect(link.textContent).toBe("se_abc123")
        const code = link.querySelector("code")
        expect(code).not.toBeNull()
        expect(code).toHaveClass("data")
        expect(code!.textContent).toBe("se_abc123")
        // Never the redundant literal text.
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

describe("SpeciesEntryLink -- structured facets", () => {
    it("appends chips built from the structured facets, and nothing from a joined label", () => {
        const { container } = render(
            <MemoryRouter>
                <SpeciesEntryLink
                    speciesEntryRef="se_abc123"
                    smiles="CC=CC"
                    formula="C4H8"
                    facets={{ species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "Z", stereo_descriptor: "Z" }}
                />
            </MemoryRouter>,
        )
        expect(container.querySelector("a")!.textContent).toBe("CC=CC (C4H8) · Z isomer")
    })
})

describe("SpeciesEntryLink -- no depositor-typed text", () => {
    it("scans the whole link: a typed label is never in its text or any attribute", () => {
        const planted = plantSentinels({ speciesEntryRef: "se_abc123", smiles: "CC", formula: "C2H6", speciesEntryLabel: "x" }, ["speciesEntryLabel"])
        expectPlanted(planted)
        const { container } = renderLink({ ...planted.value, speciesEntryLabel: `${ADVERSARIAL.stereoMark} ${ADVERSARIAL.smilesWord} ${planted.value.speciesEntryLabel}` })
        // The fixture reached the component: the link rendered the SMILES and formula.
        expect(container.querySelector("a")!.textContent).toBe("CC (C2H6)")

        expectNoDepositorText()
        expectNoAdversarialText()
    })
})
