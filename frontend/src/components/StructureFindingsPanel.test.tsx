import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { Announcer } from "./Announcer"
import { AuthProvider } from "./AuthProvider"
import { StructureFindingsPanel, type FindingSubject } from "./StructureFindingsPanel"
import { findingPage, findingRead, removedAdjudication } from "../test/curationFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const FINDINGS = "/api/v1/curation/structure-evidence-findings"
const ME = "/api/v1/auth/me"
const user = (role: string) => ({ id: 1, username: "ada", email: "a@example.com", full_name: "Ada", role, is_active: true })

const ONE = [{ ref: "calc_freq_one", scope: "calculation" as const, label: "this calculation" }]
const MANY = [
    { ref: "calc_one", scope: "calculation" as const, label: "Optimisation calc_one" },
    { ref: "geom_two", scope: "geometry" as const, label: "Final geometry geom_two" },
]

function mount(subjects: FindingSubject[] = ONE, withProvider = true) {
    const body = (
        <MemoryRouter>
            <StructureFindingsPanel subjects={subjects} idBase="test" />
        </MemoryRouter>
    )
    return render(withProvider ? <AuthProvider>{body}</AuthProvider> : body)
}

function curatorSees(list: unknown[] = [], role = "curator") {
    const requests: URL[] = []
    server.use(
        http.get(ME, () => HttpResponse.json(user(role))),
        http.get(FINDINGS, ({ request }) => {
            requests.push(new URL(request.url))
            return HttpResponse.json(findingPage(list))
        }),
    )
    return requests
}

describe("who sees the panel", () => {
    function countCurationCalls(meStatus: number, role = "user") {
        const calls = { curation: 0, me: 0 }
        server.use(
            http.get(ME, () => {
                calls.me += 1
                return meStatus === 200 ? HttpResponse.json(user(role)) : new HttpResponse(null, { status: meStatus })
            }),
            http.all("/api/v1/curation/*", () => {
                calls.curation += 1
                return HttpResponse.json(findingPage([]))
            }),
        )
        return calls
    }

    it("shows nothing and requests nothing to an anonymous visitor", async () => {
        const calls = countCurationCalls(401)
        mount()
        await waitFor(() => expect(calls.me).toBe(1))
        await waitFor(() => expect(screen.queryByText(/loading/i)).toBeNull())
        expect(screen.queryByText("Structure findings")).toBeNull()
        expect(calls.curation).toBe(0)
    })

    it("shows nothing and requests nothing to a plain signed-in user", async () => {
        const calls = countCurationCalls(200, "user")
        mount()
        await waitFor(() => expect(calls.me).toBe(1))
        await waitFor(() => expect(screen.queryByText(/loading/i)).toBeNull())
        expect(screen.queryByText("Structure findings")).toBeNull()
        expect(calls.curation).toBe(0)
    })

    it("shows nothing and does not throw with no auth provider", () => {
        mount(ONE, false)
        expect(screen.queryByText("Structure findings")).toBeNull()
    })

    it.each(["curator", "admin"])("shows the section to a %s and asks for that subject and scope", async (role) => {
        const requests = curatorSees([], role)
        mount()
        expect(await screen.findByRole("heading", { name: "Structure findings" })).toBeInTheDocument()
        await waitFor(() => expect(requests).toHaveLength(1))
        expect(requests[0].searchParams.get("subject_ref")).toBe("calc_freq_one")
        expect(requests[0].searchParams.get("scope")).toBe("calculation")
        expect(screen.getByText(/only curators and admins see this section/i)).toBeInTheDocument()
    })
})

