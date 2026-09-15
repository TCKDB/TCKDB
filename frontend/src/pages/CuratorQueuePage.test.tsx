import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import CuratorQueuePage from "./CuratorQueuePage"
import { AuthProvider } from "../components/AuthProvider"

/**
 * Machine findings is the first surface in this app where a person acts
 * on machine-review findings, and the thing it must never do is imply it
 * touched the science. These tests pin four groups of behaviour:
 *
 * 1. Who gets in (admin only, and an unreachable backend is not "not an
 *    admin").
 * 2. What a row says -- the record is named by its PUBLIC ref and linked
 *    only when a page exists for that type, never by any row id.
 * 3. What an action sends, and that everything a row owns stays with that
 *    row: its typed reason, its in-flight state, its refusal.
 * 4. What happens when the archive says something this build does not
 *    understand.
 *
 * **Most tests here use TWO rows on purpose.** A single-row table cannot
 * tell "this belongs to the row" from "this is a page-level value", and
 * the page-level version of the close form silently attached one record's
 * written justification to another record's audit trail.
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

/**
 * One task, shaped like `AdminCuratorTaskResponse`.
 *
 * `record_id` and `submission_id` are deliberately large and unlike any
 * other number on the page, so a test asserting they are absent cannot be
 * satisfied by coincidence with a findings count.
 */
