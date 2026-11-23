import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ReproducibilityDetailPanel } from "./ReproducibilityDetailPanel"
import { meaningOf } from "../domain/apiVocabulary"
import { CHECK_SENTENCE, WARNING_SENTENCE } from "../domain/curationWording"
import { SENTINEL, assessmentRead } from "../test/curationFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const BASE = `/api/v1/curation/reproducibility-assessments/calculation/${SENTINEL.recordId}`

function mount() {
    return render(<ReproducibilityDetailPanel recordType="calculation" recordId={SENTINEL.recordId} recordLabel="this calculation" idBase="t1" />)
}

describe("ReproducibilityDetailPanel", () => {
    it("says not assessed yet and offers Evaluate when there is no assessment", async () => {
        server.use(http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "none" }, { status: 404 })))
        mount()
        expect(await screen.findByText("Not assessed yet.")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Evaluate" })).toBeEnabled()
    })

    it("shows the grade, meaning, ref, date, rubric and every group in words", async () => {
        server.use(http.get(`${BASE}/latest`, () => HttpResponse.json(assessmentRead())))
        mount()
        const region = await screen.findByRole("region", { name: /Reproducibility of this calculation/ })
        expect(within(region).getByText("auditable", { selector: ".value-pill" })).toBeInTheDocument()
        expect(within(region).getByText(meaningOf("reproducibilityGrade", "auditable")!)).toBeInTheDocument()
        expect(within(region).getByText(/Assessed 2026-11-02/)).toBeInTheDocument()
        expect(within(region).getByText(/rpa_latest/)).toBeInTheDocument()
        expect(within(region).getByText("Rubric TCKDB reproducibility, version 3.")).toBeInTheDocument()
        expect(region.textContent).not.toMatch(/current/i)
        expect(within(region).getByText(/by TCKDB's rubric/i)).toBeInTheDocument()

        expect(within(region).getByRole("heading", { level: 3, name: "Reproducibility" })).toBeInTheDocument()
        expect(within(region).getByRole("heading", { level: 4, name: "Missing" })).toBeInTheDocument()
        const missing = within(region).getByRole("group", { name: "Missing" })
        expect(within(missing).getByText(CHECK_SENTENCE.preserved_input_artifacts)).toBeInTheDocument()
        expect(within(missing).getByText(CHECK_SENTENCE.verified_output_artifact_bytes)).toBeInTheDocument()
        expect(within(missing).getByText("Rerunnable")).toBeInTheDocument()
        expect(within(missing).getByText("Auditable")).toBeInTheDocument()

        const passed = within(region).getByRole("group", { name: "Passed" })
        expect(within(passed).getByText(CHECK_SENTENCE.target_identity)).toBeInTheDocument()
        expect(within(passed).queryByText(CHECK_SENTENCE.source_role_preservation)).toBeNull()
        const na = within(region).getByRole("group", { name: "Does not apply" })
        expect(within(na).getByText(CHECK_SENTENCE.source_role_preservation)).toBeInTheDocument()

        const warnings = within(region).getByRole("group", { name: "Warnings" })
        expect(within(warnings).getByText(WARNING_SENTENCE.artifact_integrity_failed)).toBeInTheDocument()
        expect(within(warnings).getByText(/abababababab\b/)).toBeInTheDocument()
        expect(within(warnings).getByText(/aie_event/)).toBeInTheDocument()
        expect(region.textContent).not.toMatch(/preserved_input_artifacts|artifact_integrity_failed|not_applicable/)
    })

    it("never renders an internal id or the raw context", async () => {
        server.use(http.get(`${BASE}/latest`, () => HttpResponse.json(assessmentRead())))
        mount()
        await screen.findByText(/Assessed 2026-11-02/)
        const html = document.body.innerHTML
        for (const value of Object.values(SENTINEL)) expect(html).not.toContain(String(value))
        expect(html).not.toContain("c0ffee")
        expect(html).not.toContain("context_json")
    })

    it("evaluates: disables the button while busy, posts, re-reads latest and shows the new assessment", async () => {
        let latest = assessmentRead({ assessment_ref: "rpa_old", grade: "described" })
        const calls: string[] = []
        server.use(
            http.get(`${BASE}/latest`, () => { calls.push("GET"); return HttpResponse.json(latest) }),
            http.post(`${BASE}/evaluate`, async () => {
                calls.push("POST")
                await delay(40)
                latest = assessmentRead({ assessment_ref: "rpa_new", grade: "rerunnable", missing: [], warnings: [] })
                return HttpResponse.json(latest, { status: 201 })
            }),
        )
        mount()
        await screen.findByText("described", { selector: ".value-pill" })
        expect(screen.getByText(/rpa_old/)).toBeInTheDocument()
        expect(screen.getByText(/adds a new assessment/i)).toBeInTheDocument()
        const button = screen.getByRole("button", { name: "Evaluate again" })
        await userEvent.click(button)
        await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"))
        expect(button).toHaveAttribute("aria-busy", "true")
        expect(screen.getByRole("status")).toHaveTextContent("Evaluating")
        await screen.findByText("rerunnable", { selector: ".value-pill" })
        expect(calls).toEqual(["GET", "POST", "GET"])
        const after = screen.getByRole("button", { name: "Evaluate again" })
        expect(after).not.toHaveAttribute("aria-disabled", "true")
        expect(after).toHaveAttribute("aria-busy", "false")
        expect(screen.getByText(/rpa_new/)).toBeInTheDocument()
        expect(screen.queryByText(/rpa_old/)).toBeNull()
    })

    it("shows a reader sentence when evaluating fails", async () => {
        server.use(
            http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "none" }, { status: 404 })),
            http.post(`${BASE}/evaluate`, () => HttpResponse.json({ detail: "Traceback in rubric" }, { status: 500 })),
        )
        mount()
        await userEvent.click(await screen.findByRole("button", { name: "Evaluate" }))
        const alert = await screen.findByRole("alert")
        expect(alert.textContent).not.toMatch(/Traceback/)
        expect(alert.textContent).toMatch(/could not/i)
        expect(alert.textContent).not.toMatch(/could not be completed\. The request/)
        const retry = screen.getByRole("button", { name: "Evaluate" })
        expect(retry).not.toHaveAttribute("aria-disabled", "true")
        expect(retry).toHaveAttribute("aria-busy", "false")
    })

    it("does not say the evaluation failed when it was saved and only the re-read failed", async () => {
        server.use(
            http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "none" }, { status: 404 }), { once: true }),
            http.post(`${BASE}/evaluate`, () => HttpResponse.json(assessmentRead({ grade: "described" }), { status: 201 })),
            http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
        )
        mount()
        await userEvent.click(await screen.findByRole("button", { name: "Evaluate" }))
        const alert = await screen.findByRole("alert")
        expect(alert.textContent).toMatch(/was saved/i)
        expect(alert.textContent).not.toMatch(/could not be completed/i)
        expect(screen.getByText("described", { selector: ".value-pill" })).toBeInTheDocument()
    })

    it("keeps focus on the button while evaluating and after a failure", async () => {
        server.use(
            http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "none" }, { status: 404 })),
            http.post(`${BASE}/evaluate`, async () => { await delay(40); return HttpResponse.json({}, { status: 500 }) }),
        )
        mount()
        const button = await screen.findByRole("button", { name: "Evaluate" })
        button.focus()
        await userEvent.keyboard("{Enter}")
        expect(button).toHaveFocus()
        await screen.findByRole("alert")
        expect(button).toHaveFocus()
    })

    it("words a 403 as curators and admins only", async () => {
        server.use(http.get(`${BASE}/latest`, () => HttpResponse.json({ detail: "Forbidden" }, { status: 403 })))
        mount()
        expect(await screen.findByRole("alert")).toHaveTextContent(/only curators and admins/i)
    })
})