describe("the list", () => {
    it("words a finding, links what settled it and what it corrects, and labels the rationale as the curator's", async () => {
        curatorSees([
            findingRead({ finding_ref: "sef_old", in_force: false, settled_by_finding_ref: "sef_new", applied_kind: null, applied_role: null }),
            findingRead({
                finding_ref: "sef_new", kind: "adjudication", role: null, verdict: "does_not_invalidate", supersedes_finding_ref: "sef_old",
                applied_kind: "adjudication", applied_role: null, rationale: "On review the energy is sound.",
            }),
        ])
        mount()
        const items = await screen.findAllByRole("listitem")
        const old = items.find((li) => li.id === "finding-sef_old")!
        const next = items.find((li) => li.id === "finding-sef_new")!
        expect(within(old).getByText(/Role invalidation/)).toBeInTheDocument()
        expect(within(old).getByRole("link", { name: "sef_new" })).toHaveAttribute("href", "#finding-sef_new")
        expect(within(old).getByText(/Settled by/)).toBeInTheDocument()
        expect(within(old).queryByText("In force")).toBeNull()
        expect(within(next).getByText("In force")).toBeInTheDocument()
        expect(within(next).getByRole("link", { name: "sef_old" })).toHaveAttribute("href", "#finding-sef_old")
        expect(within(next).getByText(/Corrects/)).toBeInTheDocument()
        expect(within(old).getByRole("link", { name: /calculation/i })).toHaveAttribute("href", "/calculations/calc_opt_one")
        expect(within(old).getByText(/2026-07-21/)).toBeInTheDocument()
        const note = within(next).getByRole("group", { name: "Curator's rationale" })
        expect(within(note).getByText("On review the energy is sound.")).toBeInTheDocument()
        expect(within(note).getByText(/written by the curator/i)).toBeInTheDocument()
        expect(document.body.textContent).not.toMatch(/role_invalidation|does_not_invalidate|authorized_adjudication/)
    })

    it("marks a depositor's assertion as the depositor's", async () => {
        curatorSees([findingRead({ authority: "producer_assertion" })])
        mount()
        expect(await screen.findByText(/asserted by the depositor/i)).toBeInTheDocument()
    })

    it("says so when there are no findings", async () => {
        curatorSees([])
        mount()
        expect(await screen.findByText("No findings have been recorded about this calculation.")).toBeInTheDocument()
    })

    it("shows a reader sentence on a load failure and retries", async () => {
        let calls = 0
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, () => {
                calls += 1
                return calls === 1 ? HttpResponse.json({ detail: "db exploded" }, { status: 500 }) : HttpResponse.json(findingPage([findingRead()]))
            }),
        )
        mount()
        const alert = await screen.findByRole("alert")
        expect(alert.textContent).not.toMatch(/db exploded/)
        await userEvent.click(within(alert.parentElement!).getByRole("button", { name: "Retry" }))
        expect(await screen.findByText("In force")).toBeInTheDocument()
    })

    it("keeps keyboard focus inside the panel when Retry is pressed, on success and on a second failure", async () => {
        let calls = 0
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, async () => {
                calls += 1
                if (calls < 3) { await delay(20); return HttpResponse.json({ detail: "x" }, { status: 500 }) }
                return HttpResponse.json(findingPage([findingRead()]))
            }),
        )
        mount()
        const first = await screen.findByRole("button", { name: "Retry" })
        first.focus()
        await userEvent.keyboard("{Enter}")
        expect(document.activeElement).not.toBe(document.body)
        await waitFor(() => expect(calls).toBe(2))
        const again = await screen.findByRole("button", { name: "Retry" })
        await waitFor(() => expect(document.activeElement).toBe(again))
        await userEvent.click(again)
        await screen.findByText("In force")
        await waitFor(() => expect(document.activeElement?.className).toMatch(/structure-findings-result/))
    })

    it("announces the refusal sentence after a second failed Retry", async () => {
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, () => HttpResponse.json({ detail: "x" }, { status: 500 })),
        )
        render(<AuthProvider><MemoryRouter><Announcer /><StructureFindingsPanel subjects={ONE} idBase="test" /></MemoryRouter></AuthProvider>)
        const first = await screen.findByRole("button", { name: "Retry" })
        await userEvent.click(first)
        const again = await screen.findByRole("button", { name: "Retry" })
        await userEvent.click(again)
        const sentence = "The request could not be completed. Try again, and reload the page if it keeps failing."
        await waitFor(() => expect(document.querySelector(".announcer")).toHaveTextContent(sentence))
    })

    it("shows each finding's own ref on its card and the date with a label", async () => {
        curatorSees([findingRead({ finding_ref: "sef_aaa3" }), findingRead({ finding_ref: "sef_aaa4" })])
        mount()
        const items = await screen.findAllByRole("listitem")
        expect(within(items[0]).getByText("sef_aaa3")).toBeInTheDocument()
        expect(within(items[1]).getByText("sef_aaa4")).toBeInTheDocument()
        expect(within(items[0]).getByText(/Recorded 2026-07-21/)).toBeInTheDocument()
    })

    it("links Settled by only to a finding that is on the page", async () => {
        curatorSees([findingRead({ in_force: false, settled_by_finding_ref: "sef_off_page" })])
        mount()
        const item = (await screen.findAllByRole("listitem"))[0]
        expect(within(item).getByText("sef_off_page").closest("a")).toBeNull()
    })

    it("pages with Show more when a full page came back", async () => {
        const requests: URL[] = []
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, ({ request }) => {
                const url = new URL(request.url)
                requests.push(url)
                const skip = Number(url.searchParams.get("skip"))
                const limit = Number(url.searchParams.get("limit"))
                return HttpResponse.json(skip === 0
                    ? findingPage(Array.from({ length: limit }, (_, i) => findingRead({ finding_ref: `sef_p${i}` })))
                    : findingPage([findingRead({ finding_ref: "sef_last" })]))
            }),
        )
        mount()
        const more = await screen.findByRole("button", { name: "Show more" })
        const limit = Number(requests[0].searchParams.get("limit"))
        await userEvent.click(more)
        await waitFor(() => expect(document.getElementById("finding-sef_last")).not.toBeNull())
        expect(requests[1].searchParams.get("skip")).toBe(String(limit))
        expect(screen.queryByRole("button", { name: "Show more" })).toBeNull()
    })
})