function task(over: Record<string, unknown> = {}) {
    return {
        id: 41,
        submission_id: 123456,
        record_type: "species",
        record_public_ref: "spc_vu7cuk4s37szxaudjpf355tqda",
        record_id: 987654,
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

/** The second row of every two-row test. */
function otherTask(over: Record<string, unknown> = {}) {
    return task({
        id: 42,
        record_type: "calculation",
        record_public_ref: "calc_7k2mq9x4ta8ndrwe5hvzcbj6y1",
        ...over,
    })
}

/** Serve a fixed queue. Returns a counter of how many times it was read. */
function queueIs(items: Record<string, unknown>[]): { reads: number } {
    const counter = { reads: 0 }
    server.use(
        http.get(QUEUE, () => {
            counter.reads += 1
            return HttpResponse.json({ items, total: items.length, skip: 0, limit: 50 })
        }),
    )
    return counter
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

/** All body rows, as arrays of cell text in column order. */
function bodyRows(): string[][] {
    const table = screen.getByRole("table")
    return within(table)
        .getAllByRole("row")
        .slice(1)
        .map((row) =>
            within(row)
                .getAllByRole("cell")
                .map((c) => (c.textContent ?? "").trim()),
        )
}

/**
 * The state cell of the row whose record ref is `ref`.
 *
 * Found by ref rather than by position, and scoped to the table: every
 * state label also appears as an `<option>` in the filter, so a bare
 * `getByText("needs review")` matches the dropdown too and cannot tell a
 * changed row from an unchanged one.
 */
async function rowStateFor(ref: string): Promise<string> {
    const table = await screen.findByRole("table")
    const row = within(table)
        .getAllByRole("row")
        .find((r) => (r.textContent ?? "").includes(ref))
    if (row === undefined) throw new Error(`no row for ${ref}`)
    return (within(row).getAllByRole("cell")[3].textContent ?? "").trim()
}

/** The buttons inside the row whose record ref is `ref`. */
function rowButtons(ref: string): HTMLElement[] {
    const table = screen.getByRole("table")
    const row = within(table)
        .getAllByRole("row")
        .find((r) => (r.textContent ?? "").includes(ref))
    if (row === undefined) throw new Error(`no row for ${ref}`)
    return within(row).getAllByRole("button")
}

function rowButton(ref: string, name: string): HTMLElement {
    const found = rowButtons(ref).find((b) => (b.textContent ?? "").trim() === name)
    if (found === undefined) throw new Error(`no "${name}" button in row ${ref}`)
    return found
}

/**
 * A promise the test releases by hand.
 *
 * Holding a request open with `setTimeout` makes a test a race between
 * that delay and however long the rest of the interaction takes. Under
 * the full parallel suite this file lost that race: "a row still waiting
 * on its own write is not re-enabled by another row finishing" failed
 * about two runs in three while passing every time on its own. A gate
 * the test opens deliberately takes the timing out of the question.
 */
function gate(): { held: Promise<void>; release: () => void } {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
        release = resolve
    })
    return { held, release }
}

describe("who can open the queue", () => {
    it("a signed-in non-admin is told, and the queue is never fetched", async () => {
        meIs(plainUser)
        // Deliberately no QUEUE handler: `onUnhandledRequest: "error"` turns
        // any request into a failure, so this asserts the page does not ask,
        // not merely that it hid the answer.
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent(/for administrators/i)
        expect(screen.queryByRole("table")).not.toBeInTheDocument()
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
    it("is titled Machine findings, and never by its old name", async () => {
        /**
         * The rename is the fix for the actual complaint: the owner could
         * not tell this page from `/review-queue` because "Curator queue"
         * and "Review queue" both named an audience rather than a subject.
         *
         * Both halves are asserted. The heading alone would still pass with
         * the old name left lying in the lede or an empty state, and a
         * half-renamed pair is exactly as unreadable as the original one.
         */
        meIs(admin)
        queueIs([])
        renderPage()

        expect(await screen.findByRole("heading", { name: "Machine findings", level: 1 })).toBeInTheDocument()
        expect(screen.queryByText(/curator queue/i)).not.toBeInTheDocument()
    })

    it("states on the page that closing a task approves no science", async () => {
        meIs(admin)
        queueIs([])
        renderPage()

        // ADR 0016's separation, said where a curator reads it rather than
        // only in a doc. A button labelled "Close" on a list of findings
        // invites the opposite reading.
        expect(
            await screen.findByText(/nothing\s+here endorses any science/i),
        ).toBeInTheDocument()
        // And the same sentence has to say what closing one DOES mean,
        // since "advisory" alone leaves the reader to guess.
        expect(
            screen.getByText(/never that the record is good/i),
        ).toBeInTheDocument()
    })

    it("an empty queue reads as 'nothing waiting', not as a failure", async () => {
        meIs(admin)
        queueIs([])
        renderPage()

        expect(await screen.findByText(/Nothing is waiting/i)).toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    it("counts what it is showing, without comparing it to a different count", async () => {
        meIs(admin)
        // The backend's `total` counts every task, closed ones included,
        // because the open filter sends no `workflow_state`. Printing
        // "1 of 9" here would compare a filtered table against an
        // unfiltered count and read as though 8 rows were missing.
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({ items: [task()], total: 9, skip: 0, limit: 50 }),
            ),
        )
        renderPage()

        const count = await screen.findByText(/shown/)
        expect(count).toHaveTextContent("1 shown")
        expect(count).not.toHaveTextContent("9")
        // And a one-row table is not a full page, so it must not claim
        // work might be hidden.
        expect(screen.queryByText(/there may be more/i)).not.toBeInTheDocument()
    })

    it("does compare against the total when the backend counted the same thing", async () => {
        meIs(admin)
        server.use(
            http.get(QUEUE, ({ request }) => {
                const filtered = new URL(request.url).searchParams.get("workflow_state")
                return HttpResponse.json({
                    items: filtered ? [task({ workflow_state: "untriaged" })] : [],
                    total: filtered ? 4 : 0,
                    skip: 0,
                    limit: 50,
                })
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText(/Nothing is waiting/i)
        await user.selectOptions(screen.getByLabelText("Showing"), "untriaged")

        // Under an exact state the backend counts exactly what the table
        // shows, so "of 4" is a real statement about work not on screen.
        expect(await screen.findByText(/shown of 4 in this state/)).toBeInTheDocument()
    })

    it("still warns about a full page when some of its rows were unreadable", async () => {
        meIs(admin)
        const rows = Array.from({ length: 50 }, (_, i) =>
            i < 2
                ? task({ id: 900 + i, workflow_state: "a_state_from_the_future" })
                : task({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({ items: rows, total: 200, skip: 0, limit: 50 }),
            ),
        )
        renderPage()

        // 48 rendered of 50 fetched. Comparing the RENDERED count against the
        // page cap would drop the hint exactly when rows are being hidden.
        await screen.findByRole("table")
        expect(screen.getByText(/there may be more/i)).toBeInTheDocument()
    })

    it("says so when a full page might be hiding more work", async () => {
        meIs(admin)
        const fifty = Array.from({ length: 50 }, (_, i) =>
            task({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({ items: fifty, total: 200, skip: 0, limit: 50 }),
            ),
        )
        renderPage()

        expect(await screen.findByText(/there may be more/i)).toBeInTheDocument()
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
    })

    it("puts no internal row id anywhere in the table", async () => {
        meIs(admin)
        // Includes the row that cannot be named: that is where a row id is
        // most tempting as a fallback, and where #479 put one last time.
        queueIs([
            task(),
            otherTask(),
            task({
                id: 43,
                record_type: "applied_energy_correction",
                record_public_ref: null,
                record_id: 987654,
            }),
        ])
        renderPage()

        const table = await screen.findByRole("table")
        // The whole table, markup included: a row id must not reach a cell,
        // a link, a title, or an aria-label. Scanning outerHTML rather than
        // textContent is what makes the attribute cases count.
        const markup = table.outerHTML
        expect(markup).not.toContain("987654") // record_id
        expect(markup).not.toContain("123456") // submission_id, also a row id
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
        queueIs([task({ record_type: "applied_energy_correction", record_public_ref: null })])
        renderPage()

        expect(await screen.findByText(/cannot be named/i)).toBeInTheDocument()
    })

    it("carries severity and finding count, which is what the queue is scanned for", async () => {
        meIs(admin)
        queueIs([task({ highest_severity: "critical", findings_count: 3 })])
        renderPage()

        await screen.findByRole("table")
        const cells = bodyRows()[0]
        expect(cells[1]).toBe("critical")
        expect(cells[2]).toBe("3")
    })

    it("gives each terminal state its own words, and never collapses them", async () => {
        meIs(admin)
        queueIs([
            task({ id: 41, record_public_ref: "spc_a", workflow_state: "resolved_no_action" }),
            task({ id: 42, record_public_ref: "spc_b", workflow_state: "resolved_human_reviewed" }),
            task({ id: 43, record_public_ref: "spc_c", workflow_state: "dismissed_machine_finding" }),
        ])
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")

        // Three distinct labels, each asserted by value. Collapsing any two
        // of them loses the difference between "the machine was wrong" and
        // "a person reviewed the record".
        await waitFor(async () => expect(await rowStateFor("spc_a")).toBe("closed: no action"))
        expect(await rowStateFor("spc_b")).toBe("closed: reviewed elsewhere")
        expect(await rowStateFor("spc_c")).toBe("dismissed")
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
                server.use(
                    http.get(QUEUE, () =>
                        HttpResponse.json({
                            items: [task({ workflow_state: "in_curator_review" })],
                            total: 1,
                            skip: 0,
                            limit: 50,
                        }),
                    ),
                )
                return HttpResponse.json(task({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Start review" }))

        await waitFor(async () =>
            expect(await rowStateFor("spc_vu7cuk4s37szxaudjpf355tqda")).toBe("in review"),
        )
        // Empty on purpose: the backend defaults the actor to the
        // authenticated admin. Letting the page name an `actor_user_id`
        // would let it claim somebody else did the work.
        expect(body).toEqual({})
        // A task already in review has nothing to start.
        expect(screen.queryByRole("button", { name: "Start review" })).not.toBeInTheDocument()
    })

    it("re-reads the queue from the server after a write", async () => {
        meIs(admin)
        const counter = queueIs([task()])
        server.use(http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())))
        renderPage()
        await screen.findByRole("table")
        const before = counter.reads

        const user = userEvent.setup()
        await user.click(screen.getByRole("button", { name: "Start review" }))

        // The server, not the page, decides whether the row still belongs
        // in this view. Patching the row locally duplicated that judgement
        // at the write site, where it read a filter captured before the
        // request went out.
        await waitFor(() => expect(counter.reads).toBeGreaterThan(before))
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
                server.use(
                    http.get(QUEUE, () =>
                        HttpResponse.json({
                            items: [
                                task({ workflow_state: "dismissed_machine_finding" }),
                            ],
                            total: 1,
                            skip: 0,
                            limit: 50,
                        }),
                    ),
                )
                return HttpResponse.json(task({ workflow_state: "dismissed_machine_finding" }))
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
    })

    it("closes the form once the task is closed", async () => {
        meIs(admin)
        queueIs([task({ workflow_state: "needs_curator_review" })])
        server.use(
            http.post(`${QUEUE}/41/resolve`, () => {
                server.use(
                    http.get(QUEUE, () =>
                        HttpResponse.json({
                            items: [task()],
                            total: 1,
                            skip: 0,
                            limit: 50,
                        }),
                    ),
                )
                return HttpResponse.json(task({ workflow_state: "resolved_no_action" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        await user.type(screen.getByLabelText(/Why/), "fine as deposited")
        await user.click(screen.getByRole("button", { name: "Close task" }))

        // Leaving a filled-in form open under a task that has just been
        // closed invites a second submission of the same reason.
        await waitFor(() =>
            expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument(),
        )
    })

    it("spells out what each way of closing a task asserts", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        const choice = screen.getByLabelText(/How it was settled/)

        expect(screen.getByText(/nothing needs changing/i)).toBeInTheDocument()
        await user.selectOptions(choice, "resolved_human_reviewed")
        // The trap this wording exists for: "reviewed" here NOTES that a
        // review happened elsewhere. It writes no `record_review` and
        // endorses nothing.
        expect(screen.getByText(/does not record that review/i)).toBeInTheDocument()
        await user.selectOptions(choice, "dismissed_machine_finding")
        expect(screen.getByText(/false positive, or not actionable/i)).toBeInTheDocument()
    })

    it("a terminal task can be reopened", async () => {
        meIs(admin)
        queueIs([task({ workflow_state: "resolved_no_action" })])
        server.use(
            http.post(`${QUEUE}/41/reopen`, () => {
                server.use(
                    http.get(QUEUE, () =>
                        HttpResponse.json({
                            items: [task({ workflow_state: "needs_curator_review" })],
                            total: 1,
                            skip: 0,
                            limit: 50,
                        }),
                    ),
                )
                return HttpResponse.json(task({ workflow_state: "needs_curator_review" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        await user.click(await screen.findByRole("button", { name: "Reopen" }))

        await waitFor(async () =>
            expect(await rowStateFor("spc_vu7cuk4s37szxaudjpf355tqda")).toBe("needs review"),
        )
    })
})

describe("a view changed mid-flight is the view that wins", () => {
    it("re-reads under the filter in force when the write lands, not the one it started under", async () => {
        meIs(admin)
        const asked: (string | null)[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("workflow_state"))
                return HttpResponse.json({ items: [task()], total: 1, skip: 0, limit: 50 })
            }),
            http.post(`${QUEUE}/41/start-review`, async () => {
                await new Promise((resolve) => setTimeout(resolve, 150))
                return HttpResponse.json(task({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        // Start a slow write under "open", then change the view while it
        // is still out. The refresh that follows must ask about the view
        // the curator is actually looking at.
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))
        await user.selectOptions(screen.getByLabelText("Showing"), "untriaged")

        await waitFor(() => expect(asked.length).toBeGreaterThanOrEqual(3))
        expect(asked.at(-1)).toBe("untriaged")
    })

    it("a superseded answer that FAILS does not replace the view that replaced it", async () => {
        meIs(admin)
        const abandoned = gate()
        let call = 0
        server.use(
            http.get(QUEUE, async ({ request }) => {
                call += 1
                const state = new URL(request.url).searchParams.get("workflow_state")
                if (call === 1) {
                    // The abandoned "open" view answers last, and answers 500.
                    await abandoned.held
                    return HttpResponse.json(
                        { code: "internal_error", detail: "Backend is down." },
                        { status: 500 },
                    )
                }
                return HttpResponse.json({
                    items: [task({ record_public_ref: "spc_current", workflow_state: state === null ? "untriaged" : "untriaged" })],
                    total: 1,
                    skip: 0,
                    limit: 50,
                })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.selectOptions(await screen.findByLabelText("Showing"), "untriaged")
        expect(await screen.findByText("spc_current")).toBeInTheDocument()

        // The failure half of the sequence guard. Without it, a slow 500 on a
        // view nobody is looking at replaces a good table with "Could not
        // load the machine findings." Released deliberately rather than slept
        // past, so the assertion cannot run before the damage would land.
        abandoned.release()
        await waitFor(() => expect(screen.getByText("spc_current")).toBeInTheDocument())
        expect(screen.queryByText(/Could not load/i)).not.toBeInTheDocument()
    })

    it("a slow answer does not overwrite the view that replaced it", async () => {
        meIs(admin)
        let call = 0
        server.use(
            http.get(QUEUE, async ({ request }) => {
                call += 1
                const state = new URL(request.url).searchParams.get("workflow_state")
                // The first request (the "open" view) answers last.
                if (call === 1) await new Promise((r) => setTimeout(r, 200))
                return HttpResponse.json({
                    items: [
                        task({
                            record_public_ref: state === null ? "spc_stale" : "spc_current",
                            workflow_state: state === null ? "untriaged" : "untriaged",
                        }),
                    ],
                    total: 1,
                    skip: 0,
                    limit: 50,
                })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.selectOptions(await screen.findByLabelText("Showing"), "untriaged")
        expect(await screen.findByText("spc_current")).toBeInTheDocument()

        // Give the superseded request time to come back and do damage.
        await new Promise((r) => setTimeout(r, 300))
        expect(screen.queryByText("spc_stale")).not.toBeInTheDocument()
        expect(screen.getByText("spc_current")).toBeInTheDocument()
    })
})

describe("everything a row owns stays with that row", () => {
    it("does not carry one record's reason over to another record", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        let sent: Record<string, unknown> = {}
        server.use(
            http.post(`${QUEUE}/42/resolve`, async ({ request }) => {
                sent = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(otherTask({ workflow_state: "resolved_no_action" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Close…"))
        await user.type(
            screen.getByLabelText(/Why/),
            "the species geometry is fine, the machine misread it",
        )

        // Change of mind: close that form, open the other row's.
        await user.click(rowButton("calc_7k2mq9x4ta8ndrwe5hvzcbj6y1", "Close…"))

        // The reason written about the species must not be sitting in the
        // calculation's form, already submittable. `resolution_note` is the
        // record of why THIS finding stopped mattering.
        expect(screen.getByLabelText(/Why/)).toHaveValue("")
        expect(screen.getByRole("button", { name: "Close task" })).toBeDisabled()

        await user.type(screen.getByLabelText(/Why/), "the calculation converged")
        await user.click(screen.getByRole("button", { name: "Close task" }))
        await waitFor(() => expect(sent.resolution_note).toBe("the calculation converged"))
    })

    it("does not carry one row's chosen resolution over either", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Close…"))
        await user.selectOptions(
            screen.getByLabelText(/How it was settled/),
            "dismissed_machine_finding",
        )
        await user.click(rowButton("calc_7k2mq9x4ta8ndrwe5hvzcbj6y1", "Close…"))

        expect(screen.getByLabelText(/How it was settled/)).toHaveValue("resolved_no_action")
    })

    it("only one row's form is open at a time", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Close…"))
        expect(screen.getAllByLabelText(/Why/)).toHaveLength(1)
        await user.click(rowButton("calc_7k2mq9x4ta8ndrwe5hvzcbj6y1", "Close…"))
        expect(screen.getAllByLabelText(/Why/)).toHaveLength(1)
    })

    it("a refusal is shown under the row it refused, and nowhere else", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        server.use(
            http.post(`${QUEUE}/41/start-review`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Task is already resolved." },
                    { status: 400 },
                ),
            ),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))

        expect(await screen.findByRole("alert")).toHaveTextContent("Task is already resolved.")
        expect(screen.getAllByRole("alert")).toHaveLength(1)
    })

    it("re-reads a refused row, because a refusal usually means it moved", async () => {
        meIs(admin)
        // The other admin's dismissal becomes visible only once our write
        // has been refused -- which is exactly the ordering that produces
        // the stale row.
        let refused = false
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({
                    items: [
                        refused
                            ? task({ workflow_state: "dismissed_machine_finding" })
                            : task(),
                    ],
                    total: 1,
                    skip: 0,
                    limit: 50,
                }),
            ),
            http.post(`${QUEUE}/41/start-review`, () => {
                refused = true
                return HttpResponse.json(
                    {
                        code: "domain_error",
                        detail: "Cannot start review on a resolved curator task.",
                    },
                    { status: 400 },
                )
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))

        // An earlier version left the row reading "needs review" with a live
        // "Start review" button under a task the server had already closed --
        // and a test asserted that as correct. A state conflict is precisely
        // the case where the row on screen is the row that is now wrong.
        expect(await screen.findByRole("alert")).toHaveTextContent(/Cannot start review/)
        await waitFor(async () =>
            expect(await rowStateFor("spc_vu7cuk4s37szxaudjpf355tqda")).toBe("dismissed"),
        )
    })

    it("keeps each row's own refusal, instead of one slot they overwrite", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        server.use(
            http.post(`${QUEUE}/41/start-review`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Species task is stuck." },
                    { status: 400 },
                ),
            ),
            http.post(`${QUEUE}/42/start-review`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Calculation task is stuck." },
                    { status: 400 },
                ),
            ),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))
        await screen.findByText("Species task is stuck.")
        await user.click(rowButton("calc_7k2mq9x4ta8ndrwe5hvzcbj6y1", "Start review"))
        await screen.findByText("Calculation task is stuck.")

        // Acting on the second row must not silently erase the first row's
        // refusal, which is still true and still unaddressed.
        expect(screen.getByText("Species task is stuck.")).toBeInTheDocument()
    })

    it("clears a refusal once the same row succeeds", async () => {
        meIs(admin)
        queueIs([task()])
        let attempts = 0
        server.use(
            http.post(`${QUEUE}/41/start-review`, () => {
                attempts += 1
                if (attempts === 1) {
                    return HttpResponse.json(
                        { code: "domain_error", detail: "Someone else has it." },
                        { status: 400 },
                    )
                }
                return HttpResponse.json(task({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        const ref = "spc_vu7cuk4s37szxaudjpf355tqda"
        await user.click(await screen.findByRole("button", { name: "Start review" }))
        expect(await screen.findByRole("alert")).toHaveTextContent("Someone else has it.")

        await user.click(rowButton(ref, "Start review"))

        // A refusal left on screen under a row that has since succeeded
        // tells the curator the opposite of what happened.
        await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
    })

    it("a row still waiting on its own write is not re-enabled by another row finishing", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        const slow = gate()
        let slowHits = 0
        server.use(
            http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())),
            http.post(`${QUEUE}/42/start-review`, async () => {
                slowHits += 1
                await slow.held
                return HttpResponse.json(otherTask({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        const slowRow = "calc_7k2mq9x4ta8ndrwe5hvzcbj6y1"
        await user.click(rowButton(slowRow, "Start review"))
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))

        // The fast row's write has landed. If in-flight state were a single
        // page-level slot, that would have re-enabled the slow row's button
        // and a second click would send a second write.
        await waitFor(() => expect(rowButton(slowRow, "Start review")).toBeDisabled())
        expect(slowHits).toBe(1)

        // Release, so nothing is left in flight when the test ends.
        slow.release()
        await waitFor(() => expect(rowButton(slowRow, "Start review")).toBeEnabled())
    })

    it("disables a row's own buttons while its write is in flight", async () => {
        meIs(admin)
        queueIs([task()])
        const write = gate()
        server.use(
            http.post(`${QUEUE}/41/start-review`, async () => {
                await write.held
                return HttpResponse.json(task({ workflow_state: "in_curator_review" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        const ref = "spc_vu7cuk4s37szxaudjpf355tqda"
        await user.click(rowButton(ref, "Start review"))
        expect(rowButton(ref, "Close…")).toBeDisabled()

        write.release()
        await waitFor(() => expect(rowButton(ref, "Close…")).toBeEnabled())
    })
})

describe("the buttons say what is actually happening", () => {
    it("keeps a row disabled until the re-read lands, not merely until the write returns", async () => {
        meIs(admin)
        let slowReads = 0
        server.use(
            http.get(QUEUE, async () => {
                slowReads += 1
                // Only the post-write read is slow.
                if (slowReads > 1) await new Promise((r) => setTimeout(r, 150))
                return HttpResponse.json({ items: [task()], total: 1, skip: 0, limit: 50 })
            }),
            http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        const ref = "spc_vu7cuk4s37szxaudjpf355tqda"
        await user.click(rowButton(ref, "Start review"))

        // Between the write returning and the list being re-read, the row on
        // screen is stale. Re-enabling there offers a button whose label
        // describes a state the server has already left.
        expect(rowButton(ref, "Start review")).toBeDisabled()
        await waitFor(() => expect(rowButton(ref, "Start review")).toBeEnabled())
    })

    it("does not blank the table while a write is being re-read", async () => {
        meIs(admin)
        server.use(
            http.get(QUEUE, async () => {
                await new Promise((r) => setTimeout(r, 80))
                return HttpResponse.json({ items: [task()], total: 1, skip: 0, limit: 50 })
            }),
            http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))

        // Unmounting the table to "Loading..." after every action throws away
        // scroll position, focus, and any other row's open form.
        expect(screen.getByRole("table")).toBeInTheDocument()
    })

    it("disables Reopen while its own write is out", async () => {
        meIs(admin)
        queueIs([task({ workflow_state: "resolved_no_action" })])
        server.use(
            http.post(`${QUEUE}/41/reopen`, async () => {
                await new Promise((r) => setTimeout(r, 120))
                return HttpResponse.json(task())
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")

        const ref = "spc_vu7cuk4s37szxaudjpf355tqda"
        await user.click(rowButton(ref, "Reopen"))
        expect(rowButton(ref, "Reopen")).toBeDisabled()
    })

    it("disables Close task while its own write is out", async () => {
        meIs(admin)
        queueIs([task()])
        server.use(
            http.post(`${QUEUE}/41/resolve`, async () => {
                await new Promise((r) => setTimeout(r, 120))
                return HttpResponse.json(task({ workflow_state: "resolved_no_action" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        await user.type(screen.getByLabelText(/Why/), "fine as deposited")
        await user.click(screen.getByRole("button", { name: "Close task" }))

        // Without this a second click files the same reason twice.
        expect(screen.getByRole("button", { name: "Close task" })).toBeDisabled()
    })

    it("Close… is a disclosure, and says so to a screen reader", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const user = userEvent.setup()
        const close = await screen.findByRole("button", { name: "Close…" })
        expect(close).toHaveAttribute("aria-expanded", "false")
        await user.click(close)
        expect(close).toHaveAttribute("aria-expanded", "true")
        expect(close).toHaveAttribute("aria-controls", "resolve-form-41")
    })

    it("clicking Close… again collapses the form rather than blanking it", async () => {
        meIs(admin)
        queueIs([task()])
        renderPage()

        const user = userEvent.setup()
        const close = await screen.findByRole("button", { name: "Close…" })
        await user.click(close)
        await user.type(screen.getByLabelText(/Why/), "some reason")
        await user.click(close)

        expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument()
    })
})

describe("one row's action leaves the other rows alone", () => {
    it("does not close another row's open form", async () => {
        meIs(admin)
        queueIs([task(), otherTask()])
        server.use(http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())))
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("calc_7k2mq9x4ta8ndrwe5hvzcbj6y1", "Close…"))
        await user.type(screen.getByLabelText(/Why/), "the calculation converged")
        await user.click(rowButton("spc_vu7cuk4s37szxaudjpf355tqda", "Start review"))

        // The species row's write must not throw away the reason typed
        // against the calculation.
        await waitFor(() =>
            expect(screen.getByLabelText(/Why/)).toHaveValue("the calculation converged"),
        )
    })

    it("does not discard a typed reason when the same row is started first", async () => {
        meIs(admin)
        queueIs([task()])
        server.use(http.post(`${QUEUE}/41/start-review`, () => HttpResponse.json(task())))
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        const ref = "spc_vu7cuk4s37szxaudjpf355tqda"
        await user.click(rowButton(ref, "Close…"))
        await user.type(screen.getByLabelText(/Why/), "half-written reason")
        await user.click(rowButton(ref, "Start review"))

        // Only resolving is what the close form is for. Pressing the other
        // button first is not a reason to lose what was typed.
        await waitFor(() =>
            expect(screen.getByLabelText(/Why/)).toHaveValue("half-written reason"),
        )
    })
})

describe("when the archive says something this build does not understand", () => {
    it("shows a row whose severity token is unfamiliar rather than dropping it", async () => {
        meIs(admin)
        queueIs([
            task({
                highest_severity: "blocking",
                machine_review_status: "machine_screened_blocking_concern",
            }),
        ])
        renderPage()

        // Neither token drives a decision, so an unfamiliar one is shown
        // as-is. Treating them as strict enums took the whole page down.
        await screen.findByRole("table")
        expect(bodyRows()[0][1]).toBe("blocking")
        // Shown, but not painted as one of the three this build knows:
        // guessing a colour would state a severity nobody asserted.
        const painted = screen.getByText("blocking")
        expect(painted.className).not.toMatch(/severity-/)
    })

    it("one unreadable row costs that row, not the queue", async () => {
        meIs(admin)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({
                    items: [
                        task({ id: 41, record_public_ref: "spc_readable" }),
                        // `workflow_state` IS strict: the page branches on it,
                        // so a value it cannot classify is unusable.
                        task({ id: 42, workflow_state: "awaiting_second_opinion" }),
                    ],
                    total: 2,
                    skip: 0,
                    limit: 50,
                }),
            ),
        )
        renderPage()

        expect(await screen.findByText("spc_readable")).toBeInTheDocument()
        // And it says so, rather than silently showing one row of two --
        // with the count, because "some rows are missing" is not actionable
        // and "1 task" is.
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be read/i)
        expect(alert).toHaveTextContent("1 task")
    })

    it("never reports a committed write as 'nothing was changed'", async () => {
        meIs(admin)
        queueIs([task()])
        let hits = 0
        server.use(
            http.post(`${QUEUE}/41/start-review`, () => {
                hits += 1
                // 2xx: the row HAS moved. Only the body is unreadable --
                // what happens the first time the backend returns an enum
                // member this build predates.
                return HttpResponse.json({ id: 41, workflow_state: "brand_new_state" })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Start review" }))

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/was saved/i)
        expect(alert).not.toHaveTextContent(/Nothing was changed/i)
        expect(hits).toBe(1)
    })

    it("closes the form when a resolve was saved but its reply was unreadable", async () => {
        meIs(admin)
        queueIs([task()])
        let hits = 0
        server.use(
            http.post(`${QUEUE}/41/resolve`, () => {
                hits += 1
                return HttpResponse.json({ id: 41, workflow_state: "brand_new_state" })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Close…" }))
        await user.type(screen.getByLabelText(/Why/), "fine as deposited")
        await user.click(screen.getByRole("button", { name: "Close task" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(/was saved/i)
        // The task IS closed. Leaving the filled-in form open beneath it
        // invites filing the same reason a second time.
        await waitFor(() =>
            expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument(),
        )
        expect(hits).toBe(1)
    })

    it("a queue that will not load is reported, not shown as empty", async () => {
        meIs(admin)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(
                    { code: "internal_error", detail: "Backend is down." },
                    { status: 500 },
                ),
            ),
        )
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent("Backend is down.")
        expect(screen.queryByText(/Nothing is waiting/i)).not.toBeInTheDocument()
    })
})
