import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import CuratorQueuePage from "./CuratorQueuePage"
import { AuthProvider } from "../components/AuthProvider"

/**
 * The curator queue is the first surface in this app where a person acts
 * on machine-review findings, and the thing it must never do is imply it
 * touched the science. These tests pin three groups of behaviour:
 *
 * 1. Who gets in (admin only, and an unreachable backend is not "not an
 *    admin").
 * 2. What a row says -- the record is named by its PUBLIC ref and linked
 *    only when a page exists for that type, never by row id.
 * 3. What an action sends and what the page does with the answer. The
 *    write routes each return the whole updated task, so a row is
 *    replaced from the server's answer rather than patched from a guess.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const QUEUE = "/api/v1/admin/machine-review/curator-tasks"

const admin = {
    id: 1,
    username: "calvin",
    email: "calvin@example.com",
    full_name: "Calvin Pieters",
    role: "admin",
    is_active: true,
}
const plainUser = { ...admin, id: 2, username: "noah", full_name: null, role: "user" }

function meIs(who: Record<string, unknown>) {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(who)))
}

/** One task, shaped like `AdminCuratorTaskResponse`. */
function task(over: Record<string, unknown> = {}) {
    return {
        id: 41,
        submission_id: 7,
        record_type: "species",
        record_public_ref: "spc_vu7cuk4s37szxaudjpf355tqda",
        record_id: 431,
        finding_fingerprint: "abc123",
        workflow_state: "needs_curator_review",
        machine_review_status: "machine_screened_warning",
        highest_severity: "warning",
        findings_count: 2,
        source_audit_event_id: 11,
        assigned_to: null,
        created_at: "2026-09-14T10:00:00Z",
        updated_at: "2026-09-14T10:00:00Z",
        resolved_at: null,
        resolved_by: null,
        resolution_note: null,
        ...over,
    }
}

function queueIs(items: Record<string, unknown>[]) {
    server.use(
        http.get(QUEUE, () =>
            HttpResponse.json({ items, total: items.length, skip: 0, limit: 50 }),
        ),
    )
}

function renderPage() {
    return render(
        <AuthProvider>
            <MemoryRouter>
                <CuratorQueuePage />
            </MemoryRouter>
        </AuthProvider>,
    )
}

/** The cells of the one body row, in column order. */
function onlyRowCells(): string[] {
    const table = screen.getByRole("table")
    const row = within(table).getAllByRole("row").at(-1)!
    return within(row).getAllByRole("cell").map((c) => (c.textContent ?? "").trim())
}

/**
 * The state shown in the one body row.
 *
 * Scoped to the table on purpose: every state label also appears as an
 * `<option>` in the filter, so a bare `getByText("needs review")` matches
 * the dropdown as well and cannot tell a changed row from an unchanged
 * one.
 */
async function rowState(): Promise<string> {
    const table = await screen.findByRole("table")
    const row = within(table).getAllByRole("row").at(-1)!
    return (within(row).getAllByRole("cell")[3].textContent ?? "").trim()
}

describe("who can open the queue", () => {
    it("a signed-in non-admin is told, and the queue is never fetched", () => {
        meIs(plainUser)
        // Deliberately no QUEUE handler: `onUnhandledRequest: "error"` turns
        // any request into a failure, so this asserts the page does not ask,
        // not merely that it hid the answer.
        renderPage()

        return screen.findByRole("alert").then((alert) => {
            expect(alert).toHaveTextContent(/for administrators/i)
            expect(screen.queryByRole("table")).not.toBeInTheDocument()
        })
    })

    it("a backend outage does not render as 'you are not an admin'", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.error()))
        renderPage()

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be reached/i)
        expect(alert).not.toHaveTextContent(/for administrators/i)
    })
})

describe("what the page says it is", () => {
    it("states on the page that closing a task approves no science", async () => {
        meIs(admin)
        queueIs([])
        renderPage()

        // ADR 0016's separation, said where a curator reads it rather than
        // only in a doc. A button labelled "Close" on a queue of findings
        // invites the opposite reading.
        const lede = await screen.findByText(/nothing here\s+approves science/i)
        expect(lede).toBeInTheDocument()
    })

    it("an empty queue reads as 'nothing waiting', not as a failure", async () => {
        meIs(admin)
        queueIs([])
        renderPage()

        expect(await screen.findByText(/Nothing is waiting/i)).toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })
})

describe("how a row names the record it concerns", () => {
    it("links a linkable type to its page, by public ref", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const link = await screen.findByRole("link", {
            name: "spc_vu7cuk4s37szxaudjpf355tqda",
        })
        expect(link).toHaveAttribute("href", "/species/spc_vu7cuk4s37szxaudjpf355tqda")

        // The row id is admin-visible data on the wire but is NOT what the
        // curator is pointed at. If it ever reaches the cell, this fails.
        expect(onlyRowCells()[0]).not.toContain("431")
    })

    it("shows the ref as plain text for a type with no page", async () => {
        meIs(admin)
        queueIs([task({ record_type: "thermo", record_public_ref: "thm_abc" })])
        renderPage()

        expect(await screen.findByText("thm_abc")).toBeInTheDocument()
        // A guessed link that 404s is worse than no link: a curator who
        // lands nowhere learns to stop clicking.
        expect(screen.queryByRole("link", { name: "thm_abc" })).not.toBeInTheDocument()
    })

    it("says so when the backend could not name the record", async () => {
        meIs(admin)
        queueIs([
            task({ record_type: "applied_energy_correction", record_public_ref: null }),
        ])
        renderPage()

        expect(await screen.findByText(/cannot be named/i)).toBeInTheDocument()
        // Not the row id as a fallback -- that is the defect #479 fixed on
        // the inspection page, and it must not reappear here.
        expect(screen.queryByText("431")).not.toBeInTheDocument()
    })

    it("carries severity and finding count, which is what the queue is scanned for", async () => {
        meIs(admin)
        queueIs([task({ highest_severity: "critical", findings_count: 3 })])
        renderPage()

        await screen.findByRole("table")
        const cells = onlyRowCells()
        expect(cells[1]).toBe("critical")
        expect(cells[2]).toBe("3")
    })
})