describe("the form", () => {
    async function open(list: unknown[] = []) {
        const requests = curatorSees(list)
        mount()
        await screen.findByRole("form", { name: "Record a finding" })
        return requests
    }
    const kind = () => screen.getByLabelText("Kind")
    const verdict = () => screen.getByLabelText("Verdict")
    const rationale = () => screen.getByLabelText(/Rationale/)

    it("offers Role only for a role invalidation and Corrects finding only for an adjudication", async () => {
        await open([findingRead({ finding_ref: "sef_live" }), findingRead({ finding_ref: "sef_gone", in_force: false, settled_by_finding_ref: "sef_live" })])
        await screen.findByText("In force")
        kind() // default
        await userEvent.selectOptions(kind(), "identity_incompatibility")
        expect(screen.queryByLabelText("Role")).toBeNull()
        expect(screen.queryByLabelText("Corrects finding")).toBeNull()
        await userEvent.selectOptions(kind(), "role_invalidation")
        expect(screen.getByLabelText("Role")).toBeInTheDocument()
        await userEvent.selectOptions(kind(), "adjudication")
        const corrects = screen.getByLabelText("Corrects finding")
        expect(screen.queryByLabelText("Role")).toBeNull()
        const values = within(corrects).getAllByRole("option").map((o) => (o as HTMLOptionElement).value).filter(Boolean)
        expect(values).toEqual(["sef_live"])
    })

    it.each([
        ["a blank rationale", async () => { /* leave blank */ }, /give the reason/i],
        ["an over-long rationale", async () => { fireEvent.change(rationale(), { target: { value: "x".repeat(2001) } }) }, /limited to 2000 characters/i],
        ["a source without calc_", async () => { fireEvent.change(rationale(), { target: { value: "ok" } }); await userEvent.type(screen.getByLabelText(/Source calculation/), "geom_1") }, /starts with calc_/],
        ["a literature ref without lit_", async () => { fireEvent.change(rationale(), { target: { value: "ok" } }); await userEvent.type(screen.getByLabelText(/Literature/), "doi:1") }, /starts with lit_/],
        ["does not invalidate on a non-adjudication", async () => { fireEvent.change(rationale(), { target: { value: "ok" } }); await userEvent.selectOptions(kind(), "identity_incompatibility"); await userEvent.selectOptions(verdict(), "does_not_invalidate") }, /must be an adjudication/i],
    ])("blocks %s with an inline error and sends nothing", async (_name, fill, message) => {
        let posts = 0
        await open()
        server.use(http.post(FINDINGS, () => { posts += 1; return HttpResponse.json(findingRead(), { status: 201 }) }))
        await userEvent.selectOptions(kind(), "identity_incompatibility")
        await fill()
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        const error = await screen.findByText(message)
        const field = document.querySelector("[aria-invalid='true']")
        expect(field).not.toBeNull()
        expect(field!.getAttribute("aria-describedby")).toContain(error.id)
        expect(posts).toBe(0)
    })

    it("counts and accepts a 2000-character rationale padded with spaces, sending it trimmed", async () => {
        let body: Record<string, unknown> = {}
        await open()
        server.use(http.post(FINDINGS, async ({ request }) => {
            body = (await request.json()) as Record<string, unknown>
            return HttpResponse.json(findingRead(), { status: 201 })
        }))
        await userEvent.selectOptions(kind(), "identity_incompatibility")
        const text = "x".repeat(2000)
        fireEvent.change(rationale(), { target: { value: `   ${text}   ` } })
        expect(screen.getByText("2000 / 2000")).toBeInTheDocument()
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        await waitFor(() => expect(body.rationale).toBe(text))
    })

    it("requires a role for a role invalidation and a target for an adjudication", async () => {
        await open([findingRead({ finding_ref: "sef_live" })])
        await screen.findByText("In force")
        fireEvent.change(rationale(), { target: { value: "ok" } })
        await userEvent.selectOptions(kind(), "role_invalidation")
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        expect(await screen.findByText(/choose the role/i)).toBeInTheDocument()
        await userEvent.selectOptions(kind(), "adjudication")
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        expect(await screen.findByText(/choose the finding it corrects/i)).toBeInTheDocument()
    })

    it("records a finding: posts, announces, resets, reloads and moves focus to the status", async () => {
        const gets: number[] = []
        let body: Record<string, unknown> = {}
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, () => { gets.push(1); return HttpResponse.json(findingPage(gets.length === 1 ? [] : [findingRead({ finding_ref: "sef_made" })])) }),
            http.post(FINDINGS, async ({ request }) => {
                body = (await request.json()) as Record<string, unknown>
                await delay(30)
                return HttpResponse.json(findingRead({ finding_ref: "sef_made" }), { status: 201 })
            }),
        )
        mount()
        await screen.findByRole("form", { name: "Record a finding" })
        await userEvent.selectOptions(kind(), "identity_incompatibility")
        fireEvent.change(rationale(), { target: { value: "Different species." } })
        const submit = screen.getByRole("button", { name: "Record finding" })
        await userEvent.click(submit)
        await waitFor(() => expect(submit).toHaveAttribute("aria-disabled", "true"))
        expect(submit).toHaveAttribute("aria-busy", "true")
        const status = await screen.findByRole("status")
        expect(status).toHaveTextContent("Finding sef_made recorded.")
        expect(status).toHaveFocus()
        expect(submit).not.toHaveAttribute("aria-disabled", "true")
        expect(submit).toHaveAttribute("aria-busy", "false")
        expect(body).toMatchObject({ scope: "calculation", subject_ref: "calc_freq_one", kind: "identity_incompatibility", rationale: "Different species." })
        expect(rationale()).toHaveValue("")
        await waitFor(() => expect(document.getElementById("finding-sef_made")).not.toBeNull())
        expect(gets.length).toBe(2)
    })

    it("words an already-settled refusal from its code and context", async () => {
        await open([findingRead({ finding_ref: "sef_live" })])
        await screen.findByText("In force")
        server.use(http.post(FINDINGS, () => HttpResponse.json(
            { code: "structure_finding_already_settled", detail: "this finding has already been corrected by an adjudication", context: { supersedes_finding_ref: "sef_live", settled_by_finding_ref: "sef_other" } },
            { status: 409 },
        )))
        await userEvent.selectOptions(kind(), "adjudication")
        await userEvent.selectOptions(screen.getByLabelText("Corrects finding"), "sef_live")
        await userEvent.selectOptions(verdict(), "does_not_invalidate")
        fireEvent.change(rationale(), { target: { value: "Second opinion." } })
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/already been corrected by sef_other/i)
        expect(alert.textContent).not.toMatch(/structure_finding|detail text|adjudication$/)
        expect(rationale()).toHaveValue("Second opinion.")
    })

    it("words a 403 as curators and admins only", async () => {
        await open()
        server.use(http.post(FINDINGS, () => HttpResponse.json({ detail: "Forbidden" }, { status: 403 })))
        await userEvent.selectOptions(kind(), "identity_incompatibility")
        fireEvent.change(rationale(), { target: { value: "Because." } })
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        expect(await screen.findByRole("alert")).toHaveTextContent(/only curators and admins/i)
    })
})

