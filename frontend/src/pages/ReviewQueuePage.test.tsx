import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ReviewQueuePage from "./ReviewQueuePage"
import { AuthProvider } from "../components/AuthProvider"

/**
 * Record review is the one surface in this app where a click changes
 * what readers are told to trust. These tests pin:
 *
 * 1. Who gets in (curator or admin; a plain signed-in user is told why).
 * 2. That the page says which review axis it is, since "review" alone
 *    does not distinguish it from Machine findings.
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

/**
 * Serve these rows for EVERY request, whatever the page asked for.
 *
 * Deliberately unrealistic, and that is a trap worth naming: because it
 * ignores `status`, a test using it cannot see anything that depends on
 * a row leaving the current view. Exactly that hid a defect where a
 * refusal message vanished along with its row. Use `queueByStatus` for
 * anything about filtering, or about what happens after a re-read.
 */
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

/** Serve rows the way the server does: filtered by the `status` asked for. */
function queueByStatus(rowsNow: () => Record<string, unknown>[]) {
    server.use(
        http.get(REVIEWS, ({ request }) => {
            const wanted = new URL(request.url).searchParams.get("status")
            const rows = rowsNow()
            return HttpResponse.json(
                wanted === null ? rows : rows.filter((r) => r.status === wanted),
            )
        }),
    )
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

describe("who can open record review", () => {
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
    it("is titled Record review, and never by its old name", async () => {
        /**
         * The rename is the fix for the actual complaint: the owner could
         * not tell this page from `/admin/curator-queue`, because "Review
         * queue" and "Curator queue" both named an audience instead of a
         * subject.
         *
         * The heading alone is not enough to assert -- the old name left
         * lying in the lede, an empty state or an error string leaves the
         * pair exactly as confusable as before -- so the absence of the
         * old wording anywhere on the page is asserted beside it.
         */
        meIs(curator)
        queueIs([])
        renderPage()

        expect(await screen.findByRole("heading", { name: "Record review", level: 1 })).toBeInTheDocument()
        expect(screen.queryByText(/review queue/i)).not.toBeInTheDocument()
    })

    it("states that approving here changes what readers are told to trust", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        // Machine findings' lede says the opposite about itself. Two
        // surfaces that both sound like "review" must each say which they
        // are, or the distinction lives only in an ADR.
        expect(
            await screen.findByText(/changes what every reader is told to trust/i),
        ).toBeInTheDocument()
    })

    it("links to the other queue, so the two are not confused", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        const link = await screen.findByRole("link", { name: "Machine findings" })
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
        // The judgement IS recorded. Leaving the filled-in form open
        // beneath it invites recording it a second time.
        await waitFor(() =>
            expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument(),
        )
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

describe("a refusal is still said when its row has gone", () => {
    it("says what was refused, and about which record, after the row leaves the view", async () => {
        meIs(curator)
        // The commonest refusal: somebody else moved the record. The
        // re-read that follows is under `not_reviewed`, and the record is
        // not that any more -- so the row goes.
        let moved = false
        queueByStatus(() =>
            moved
                ? [review({ status: "approved" })]
                : [review({ status: "not_reviewed" })],
        )
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () => {
                moved = true
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

        // Previously the message was keyed to the row and rendered only
        // inside it, so it vanished with the row: the curator pressed the
        // button, the row disappeared, and nothing was ever said. The
        // earlier test could not see this because its mock ignored the
        // `status` the page sends.
        // Target the BANNER, not "an alert": the in-row message renders
        // first and contains neither the ref nor this wording, so
        // `findByRole("alert")` passed only because the re-read happened
        // to win a scheduler race. That is the same defect as holding a
        // request open with a timer.
        const banner = await screen.findByText(/no longer in this view/i)
        expect(banner).toHaveTextContent(/is not allowed/)
        expect(banner).toHaveTextContent(SPECIES)
    })

    it("names an unnameable record in that message rather than saying nothing", async () => {
        meIs(curator)
        let moved = false
        queueByStatus(() =>
            moved
                ? []
                : [
                      review({
                          record_type: "applied_energy_correction",
                          record_public_ref: null,
                      }),
                  ],
        )
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () => {
                moved = true
                return HttpResponse.json(
                    { code: "domain_error", detail: "Somebody else got there first." },
                    { status: 400 },
                )
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton("cannot be named", "Review…"))
        await user.type(screen.getByLabelText(/Why/), "mine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        const banner = await screen.findByText(/no longer in this view/i)
        expect(banner).toHaveTextContent("applied_energy_correction (unnamed)")
        expect(banner).toHaveTextContent(/Somebody else got there first/)
    })
})

describe("a refusal does not outstay its welcome", () => {
    it("is gone once the same row succeeds and leaves the view", async () => {
        meIs(curator)
        // Refused, then retried successfully. The row leaves the view
        // because the retry moved it -- and the banner must not then
        // announce the OLD refusal about a write that has just worked.
        let attempts = 0
        queueByStatus(() =>
            attempts >= 2
                ? [review({ status: "approved" })]
                : [review({ status: "not_reviewed" })],
        )
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () => {
                attempts += 1
                if (attempts === 1) {
                    return HttpResponse.json(
                        { code: "domain_error", detail: "Someone else has it." },
                        { status: 400 },
                    )
                }
                return HttpResponse.json(review({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "first")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Someone else has it/)

        // The form stays open after a non-saved refusal, so retry in it --
        // clicking "Review..." again would close it.
        await user.clear(screen.getByLabelText(/Why/))
        await user.type(screen.getByLabelText(/Why/), "second")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() =>
            expect(screen.queryByText(/Someone else has it/)).not.toBeInTheDocument(),
        )
        expect(screen.queryByText(/no longer in this view/i)).not.toBeInTheDocument()
    })

    it("does not follow the curator to another page", async () => {
        meIs(curator)
        // The same rule as the filter, through the paging door: a refusal
        // is about a row in a view, and a different page is a different
        // view. Without `offset` in the clearing effect the banner rides
        // along, naming a row from page two as "no longer in this view"
        // while the curator reads page one.
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 200 + i, record_public_ref: `spc_p1_${i}` }),
        )
        server.use(
            http.get(REVIEWS, ({ request }) => {
                const skip = Number(new URL(request.url).searchParams.get("skip") ?? 0)
                return HttpResponse.json(
                    skip === 0
                        ? fifty
                        : [review({ id: 900, record_public_ref: "spc_page_two" })],
                )
            }),
            http.patch(`${REVIEWS}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "service_unavailable", detail: "Page two refusal." },
                    { status: 503 },
                ),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await screen.findByText("spc_page_two")

        await user.click(rowButton("spc_page_two", "Review…"))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Page two refusal/)

        await user.click(screen.getByRole("button", { name: "Newer" }))

        await waitFor(() =>
            expect(screen.queryByText(/Page two refusal/)).not.toBeInTheDocument(),
        )
    })

    it("does not follow the curator into another view", async () => {
        meIs(curator)
        // A refusal that leaves the row where it is (a 503, say). Change
        // filter and the row is absent for a reason that has nothing to do
        // with the refusal -- announcing "no longer in this view" there is
        // simply false, and there was no way to dismiss it.
        queueByStatus(() => [review({ status: "not_reviewed" })])
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "service_unavailable", detail: "Try again shortly." },
                    { status: 503 },
                ),
            ),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Try again shortly/)

        await user.selectOptions(screen.getByLabelText("Showing"), "approved")

        await waitFor(() =>
            expect(screen.queryByText(/Try again shortly/)).not.toBeInTheDocument(),
        )
    })
})

describe("paging cannot strand a curator", () => {
    it("still offers a way back from a page that came back empty", async () => {
        meIs(curator)
        // Exactly one full page, so "Older" is offered and the page beyond
        // it is empty. Inside the rows-present branch the controls went
        // with the rows, leaving no way back at all.
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(REVIEWS, ({ request }) => {
                const skip = Number(new URL(request.url).searchParams.get("skip") ?? 0)
                return HttpResponse.json(skip === 0 ? fifty : [])
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.click(screen.getByRole("button", { name: "Older" }))

        expect(await screen.findByText(/Nothing older than this/i)).toBeInTheDocument()
        // And it must NOT claim the archive is reviewed.
        expect(
            screen.queryByText(/Every record has been looked at/i),
        ).not.toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Newer" })).toBeEnabled()
        // Older must be dead here. Every other "Older is disabled"
        // assertion sits at offset 0, so without this one a rule like
        // `offset === 0 && !full` would let a curator click onward into
        // nothing, page after page.
        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
    })

    it("goes back one page at a time, not to the start", async () => {
        meIs(curator)
        const asked: string[] = []
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(REVIEWS, ({ request }) => {
                const skip = new URL(request.url).searchParams.get("skip") ?? "0"
                asked.push(skip)
                return HttpResponse.json(fifty)
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(2))
        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(3))

        // From page three, Newer is page two -- not page one.
        await user.click(screen.getByRole("button", { name: "Newer" }))
        await waitFor(() => expect(asked).toEqual(["0", "50", "100", "50"]))
    })

    it("does not call a nearly-full page full", async () => {
        meIs(curator)
        // 49 of a 50-row page. A threshold of `>= limit - 1` would offer
        // "Older" here and strand the curator on the empty page beyond.
        const rows = Array.from({ length: 49 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(http.get(REVIEWS, () => HttpResponse.json(rows)))
        renderPage()

        await screen.findByRole("table")
        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
        expect(screen.queryByText(/cannot say how many/i)).not.toBeInTheDocument()
    })
})

describe("the form never shows one judgement and sends another", () => {
    it("re-derives the choice when a re-read makes it impossible", async () => {
        meIs(curator)
        // not_reviewed offers rejected; approved does not. A re-read that
        // moves the row to approved leaves "rejected" held in a draft that
        // no option matches -- and a controlled <select> whose value
        // matches nothing displays the FIRST option instead.
        let moved = false
        queueByStatus(() => [
            moved ? review({ status: "approved" }) : review({ status: "not_reviewed" }),
        ])
        let sent: Record<string, unknown> = {}
        server.use(
            http.patch(`${REVIEWS}/:type/:id`, async ({ request }) => {
                const body = (await request.json()) as Record<string, unknown>
                if (!moved) {
                    moved = true
                    return HttpResponse.json(
                        { code: "domain_error", detail: "Not allowed." },
                        { status: 400 },
                    )
                }
                sent = body
                return HttpResponse.json(review({ status: "under_review" }))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        await screen.findByRole("table")

        await user.click(rowButton(SPECIES, "Review…"))
        await user.selectOptions(screen.getByLabelText(/New review state/), "rejected")
        await user.type(screen.getByLabelText(/Why/), "first try")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        // The row is now approved, whose options are under_review and
        // deprecated. Whatever the select shows must be what is sent.
        await waitFor(async () =>
            expect(await rowStatus(SPECIES)).toBe("approved"),
        )
        const select = screen.getByLabelText(/New review state/) as HTMLSelectElement
        const shown = select.value
        expect(["under_review", "deprecated"]).toContain(shown)

        // The hint is the third thing that must agree. With the stale
        // draft still held, the select showed one state and the sentence
        // beneath it described another.
        const hint =
            shown === "under_review"
                ? /No judgement is recorded yet/i
                : /without being judged wrong/i
        expect(screen.getByText(hint)).toBeInTheDocument()
        expect(screen.queryByText(/judged this record wrong/i)).not.toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await waitFor(() => expect(sent.status).toBe(shown))
    })

    it("describes the state it is actually offering", async () => {
        meIs(curator)
        queueIs([review({ status: "approved" })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const select = screen.getByLabelText(/New review state/) as HTMLSelectElement

        // The hint and the select must agree: they are the two things a
        // curator reads before deciding.
        expect(select.value).toBe("under_review")
        expect(screen.getByText(/No judgement is recorded yet/i)).toBeInTheDocument()
    })
})

describe("one row's write leaves the other rows alone", () => {
    it("does not close another row's open form when a write succeeds", async () => {
        meIs(curator)
        queueIs([review(), otherReview()])
        const species = gate()
        server.use(
            http.patch(`${REVIEWS}/species/987654`, async () => {
                await species.held
                return HttpResponse.json(review({ status: "under_review" }))
            }),
        )
        renderPage()
        await screen.findByRole("table")

        const user = userEvent.setup()
        // Start the species write, then open the calculation's form while
        // it is still out.
        await user.click(rowButton(SPECIES, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "species reason")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await user.click(rowButton(CALC, "Review…"))
        await user.type(screen.getByLabelText(/Why/), "calculation reason")

        species.release()

        // Wait for the species write to have FINISHED before asserting.
        // Without this the assertion ran while the write was still out and
        // passed on the first check, so it could not see a later close --
        // the test ended before the behaviour it was written for happened.
        await waitFor(() => expect(rowButton(SPECIES, "Review…")).toBeEnabled())

        // The species row finishing must not throw away what is being
        // typed against the calculation.
        expect(screen.getByLabelText(/Why/)).toHaveValue("calculation reason")
    })
})

describe("what a curator may not do to their own deposit", () => {
    it("does not offer approval on a record this curator deposited", async () => {
        meIs(curator)
        // The service refuses self-approval. Offering the option is a
        // button that can only fail, which is the thing mirroring the
        // transition table was supposed to prevent.
        queueIs([review({ created_by: curator.id })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).not.toContain("approved")
        expect(options).toContain("rejected")
    })

    it("still offers approval on somebody else's deposit", async () => {
        meIs(curator)
        queueIs([review({ created_by: curator.id + 999 })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).toContain("approved")
    })

    it("offers approval when the depositor is unknown", async () => {
        meIs(curator)
        // `created_by` is nullable. Withholding approval on a null would
        // block review of anything whose depositor was not recorded.
        queueIs([review({ created_by: null })])
        renderPage()

        const user = userEvent.setup()
        await user.click(await screen.findByRole("button", { name: "Review…" }))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).toContain("approved")
    })
})

describe("the filter control", () => {
    it("asks the server for the status chosen", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(REVIEWS, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json([review({ status: "approved" })])
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.selectOptions(screen.getByLabelText("Showing"), "approved")
        await waitFor(() => expect(asked).toEqual(["not_reviewed", "approved"]))
    })

    it("sends no status at all for 'every record'", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(REVIEWS, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json([review()])
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        // `status=all` is not a value the enum has; sending it would be a
        // 422 rather than "every record".
        await user.selectOptions(screen.getByLabelText("Showing"), "all")
        await waitFor(() => expect(asked).toEqual(["not_reviewed", null]))
    })

    it("actually shows what the new filter returned", async () => {
        meIs(curator)
        queueByStatus(() => [
            review({ record_public_ref: "spc_unreviewed", status: "not_reviewed" }),
            otherReview({ record_public_ref: "calc_approved", status: "approved" }),
        ])
        const user = userEvent.setup()
        renderPage()

        expect(await screen.findByText("spc_unreviewed")).toBeInTheDocument()
        await user.selectOptions(screen.getByLabelText("Showing"), "approved")

        expect(await screen.findByText("calc_approved")).toBeInTheDocument()
        expect(screen.queryByText("spc_unreviewed")).not.toBeInTheDocument()
    })

    it("asks for a page of the size it claims", async () => {
        meIs(curator)
        let limit: string | null = null
        server.use(
            http.get(REVIEWS, ({ request }) => {
                limit = new URL(request.url).searchParams.get("limit")
                return HttpResponse.json([review()])
            }),
        )
        renderPage()
        await screen.findByRole("table")

        // The "a full page" wording is only true if the page asked for
        // exactly as many rows as it treats as full.
        expect(limit).toBe("50")
    })
})

describe("reaching past the newest page", () => {
    it("asks for the next page, and says where it is", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(REVIEWS, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("skip"))
                return HttpResponse.json(fifty)
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        // The list is newest-first, so without this the oldest deposits --
        // the actual backlog -- cannot be reached at all.
        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toEqual(["0", "50"]))
        expect(await screen.findByText(/from 51/)).toBeInTheDocument()
    })

    it("cannot go older than a page that is not full", async () => {
        meIs(curator)
        queueIs([review()])
        renderPage()
        await screen.findByRole("table")

        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Newer" })).toBeDisabled()
    })

    it("returns to the first page when the filter changes", async () => {
        meIs(curator)
        const asked: string[] = []
        const fifty = Array.from({ length: 50 }, (_, i) =>
            review({ id: 100 + i, record_public_ref: `spc_${i}` }),
        )
        server.use(
            http.get(REVIEWS, ({ request }) => {
                const url = new URL(request.url)
                asked.push(`${url.searchParams.get("status")}@${url.searchParams.get("skip")}`)
                return HttpResponse.json(fifty)
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(2))
        await user.selectOptions(screen.getByLabelText("Showing"), "approved")

        // Carrying the offset across would show page two of a view the
        // curator has not seen page one of.
        await waitFor(() => expect(asked.at(-1)).toBe("approved@0"))
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
        const count = await screen.findByText(/cannot say how many/i)
        // And it must not overstate in the other direction: a list of
        // exactly 50 fills the page with nothing beyond it, so "there ARE
        // more" would be a claim the page cannot support -- and it is what
        // sends a curator to an empty page looking for rows.
        expect(count).toHaveTextContent(/may be more/i)
        expect(count).not.toHaveTextContent(/there are more/i)
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

describe("how much of the queue this page claims to show", () => {
    it("says nothing about more rows when the page is neither empty nor full", async () => {
        meIs(curator)
        // Three rows: not the 1 and not the 50 the other tests use. A
        // threshold that fires on "more than one" would claim a partial
        // page is full, and send a curator looking for pages that do not
        // exist.
        queueIs([
            review({ id: 11, record_public_ref: "spc_a" }),
            review({ id: 12, record_public_ref: "spc_b" }),
            review({ id: 13, record_public_ref: "spc_c" }),
        ])
        renderPage()

        await screen.findByRole("table")
        expect(screen.getByText(/3 shown/)).toBeInTheDocument()
        expect(screen.queryByText(/cannot say how many/i)).not.toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
    })

    it("does not call an empty filtered view a finished backlog", async () => {
        meIs(curator)
        queueByStatus(() => [review({ status: "not_reviewed" })])
        const user = userEvent.setup()
        renderPage()
        await screen.findByRole("table")

        await user.selectOptions(screen.getByLabelText("Showing"), "rejected")

        // "Every record has been looked at" is a claim about the whole
        // archive. An empty `rejected` view means nothing was rejected,
        // which is a different sentence entirely.
        expect(await screen.findByText(/No records match this filter/i)).toBeInTheDocument()
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

    it("offers a control on every status a row can actually hold", async () => {
        meIs(curator)
        // Named for what it checks. Every one of the five statuses has at
        // least one allowed transition, and an unknown status never
        // reaches the table (the parser rejects the row), so "a row with
        // no transitions" is not a state this page can be in.
        queueIs([review({ status: "approved" })])
        renderPage()
        await screen.findByRole("table")
        expect(rowButton(SPECIES, "Review…")).toBeEnabled()
    })
})
