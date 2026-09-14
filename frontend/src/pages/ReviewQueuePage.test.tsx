import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ReviewQueuePage from "./ReviewQueuePage"
import { AuthProvider } from "../components/AuthProvider"

/**
 * The review queue is the one surface in this app where a click changes
 * what readers are told to trust. These tests pin:
 *
 * 1. Who gets in (curator or admin; a plain signed-in user is told why).
 * 2. That the page says which review axis it is, since "review" alone
 *    does not distinguish it from the curator queue.
 * 3. That only transitions the backend allows are offered, and that a
 *    refusal is believed over the page's own copy of the policy.
 * 4. That everything a row owns stays with that row.
 *
 * **Two rows wherever a row-owned value is involved.** A single-row table
 * cannot tell "belongs to this row" from "page-level", which is the class
 * of defect that put one record's reason on another record in #483.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const REVIEWS = "/api/v1/record-reviews"

const curator = {
    id: 1,
    username: "calvin",
    email: "calvin@example.com",
    full_name: "Calvin Pieters",
    role: "curator",
    is_active: true,
}
const admin = { ...curator, id: 2, username: "root", role: "admin" }
const plainUser = { ...curator, id: 3, username: "noah", role: "user" }

function meIs(who: Record<string, unknown>) {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(who)))
}

/** One review row, shaped like `RecordReviewRead`. */
function review(over: Record<string, unknown> = {}) {
    return {
        id: 11,
        record_type: "species",
        record_id: 987654,
        record_public_ref: "spc_vu7cuk4s37szxaudjpf355tqda",
        status: "not_reviewed",
        submission_id: 7,
        reviewed_by: null,
        reviewed_at: null,
        first_approved_at: null,
        note: null,
        created_at: "2026-09-14T10:00:00Z",
        created_by: 5,
        ...over,
    }
}

function otherReview(over: Record<string, unknown> = {}) {
    return review({
        id: 12,
        record_type: "calculation",
        record_id: 123456,
        record_public_ref: "calc_7k2mq9x4ta8ndrwe5hvzcbj6y1",
        ...over,
    })
}

function queueIs(rows: Record<string, unknown>[]): { reads: number } {
    const counter = { reads: 0 }
    server.use(
        http.get(REVIEWS, () => {
            counter.reads += 1
            return HttpResponse.json(rows)
        }),
    )
    return counter
}

function renderPage() {
    return render(
        <AuthProvider>
            <MemoryRouter>
                <ReviewQueuePage />
            </MemoryRouter>
        </AuthProvider>,
    )
}

function rowFor(ref: string): HTMLElement {
    const table = screen.getByRole("table")
    const row = within(table)
        .getAllByRole("row")
        .find((r) => (r.textContent ?? "").includes(ref))
    if (row === undefined) throw new Error(`no row for ${ref}`)
    return row
}

function rowButton(ref: string, name: string): HTMLElement {
    const found = within(rowFor(ref))
        .getAllByRole("button")
        .find((b) => (b.textContent ?? "").trim() === name)
    if (found === undefined) throw new Error(`no "${name}" button in row ${ref}`)
    return found
}

/** The review-state cell of one row. */
function rowStatus(ref: string): string {
    return (within(rowFor(ref)).getAllByRole("cell")[1].textContent ?? "").trim()
}

/**
 * A promise the test releases by hand.
 *
 * Holding a request open with `setTimeout` makes the test a race between
 * that delay and however long the rest of the interaction takes, which
 * under a loaded parallel suite is not a race you win reliably -- this
 * file had exactly that flake, at roughly one run in three. A gate the
 * test opens deliberately removes the timing from the question.
 */
function gate(): { held: Promise<void>; release: () => void } {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
        release = resolve
    })
    return { held, release }
}

const SPECIES = "spc_vu7cuk4s37szxaudjpf355tqda"
const CALC = "calc_7k2mq9x4ta8ndrwe5hvzcbj6y1"

describe("who can open the review queue", () => {
    it("a curator can", async () => {
        meIs(curator)
        queueIs([review()])
        renderPage()
        expect(await screen.findByRole("table")).toBeInTheDocument()
    })

    it("an admin can", async () => {
        meIs(admin)
        queueIs([review()])
        renderPage()
        expect(await screen.findByRole("table")).toBeInTheDocument()
    })

    it("a signed-in user without the role is told, and the queue is never fetched", async () => {
        meIs(plainUser)
        // No REVIEWS handler: `onUnhandledRequest: "error"` makes any
        // request a failure, so this asserts the page does not ask.
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent(/for curators/i)
        expect(screen.queryByRole("table")).not.toBeInTheDocument()
    })

    it("a backend outage does not render as 'you are not a curator'", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.error()))
        renderPage()

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be reached/i)
        expect(alert).not.toHaveTextContent(/for curators/i)
    })
})

