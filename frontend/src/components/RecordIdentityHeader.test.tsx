import { describe, expect, it, afterEach } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { RecordIdentityHeader } from "./RecordIdentityHeader"
import type { RecordIdentity } from "../domain/recordIdentity"

afterEach(cleanup)

function renderHeader(props: Parameters<typeof RecordIdentityHeader>[0]) {
    return render(<MemoryRouter><RecordIdentityHeader {...props} /></MemoryRouter>)
}

const speciesIdentity: RecordIdentity = {
    kind: "species_entry",
    formula: "CH3",
    canonicalSmiles: "[CH3]",
    inchiKey: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
    charge: 0,
    multiplicity: 2,
    speciesEntryRef: "spe_demo",
}

const tsIdentity: RecordIdentity = {
    kind: "transition_state_entry",
    formula: null,
    unmappedSmiles: null,
    charge: 0,
    multiplicity: 2,
    transitionStateEntryRef: "tse_demo",
}

describe("RecordIdentityHeader", () => {
    it("renders a known species identity with SMILES and InChIKey", () => {
        renderHeader({ identity: speciesIdentity })
        expect(screen.getByText("[CH3]")).toBeVisible()
        expect(screen.getByText("WCYWZMWISLQXQU-UHFFFAOYSA-N")).toBeVisible()
    })

    it("renders the ambiguous case distinctly from the unambiguous case -- no SMILES/InChIKey shown, owners listed instead", () => {
        renderHeader({
            identity: { kind: "ambiguous", owners: [{ kind: "species_entry", ref: "spe_a" }, { kind: "species_entry", ref: "spe_b" }] },
        })
        expect(screen.getByTestId("record-identity-ambiguous")).toHaveTextContent(/more than one distinct owner/)
        expect(screen.getByText("spe_a")).toBeVisible()
        expect(screen.getByText("spe_b")).toBeVisible()
        expect(screen.queryByText("SMILES")).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()
    })

    it("renders the absent case distinctly from both known and ambiguous", () => {
        renderHeader({ identity: { kind: "absent" } })
        expect(screen.getByText(/No molecular identity is recorded/)).toBeVisible()
        expect(screen.queryByTestId("record-identity-ambiguous")).not.toBeInTheDocument()
    })

    it("a transition-state identity never renders a SMILES or InChIKey field, even an empty one", () => {
        renderHeader({ identity: tsIdentity })
        // No "SMILES" or "InChIKey" label anywhere -- not present with an
        // empty value, not present at all.
        expect(screen.queryByText("SMILES")).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()
        // Its own field, "Reaction SMILES (unmapped)", is present and
        // says plainly that nothing was deposited -- not a blank cell.
        expect(screen.getByText("Reaction SMILES (unmapped)")).toBeVisible()
        expect(screen.getByText("not recorded")).toBeVisible()
    })

    it("renders a transition-state's unmapped SMILES when one was deposited", () => {
        renderHeader({ identity: { ...tsIdentity, unmappedSmiles: "[CH2]OO[CH2]" } })
        expect(screen.getByText("[CH2]OO[CH2]")).toBeVisible()
    })

    it("renders the 'no canonical SMILES' note for a transition-state identity by default", () => {
        renderHeader({ identity: tsIdentity })
        expect(screen.getByText(/no canonical SMILES/i)).toBeVisible()
    })

    it("omits the 'no canonical SMILES' note when explainTransitionStateIdentity=false -- the caller's own Reaction section covers it instead", () => {
        renderHeader({ identity: tsIdentity, explainTransitionStateIdentity: false })
        expect(screen.queryByText(/no canonical SMILES/i)).not.toBeInTheDocument()
    })

    it("inserts a <wbr> after '>>' and '.' in a multi-fragment unmapped SMILES so it wraps at token boundaries", () => {
        const { container } = renderHeader({
            identity: { ...tsIdentity, unmappedSmiles: "[CH3].[OH2]>>[CH4].[O]" },
        })
        const code = container.querySelector(".record-identity-facts code")
        expect(code).not.toBeNull()
        expect(code?.querySelectorAll("wbr").length).toBe(3)
        expect(code?.textContent).toBe("[CH3].[OH2]>>[CH4].[O]")
    })

    it("spans the unmapped-SMILES fact across the full facts grid", () => {
        const { container } = renderHeader({
            identity: { ...tsIdentity, unmappedSmiles: "[CH3].[OH2]" },
        })
        const wideFact = container.querySelector(".record-identity-fact-wide")
        expect(wideFact).not.toBeNull()
        expect(wideFact?.textContent).toContain("Reaction SMILES (unmapped)")
    })

    it("renders no submission row at all when the key is absent (anonymous caller)", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: undefined })
        expect(screen.queryByText("Submission")).not.toBeInTheDocument()
    })

    it("renders 'not recorded' when the key is present but null (authenticated, no linked submission)", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: null })
        expect(screen.getByText("Submission")).toBeVisible()
        expect(screen.getByText("not recorded")).toBeVisible()
    })

    it("renders the submission ref when the key is present and populated", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: "sub_demo" })
        expect(screen.getByText("sub_demo")).toBeVisible()
    })

    it("renders classification facets as a plain readable phrase, only when facets are supplied -- no pill row", () => {
        const { rerender } = render(
            <MemoryRouter>
                <RecordIdentityHeader identity={speciesIdentity} />
            </MemoryRouter>,
        )
        expect(document.querySelector(".record-identity-facets")).toBeNull()
        rerender(
            <MemoryRouter>
                <RecordIdentityHeader
                    identity={speciesIdentity}
                    facets={{ species_entry_kind: "minimum", electronic_state_kind: "ground" }}
                />
            </MemoryRouter>,
        )
        const facets = document.querySelector(".record-identity-facets")
        expect(facets).not.toBeNull()
        expect(facets).toHaveTextContent("minimum · ground state")
        // No pill boxes: never a `.record-facet-chips` / `.record-facet-chip`
        // list, not even when facets are supplied.
        expect(document.querySelector(".record-facet-chips")).toBeNull()
    })
})