describe("several subjects", () => {
    it("lets the reader choose the subject, reloads, and ignores a late reply for the old one", async () => {
        const asked: string[] = []
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, async ({ request }) => {
                const ref = new URL(request.url).searchParams.get("subject_ref")!
                asked.push(ref)
                if (ref === "calc_one") {
                    await delay(150)
                    return HttpResponse.json(findingPage([findingRead({ finding_ref: "sef_for_calc" })], { subject_ref: ref }))
                }
                return HttpResponse.json(findingPage([findingRead({ finding_ref: "sef_for_geom", scope: "geometry", subject_ref: ref })], { subject_ref: ref }))
            }),
        )
        mount(MANY)
        const select = await screen.findByLabelText("Findings about")
        expect(within(select).getAllByRole("option").map((o) => (o as HTMLOptionElement).value)).toEqual(["calculation:calc_one", "geometry:geom_two"])
        await userEvent.selectOptions(select, "geometry:geom_two")
        await waitFor(() => expect(document.getElementById("finding-sef_for_geom")).not.toBeNull())
        await new Promise((r) => setTimeout(r, 250))
        expect(document.getElementById("finding-sef_for_calc")).toBeNull()
        expect(asked).toEqual(["calc_one", "geom_two"])
    })

    it("drops a chosen correction target when the subject changes", async () => {
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, ({ request }) => {
                const ref = new URL(request.url).searchParams.get("subject_ref")!
                return HttpResponse.json(findingPage([findingRead({ finding_ref: `sef_${ref}` })], { subject_ref: ref }))
            }),
        )
        mount(MANY)
        await screen.findByText("In force")
        await userEvent.selectOptions(screen.getByLabelText("Kind"), "adjudication")
        await userEvent.selectOptions(screen.getByLabelText("Corrects finding"), "sef_calc_one")
        await userEvent.selectOptions(screen.getByLabelText("Findings about"), "geometry:geom_two")
        await waitFor(() => expect(document.getElementById("finding-sef_geom_two")).not.toBeNull())
        expect(screen.getByLabelText("Kind")).toHaveValue("identity_incompatibility")
    })
})