describe("the page says which review this is", () => {
    it("states that approving here changes what readers are told to trust", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        // The curator queue's lede says the opposite about itself. Two
        // surfaces both called "review" must each say which they are, or
        // the distinction lives only in an ADR.
        expect(
            await screen.findByText(/changes what every reader is told to trust/i),
        ).toBeInTheDocument()
    })

    it("links to the other queue, so the two are not confused", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        const link = await screen.findByRole("link", { name: /curator queue/i })
        expect(link).toHaveAttribute("href", "/admin/curator-queue")
    })

    it("defaults to the backlog: records nobody has judged", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(REVIEWS, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json([review()])
            }),
        )
        renderPage()
        await screen.findByRole("table")

        expect(asked).toEqual(["not_reviewed"])
    })
})

describe("how a row names the record it concerns", () => {
    it("links a linkable type by its public ref", async () => {
        meIs(curator)
        queueIs([review()])
        renderPage()

        const link = await screen.findByRole("link", { name: SPECIES })
        expect(link).toHaveAttribute("href", `/species/${SPECIES}`)
    })

    it("puts no internal row id anywhere in the table", async () => {
        meIs(curator)
        queueIs([
            review(),
            otherReview(),
            review({ id: 13, record_type: "thermo", record_public_ref: null }),
        ])
        renderPage()

        const table = await screen.findByRole("table")
        const markup = table.outerHTML
        expect(markup).not.toContain("987654") // species record_id
        expect(markup).not.toContain("123456") // calculation record_id
    })

    it("says so when the record cannot be named", async () => {
        meIs(curator)
        queueIs([review({ record_type: "applied_energy_correction", record_public_ref: null })])
        renderPage()

        expect(await screen.findByText(/cannot be named/i)).toBeInTheDocument()
    })

    it("shows the reason recorded on a judged record", async () => {
        meIs(curator)
        queueIs([review({ status: "approved", note: "frequencies check out by hand" })])
        renderPage()

        await screen.findByRole("table")
        expect(screen.getByText("frequencies check out by hand")).toBeInTheDocument()
    })
})

