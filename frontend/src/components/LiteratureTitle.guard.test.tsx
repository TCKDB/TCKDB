// Sentinel guard: a literature title shows on a public page only when
// the server says it was resolved from DOI/ISBN metadata. Any other title, and any DOI, is depositor text.

import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryTransportSection } from "./EntryTransportSection"
import { TITLE_NOT_VERIFIED } from "../domain/literatureCitation"
import { expectNoDepositorText } from "../test/depositorSentinel"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const SENTINEL = "DEPOSITOR_SENTINEL_literature_title"
const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/transport`

function record(literature: Record<string, unknown>) {
    return {
        transport: {
            transport_ref: "trn_lit", scientific_origin: "computed", sigma_angstrom: 3.8, epsilon_over_k_k: 250.1,
            dipole_debye: null, polarizability_angstrom3: null, rotational_relaxation: null, note: null,
            created_at: "2026-07-21T12:14:32.845900",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        supersession: null,
        species: {
            species_ref: "spc_ch3", species_entry_ref: entryRef, species_entry_label: null,
            canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", charge: 0, multiplicity: 2,
        },
        software_release: null,
        workflow_tool_release: null,
        literature,
        evidence_summary: {
            source_calculation_count: 1, has_source_calculations: true, has_lj_parameters: true,
            has_dipole_moment: false, has_polarizability: false, has_rotational_relaxation: false,
            has_literature_source: true,
        },
        available_sections: { has_source_calculations: true, has_review: false },
    }
}

async function renderWith(literature: Record<string, unknown>) {
    const records = [record(literature)]
    server.use(http.get(ENDPOINT, () => HttpResponse.json({
        review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
        records,
        pagination: { offset: 0, limit: 50, returned: 1, total: 1 },
    })))
    render(<MemoryRouter><EntryTransportSection entryRef={entryRef} /></MemoryRouter>)
    // Proves the fixture reached the page: the record itself rendered.
    await screen.findByText("trn_lit")
}

const expectNoSentinel = expectNoDepositorText

const base = { literature_ref: "lit_abc", title: SENTINEL, year: 2024, doi: "10.1000/xyz" }

describe("a literature title on a public page", () => {
    it("is shown when it was resolved from DOI/ISBN metadata (the positive case: the test can see the title)", async () => {
        await renderWith({ ...base, title_source: "resolved_metadata" })
        expect(document.body.innerHTML).toContain(SENTINEL)
        expect(screen.queryByText(TITLE_NOT_VERIFIED)).not.toBeInTheDocument()
    })

    it("is withheld when the depositor typed it; the page prints the public ref and says the title is not verified", async () => {
        await renderWith({ ...base, title_source: "depositor_supplied" })
        expectNoSentinel()
        expect(screen.getByText(/lit_abc/)).toBeVisible()
        expect(screen.getByText(TITLE_NOT_VERIFIED)).toBeVisible()
    })

    it("never prints or links the DOI of an unverified record: it is depositor-typed text", async () => {
        const doi = "10.1/ DEPOSITOR_SENTINEL_doi visit spam.example"
        await renderWith({ ...base, doi, title_source: "depositor_supplied" })
        expectNoSentinel()
        expect(document.body.innerHTML).not.toContain("spam.example")
        expect(document.body.innerHTML).not.toContain("doi.org")
        expect(screen.queryByRole("link", { name: /10\.1/ })).not.toBeInTheDocument()
    })

    it("is withheld when its origin was never recorded (null)", async () => {
        await renderWith({ ...base, title_source: null })
        expectNoSentinel()
        expect(screen.getByText(TITLE_NOT_VERIFIED)).toBeVisible()
    })

    it("is withheld when the field is absent from the response entirely", async () => {
        await renderWith({ ...base })
        expectNoSentinel()
    })

    it("falls back to the ISBN when there is no DOI", async () => {
        await renderWith({ literature_ref: "lit_abc", title: SENTINEL, title_source: "depositor_supplied", isbn: "9780387954523" })
        expectNoSentinel()
        expect(screen.getByText(/ISBN 9780387954523/)).toBeVisible()
    })

    it("falls back to the public ref when there is no DOI or ISBN", async () => {
        await renderWith({ literature_ref: "lit_abc", title: SENTINEL, title_source: "depositor_supplied" })
        expectNoSentinel()
        expect(screen.getByText(/lit_abc/)).toBeVisible()
    })
})