describe("recording an adjudication", () => {
    it("shows Settled by on the corrected finding after the reload", async () => {
        let posted = false
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, () => HttpResponse.json(findingPage(posted
                ? [findingRead({ finding_ref: "sef_old", in_force: false, settled_by_finding_ref: "sef_new" }), findingRead({ finding_ref: "sef_new", kind: "adjudication", supersedes_finding_ref: "sef_old", in_force: false })]
                : [findingRead({ finding_ref: "sef_old" })]))),
            http.post(FINDINGS, () => { posted = true; return HttpResponse.json(findingRead({ finding_ref: "sef_new" }), { status: 201 }) }),
        )
        mount()
        await screen.findByText("In force")
        await userEvent.selectOptions(screen.getByLabelText("Kind"), "adjudication")
        await userEvent.selectOptions(screen.getByLabelText("Corrects finding"), "sef_old")
        fireEvent.change(screen.getByLabelText(/Rationale/), { target: { value: "Settled." } })
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        const link = await screen.findByRole("link", { name: "sef_new" })
        expect(link.closest(".value-pill")?.textContent).toMatch(/Settled by/)
    })
})

describe("removed adjudications and applied effects", () => {
    it("says a finding is in force again when the ruling that settled it was removed", async () => {
        curatorSees([findingRead({ removed_adjudications: [removedAdjudication()] })])
        mount()
        const item = (await screen.findAllByRole("listitem")).find((li) => li.id === "finding-sef_first")!
        const sentence = within(item).getByText(/sef_gone/)
        expect(sentence.textContent).toMatch(/calc_deleted/)
        expect(sentence.textContent).toMatch(/2026-09-02/)
        expect(sentence.textContent).toMatch(/in force again/i)
    })

    it("does not say in force again when another ruling has since settled the finding", async () => {
        curatorSees([findingRead({ in_force: false, settled_by_finding_ref: "sef_next", removed_adjudications: [removedAdjudication()] })])
        mount()
        const item = (await screen.findAllByRole("listitem"))[0]
        const sentence = within(item).getByText(/sef_gone/)
        expect(sentence.textContent).not.toMatch(/in force again/i)
        expect(sentence.textContent).toMatch(/settled by sef_next/i)
    })

    it("words the effect from the verdict and says nothing for a ruling with no effect", async () => {
        curatorSees([
            findingRead({ finding_ref: "sef_a", verdict: "invalidates" }),
            findingRead({ finding_ref: "sef_b", verdict: "unresolved" }),
            findingRead({ finding_ref: "sef_c", kind: "adjudication", role: null, verdict: "does_not_invalidate", applied_kind: "adjudication", applied_role: null, supersedes_finding_ref: "sef_x" }),
        ])
        mount()
        const items = await screen.findAllByRole("listitem")
        const byId = (id: string) => items.find((li) => li.id === `finding-${id}`)!
        expect(within(byId("sef_a")).getByText("Applied as a role invalidation of the energy role.")).toBeInTheDocument()
        expect(within(byId("sef_b")).getByText(/while the verdict is unresolved/)).toBeInTheDocument()
        expect(within(byId("sef_c")).queryByText(/Applied as/)).toBeNull()
    })
})