describe("only transitions the backend allows are offered", () => {
    it("offers all four from not_reviewed", async () => {
        meIs(curator)
        queueIs([review({ status: "not_reviewed" })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())
        expect(options).toEqual(["under review", "approved", "rejected", "deprecated"])
    })

    it("does not offer approved -> rejected, which routes through under review", async () => {
        meIs(curator)
        queueIs([review({ status: "approved" })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        // Reversing a judgement is deliberately a two-step so that the
        // re-review is recorded. Offering the one-step here would be a
        // button that can only ever fail.
        expect(options).toEqual(["under review", "deprecated"])
        expect(options).not.toContain("rejected")
    })

    it("does not offer rejected -> approved either", async () => {
        meIs(curator)
        queueIs([review({ status: "rejected" })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())
        expect(options).toEqual(["under review", "deprecated"])
    })

    it("spells out what each state asserts about the record", async () => {
        meIs(curator)
        queueIs([review({ status: "not_reviewed" })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const choice = screen.getByLabelText(/New review state/)

        expect(screen.getByText(/No judgement is recorded yet/i)).toBeInTheDocument()
        await user.selectOptions(choice, "approved")
        expect(screen.getByText(/Readers are told it is trusted/i)).toBeInTheDocument()
        await user.selectOptions(choice, "deprecated")
        expect(screen.getByText(/without being judged wrong/i)).toBeInTheDocument()
    })
})

describe("recording a judgement", () => {
    it("sends the record's type and id, the new status, and the reason", async () => {
        meIs(curator)
        queueIs([review()])
        let body: Record<string, unknown> = {}
        let path = ""
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, async ({ request, params }) => {
                path = `${params.type}/${params.id}`
                body = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(review({ status: "approved", note: "checked" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        await user.selectOptions(screen.getByLabelText(/New review state/), "approved")
        await user.type(screen.getByLabelText(/Why/), "checked")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(body.status).toBe("approved"))
        // The PATCH is addressed by row id even though the page displays a
        // public ref: that asymmetry is recorded in the walkthrough.
        expect(path).toBe("species/987654")
        expect(body.note).toBe("checked")
    })

    it("will not record a judgement without a reason", async () => {
        meIs(curator)
        queueIs([review()])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))

        // No PATCH handler: if the disabled button were clickable the
        // request would trip the unhandled-request guard.
        expect(
            screen.getByRole("button", { name: "Record this judgement" }),
        ).toBeDisabled()
        await user.type(screen.getByLabelText(/Why/), "   ")
        expect(
            screen.getByRole("button", { name: "Record this judgement" }),
        ).toBeDisabled()
    })

    it("re-reads the queue from the server afterwards", async () => {
        meIs(curator)
        const counter = queueIs([review()])
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () =>
                HttpResponse.json(review({ status: "approved" })),
            ),
        )
        renderPage()
        await screen.findByRole("table")
        const before = counter.reads

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(counter.reads).toBeGreaterThan(before))
    })

    it("believes the server over its own copy of the transition policy", async () => {
        meIs(curator)
        queueIs([review()])
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () =>
                HttpResponse.json(
                    {
                        code: "domain_error",
                        detail: "Transition not_reviewed -> approved is not allowed.",
                    },
                    { status: 400 },
                ),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        // ALLOWED_TRANSITIONS is a convenience for the UI; the service
        // decides. If the two ever disagree, the curator must see the
        // server's reason rather than a generic failure.
        expect(await screen.findByRole("alert")).toHaveTextContent(
            /Transition not_reviewed -> approved is not allowed/,
        )
    })

    it("surfaces the self-approval refusal as the server words it", async () => {
        meIs(curator)
        queueIs([review()])
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () =>
                HttpResponse.json(
                    {
                        code: "domain_error",
                        detail: "You cannot approve a record you deposited.",
                    },
                    { status: 400 },
                ),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        await user.type(screen.getByLabelText(/Why/), "mine, but good")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(
            /cannot approve a record you deposited/i,
        )
    })

    it("never reports a committed judgement as 'nothing was changed'", async () => {
        meIs(curator)
        queueIs([review()])
        let hits = 0
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () => {
                hits += 1
                // 2xx: the transition HAS happened; only the body is
                // unreadable, which is what a future enum member looks like.
                return HttpResponse.json({ id: 11, status: "provisionally_endorsed" })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/was saved/i)
        expect(alert).not.toHaveTextContent(/Nothing was changed/i)
        expect(hits).toBe(1)
    })
})

describe("everything a row owns stays with that row", () => {
    it("does not carry one record's reason over to another record", async () => {
        meIs(curator)
        queueIs([review(), otherReview()])
        let sent: Record<string, unknown> = {}
        server.use(
            http.patch(`${REVIEWS}/calculation/123456`, async ({ request }) => {
                sent = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(otherReview({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "the species geometry is fine")
        await user.click(rowButton(CALC, "Review…"))

        // A judgement about the species must not arrive attached to the
        // calculation. This axis writes what readers are told to trust,
        // so a misattributed reason is worse here than anywhere.
        expect(screen.getByLabelText(/Why/)).toHaveValue("")
        await user.type(screen.getByLabelText(/Why/), "the calculation converged")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await waitFor(() => expect(sent.note).toBe("the calculation converged"))
    })

    it("does not carry one row's chosen state over either", async () => {
        meIs(curator)
        queueIs([review(), otherReview()])
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.selectOptions(screen.getByLabelText(/New review state/), "deprecated")
        await user.click(rowButton(CALC, "Review…"))

        // Each form opens at its row's own first allowed transition.
        expect(screen.getByLabelText(/New review state/)).toHaveValue("under_review")
    })

    it("keeps each row's refusal, instead of one slot they overwrite", async () => {
        meIs(curator)
        queueIs([review(), otherReview()])
        server.use(
            http.patch(`${REVIEWS}/species/987654`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Species is stuck." },
                    { status: 400 },
                ),
            ),
            http.patch(`${REVIEWS}/calculation/123456`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Calculation is stuck." },
                    { status: 400 },
                ),
            ),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "a")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText("Species is stuck.")

        await user.click(rowButton(CALC, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "b")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText("Calculation is stuck.")

        expect(screen.getByText("Species is stuck.")).toBeInTheDocument()
    })

    it("a row still waiting on its own write is not re-enabled by another row finishing", async () => {
        meIs(curator)
        // The calculation row moves once its own write lands; the species
        // row's write is held open by the test, so the species row must not.
        const species = gate()
        let calcDone = false
        server.use(
            http.get(REVIEWS, () =>
                HttpResponse.json([
                    review(),
                    calcDone ? otherReview({ status: "approved" }) : otherReview(),
                ]),
            ),
            http.patch(`${REVIEWS}/species/987654`, async () => {
                await species.held
                return HttpResponse.json(review({ status: "approved" }))
            }),
            http.patch(`${REVIEWS}/calculation/123456`, () => {
                calcDone = true
                return HttpResponse.json(otherReview({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        // Start the slow one, then open and submit the fast one.
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "slow")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await user.click(rowButton(CALC, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "fast")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        // The fast row has landed. With in-flight state as a single
        // page-level slot, that would re-enable the slow row's control
        // while its own PATCH is still out, and a second click would send
        // a second judgement.
        await waitFor(() => expect(rowFor(CALC).textContent).toContain("approved"))
        expect(rowButton(SPECIES, "Review…")).toBeDisabled()

        // Let the held write finish, so the test leaves nothing in flight.
        species.release()
        await waitFor(() => expect(rowButton(SPECIES, "Review…")).toBeEnabled())
    })

    it("re-reads a refused row, because a refusal usually means it moved", async () => {
        meIs(curator)
        // Another curator gets there first: our PATCH is refused, and the
        // next read shows the state they set.
        let refused = false
        server.use(
            http.get(REVIEWS, () =>
                HttpResponse.json([
                    refused ? review({ status: "approved" }) : review(),
                ]),
            ),
            http.patch(`${REVIEWS}/:type/:id`, () => {
                refused = true
                return HttpResponse.json(
                    {
                        code: "domain_error",
                        detail: "Transition not_reviewed -> rejected is not allowed.",
                    },
                    { status: 400 },
                )
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.selectOptions(screen.getByLabelText(/New review state/), "rejected")
        await user.type(screen.getByLabelText(/Why/), "wrong")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(/is not allowed/)
        // Leaving the stale "not reviewed" on screen under a live control
        // invites the curator to try the same thing again against a state
        // the server has already left.
        await waitFor(() => expect(rowStatus(SPECIES)).toBe("approved"))
    })

    it("disables a row's own control while its write is in flight", async () => {
        meIs(curator)
        queueIs([review()])
        const write = gate()
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, async () => {
                await write.held
                return HttpResponse.json(review({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(
            screen.getByRole("button", { name: "Record this judgement" }),
        ).toBeDisabled()

        write.release()
        await waitFor(() => expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument())
    })
})

describe("what this page can and cannot tell you", () => {
    it("says plainly when the page is full, rather than implying it is the whole backlog", async () => {
        meIs(curator)
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(http.get(REVIEWS, () => HttpResponse.json(fifty)))
        renderPage()

        // The route answers with a bare array and no total (task #257), so
        // "50 shown" alone would read as "that is all of them".
        expect(await screen.findByText(/cannot say how many/i)).toBeInTheDocument()
    })

    it("does not claim there may be more when the page is short", async () => {
        meIs(curator)
        queueIs([review()])
        renderPage()

        await screen.findByRole("table")
        expect(screen.queryByText(/cannot say how many/i)).not.toBeInTheDocument()
    })

    it("an empty backlog reads as finished, not as broken", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        expect(
            await screen.findByText(/Every record has been looked at/i),
        ).toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    it("one unreadable row costs that row, not the queue", async () => {
        meIs(curator)
        server.use(
            http.get(REVIEWS, () =>
                HttpResponse.json([
                    review({ record_public_ref: "spc_readable" }),
                    review({ id: 12, status: "awaiting_second_opinion" }),
                ]),
            ),
        )
        renderPage()

        expect(await screen.findByText("spc_readable")).toBeInTheDocument()
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be read/i)
        expect(alert).toHaveTextContent("1 row")
    })

    it("a queue that will not load is reported, not shown as finished", async () => {
        meIs(curator)
        server.use(
            http.get(REVIEWS, () =>
                HttpResponse.json(
                    { code: "internal_error", detail: "Backend is down." },
                    { status: 500 },
                ),
            ),
        )
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent("Backend is down.")
        expect(
            screen.queryByText(/Every record has been looked at/i),
        ).not.toBeInTheDocument()
    })
})

describe("the status a row shows", () => {
    it("shows each row's own state, not a neighbour's", async () => {
        meIs(curator)
        queueIs([
            review({ status: "approved" }),
            otherReview({ status: "rejected" }),
        ])
        renderPage()
        await screen.findByRole("table")

        expect(rowStatus(SPECIES)).toBe("approved")
        expect(rowStatus(CALC)).toBe("rejected")
    })

    it("offers nothing to act on where no transition exists", async () => {
        meIs(curator)
        // Every real status has at least one transition, so this is the
        // unknown-status path -- which the row parser rejects. The guard
        // is here so that a future terminal state renders as a row with
        // no control rather than a button that cannot work.
        queueIs([review({ status: "approved" })])
        renderPage()
        await screen.findByRole("table")
        expect(rowButton(SPECIES, "Review…")).toBeEnabled()
    })
})