describe("the default view is the work, not the history", () => {
    it("hides terminal tasks until asked for them", async () => {
        meIs(admin)
        queueIs([
            task({ id: 41, record_public_ref: "spc_open" }),
            task({
                id: 42,
                record_public_ref: "spc_closed",
                workflow_state: "resolved_no_action",
            }),
        ])
        renderPage()

        expect(await screen.findByText("spc_open")).toBeInTheDocument()
        expect(screen.queryByText("spc_closed")).not.toBeInTheDocument()

        const user = userEvent.setup()
        await user.selectOptions(screen.getByLabelText("Showing"), "all")
        expect(await screen.findByText("spc_closed")).toBeInTheDocument()
    })

    it("asks the backend to filter when one exact state is chosen", async () => {
        meIs(admin)
        const asked: string[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("workflow_state") ?? "")
                return HttpResponse.json({ items: [], total: 0, skip: 0, limit: 50 })
            }),
        )
        renderPage()
        await screen.findByText(/Nothing is waiting/i)

        const user = userEvent.setup()
        await user.selectOptions(screen.getByLabelText("Showing"), "in_curator_review")
        await screen.findByText(/No tasks match/i)

        // "open" is three states and the route filters on one, so only an
        // exact state becomes a query parameter. Sending `workflow_state=open`
        // would be a 422, and filtering client-side for a single state would
        // quietly drop work past the page limit.
        expect(asked).toEqual(["", "in_curator_review"])
    })
})

describe("acting on a task", () => {
    it("starting a review sends no actor, and shows the state the server returned", async () => {
        meIs(admin)
        queueIs([task()])
        let body: unknown = "not called"
        server.use(
            http.post(`${QUEUE}/41/start-review`, async ({ request }) => {
                body = await request.json()
                return HttpResponse.json(task({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Start review" }))

        await waitFor(async () => expect(await rowState()).toBe("in review"))
        // Empty on purpose: the backend defaults the actor to the
        // authenticated admin. Letting the page name an `actor_user_id`
        // would let it claim somebody else did the work.
        expect(body).toEqual({})
        // A task already in review has nothing to start.
        expect(screen.queryByRole("button", { name: "Start review" })).not.toBeInTheDocument()
    })

    it("will not close a task without a reason", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))

        // No `/resolve` handler is registered: if the disabled button were
        // clickable the request would fail the unhandled-request guard.
        expect(screen.getByRole("button", { name: "Close task" })).toBeDisabled()
        await user.type(screen.getByLabelText(/Why/), "   ")
        expect(screen.getByRole("button", { name: "Close task" })).toBeDisabled()
    })

    it("sends the chosen resolution and the note, and drops the row from the open queue", async () => {
        meIs(admin)
        queueIs([task()])
        let body: Record<string, unknown> = {}
        server.use(
            http.post(`${QUEUE}/41/resolve`, async ({ request }) => {
                body = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(
                    task({
                        workflow_state: "dismissed_machine_finding",
                        resolution_note: "the machine misread the geometry",
                    }),
                )
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        await user.selectOptions(
            screen.getByLabelText(/How it was settled/),
            "dismissed_machine_finding",
        )
        await user.type(screen.getByLabelText(/Why/), "the machine misread the geometry")
        await user.click(screen.getByRole("button", { name: "Close task" }))

        await screen.findByText(/Nothing is waiting/i)
        expect(body).toEqual({
            resolution_state: "dismissed_machine_finding",
            resolution_note: "the machine misread the geometry",
        })
        // The row is gone rather than sitting there in a state the filter
        // excludes, which reads as "my change did not take".
        expect(screen.queryByText("spc_vu7cuk4s37szxaudjpf355tqda")).not.toBeInTheDocument()
    })

    it("spells out what each way of closing a task asserts", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        await user.selectOptions(
            screen.getByLabelText(/How it was settled/),
            "resolved_human_reviewed",
        )

        // The trap this wording exists for: "reviewed" here NOTES that a
        // review happened elsewhere. It writes no `record_review` and
        // endorses nothing.
        expect(screen.getByText(/does not record that review/i)).toBeInTheDocument()
    })

    it("a refused action says nothing changed, and the row does not move", async () => {
        meIs(admin)
        queueIs([task()])
        server.use(
            http.post(`${QUEUE}/41/start-review`, () =>
                HttpResponse.json(
                    { code: "curator_task_state_conflict", detail: "Task is already resolved." },
                    { status: 409 },
                ),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Start review" }))

        expect(await screen.findByRole("alert")).toHaveTextContent("Task is already resolved.")
        expect(await rowState()).toBe("needs review")
    })

    it("a terminal task can be reopened", async () => {
        meIs(admin)
        queueIs([task({ workflow_state: "resolved_no_action" })])
        server.use(
            http.post(`${QUEUE}/41/reopen`, () =>
                HttpResponse.json(task({ workflow_state: "needs_curator_review" })),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        await user.click(await screen.findByRole("button", { name: "Reopen" }))

        await waitFor(async () => expect(await rowState()).toBe("needs review"))
    })

    it("a queue that will not load is reported, not shown as empty", async () => {
        meIs(admin)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({ code: "internal_error", detail: "Backend is down." }, { status: 500 }),
            ),
        )
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent("Backend is down.")
        expect(screen.queryByText(/Nothing is waiting/i)).not.toBeInTheDocument()
    })
})