describe("the form while the list is unavailable", () => {
    it("does not claim nothing is in force when the findings failed to load", async () => {
        server.use(
            http.get(ME, () => HttpResponse.json(user("curator"))),
            http.get(FINDINGS, () => HttpResponse.json({}, { status: 500 })),
        )
        mount()
        await screen.findByRole("alert")
        await userEvent.selectOptions(screen.getByLabelText("Kind"), "adjudication")
        expect(screen.queryByText(/nothing to correct/i)).toBeNull()
        expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument()
    })

    it("says nothing is in force only after a successful empty load", async () => {
        curatorSees([])
        mount()
        await screen.findByText(/No findings have been recorded/)
        await userEvent.selectOptions(screen.getByLabelText("Kind"), "adjudication")
        expect(screen.getByText(/nothing to correct/i)).toBeInTheDocument()
    })
})

describe("keyboard and assistive technology", () => {
    it("moves focus to the first invalid field when the form is submitted incomplete", async () => {
        curatorSees([])
        mount()
        await screen.findByText(/No findings have been recorded/)
        await userEvent.click(screen.getByRole("button", { name: "Record finding" }))
        await waitFor(() => expect(screen.getByLabelText(/Rationale/)).toHaveFocus())
    })

    it("keeps focus on the submit button while a request is in flight", async () => {
        curatorSees([])
        server.use(http.post(FINDINGS, async () => { await delay(60); return HttpResponse.json({}, { status: 500 }) }))
        mount()
        await screen.findByText(/No findings have been recorded/)
        fireEvent.change(screen.getByLabelText(/Rationale/), { target: { value: "Because." } })
        const button = screen.getByRole("button", { name: "Record finding" })
        button.focus()
        await userEvent.keyboard("{Enter}")
        expect(button).toHaveFocus()
        expect(button).toHaveAttribute("aria-disabled", "true")
        await screen.findByRole("alert")
        expect(button).toHaveFocus()
    })
})
