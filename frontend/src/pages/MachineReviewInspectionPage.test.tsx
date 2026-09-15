import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"
import { CuratorTaskBuildResultSchema } from "../types/curatorTask"

/**
 * The record-summaries table used to carry a column headed `record_ref` whose
 * value was a database row id. `record_ref` means the *public* ref everywhere
 * else in this API, so the page promised a handle and delivered a row number.
 *
 * It now shows `record_public_ref` -- the identifier the archive is addressed
 * by -- and `record_id`, which an admin-only surface may carry. These tests pin
 * which value lands in which column, and that a record with no public ref shows
 * nothing rather than falling back to its id.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const SUMMARY = {
    status: "machine_screened_warning",
    highest_severity: "warning",
    findings_count: 1,
    model: "fake_test/simple-v1",
    provider: "FakeLLMPrecheckProvider",
    reviewed_at: "2026-09-14T10:00:00Z",
    submission_id: 7,
}

const PUBLIC_REF = "spc_vu7cuk4s37szxaudjpf355tqda"

function serve(record: Record<string, unknown>) {
    server.use(
        http.get("/api/v1/admin/submissions/7/machine-review-inspection", () =>
            HttpResponse.json({
                submission_id: 7,
                record_summaries: [record],
                unmapped_findings_count: 0,
                mapping_warnings: [],
                parse_warnings: [],
                source_audit_event_ids: [11],
            }),
        ),
    )
}

/**
 * Render and inspect one submission; returns the `user` for further acts.
 *
 * `MemoryRouter` is here because the build tally offers a link into the
 * curator queue, and a `<Link>` outside a router throws. The page itself
 * is mounted outside the app's `Layout` route (see `App.tsx`) but still
 * inside `BrowserRouter`, so this matches how it really renders.
 */
async function inspect(id = "7") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
        <MemoryRouter>
            <QueryClientProvider client={client}>
                <MachineReviewInspectionPage />
            </QueryClientProvider>
        </MemoryRouter>,
    )
    await user.type(screen.getByLabelText(/submission_id/), id)
    await user.click(screen.getByRole("button", { name: "Inspect" }))
    return user
}

/** Type a different submission id into the form already on screen. */
async function reinspect(user: ReturnType<typeof userEvent.setup>, id: string) {
    const field = screen.getByLabelText(/submission_id/)
    await user.clear(field)
    await user.type(field, id)
    await user.click(screen.getByRole("button", { name: "Inspect" }))
}

/** The record row's cell texts, in column order. */
async function recordCells(): Promise<string[]> {
    const table = (await screen.findAllByRole("table")).at(-1)!
    const bodyRow = within(table).getAllByRole("row").at(-1)!
    return within(bodyRow).getAllByRole("cell").map((c) => (c.textContent ?? "").trim())
}

describe("the record summaries table", () => {
    it("shows the public ref and the row id in their own columns", async () => {
        serve({
            record_type: "species",
            record_public_ref: PUBLIC_REF,
            record_id: 431,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        // By position, not just presence: a swap leaves both strings on the
        // page and would satisfy any "is it rendered" check. The two values are
        // deliberately unlike each other so a swap is visible.
        const cells = await recordCells()
        expect(cells[0]).toBe("species")
        expect(cells[1]).toBe(PUBLIC_REF)
        expect(cells[2]).toBe("431")
    })

    it("headings name what the columns hold", async () => {
        serve({
            record_type: "species",
            record_public_ref: PUBLIC_REF,
            record_id: 431,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        const table = (await screen.findAllByRole("table")).at(-1)!
        const headings = within(table)
            .getAllByRole("columnheader")
            .map((h) => h.textContent)

        expect(headings).toContain("record_public_ref")
        expect(headings).toContain("record_id")
        // The old name promised a public ref and delivered a row id.
        expect(headings).not.toContain("record_ref")
        // Dropped rather than renamed: it was always `String(record_id)`.
        expect(headings).not.toContain("record_match_key")
    })

    it("a record that cannot be named shows a dash, never its row id", async () => {
        /**
         * `applied_energy_correction` has no `public_ref` column, so the
         * backend answers `null`. Falling back to `record_id` would put a row
         * id under a heading that says "public ref" -- the exact defect this
         * page had.
         */
        serve({
            record_type: "applied_energy_correction",
            record_public_ref: null,
            record_id: 88,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        const cells = await recordCells()
        expect(cells[1]).not.toBe("88")
        expect(cells[1]).not.toBe("")
        expect(cells[2]).toBe("88")
    })
})

/* ------------------------------------------------------------------ *
 * Building curator tasks (task #256)
 *
 * Until this button existed nothing in the product could create a curator
 * task: the backend route is explicit-only and runs on no upload, so the
 * queue stayed empty however many submissions arrived, and the only way
 * to fill it was a hand-written curl.
 * ------------------------------------------------------------------ */

const BUILD =
    "/api/v1/admin/machine-review/curator-tasks/build-for-submission/:submissionId"

const BUILD_BUTTON = "Build curator tasks for this submission"

/**
 * Serve the inspection for ANY submission id.
 *
 * The record's public ref names the submission it came from (`spc_sub_9`),
 * which is what makes "wait until the page is showing submission 9" an
 * assertion rather than a hope. Every control on this page is present both
 * before and after a submission change, so without a value that differs
 * per submission there is nothing to wait for.
 */
function serveAnyInspection() {
    server.use(
        http.get(
            "/api/v1/admin/submissions/:submissionId/machine-review-inspection",
            ({ params }) =>
                HttpResponse.json({
                    submission_id: Number(params.submissionId),
                    record_summaries: [
                        {
                            record_type: "species",
                            record_public_ref: `spc_sub_${params.submissionId}`,
                            record_id: 431,
                            latest_summary: SUMMARY,
                            all_record_reviews_count: 1,
                        },
                    ],
                    unmapped_findings_count: 0,
                    mapping_warnings: [],
                    parse_warnings: [],
                    source_audit_event_ids: [11],
                }),
        ),
    )
}

/** Wait until the page is showing this submission's findings. */
function showing(id: string): Promise<HTMLElement> {
    return screen.findByText(`spc_sub_${id}`)
}

/**
 * The tally region, once it has something in it.
 *
 * `role="status"` is now a permanently mounted, initially EMPTY container
 * (see the component), so its mere presence proves nothing and
 * `findByRole("status")` resolves instantly whether a build has run or
 * not. Emptiness is the signal.
 */
async function tallyShown(): Promise<HTMLElement> {
    const region = screen.getByRole("status")
    await waitFor(() => expect(region).not.toBeEmptyDOMElement())
    return region
}

/** The alert region's text, once it has some. */
async function alertSays(pattern: RegExp): Promise<HTMLElement> {
    const region = screen.getByRole("alert")
    await waitFor(() => expect(region).toHaveTextContent(pattern))
    return region
}

type Tally = {
    created_count?: number
    reused_count?: number
    refreshed_count?: number
    skipped_info_count?: number
    skipped_unmapped_count?: number
    skipped_terminal_count?: number
    task_ids?: number[]
    warnings?: string[]
}

/** A build reply with every count present, overridable one at a time. */
function tally(over: Tally = {}): Record<string, unknown> {
    return {
        created_count: 0,
        reused_count: 0,
        refreshed_count: 0,
        skipped_info_count: 0,
        skipped_unmapped_count: 0,
        skipped_terminal_count: 0,
        task_ids: [],
        warnings: [],
        ...over,
    }
}

/**
 * Serve the build route. The returned array collects the submission ids it
 * was actually called with, which is how "built for the one on screen" is
 * asserted rather than assumed.
 */
function serveBuild(
    body: Record<string, unknown>,
    init?: { status: number },
): { calledFor: number[] } {
    const calledFor: number[] = []
    server.use(
        http.post(BUILD, ({ params }) => {
            calledFor.push(Number(params.submissionId))
            return HttpResponse.json(body, init)
        }),
    )
    return { calledFor }
}

/** A promise the test releases, so "while it is running" has no timer in it. */
function gate(): { held: Promise<void>; release: () => void } {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
        release = resolve
    })
    return { held, release }
}

describe("the schema behind the build tally", () => {
    it("drops task_ids, so no page can print one", () => {
        /**
         * The whole "an admin never sees a row id here" argument rests on
         * zod stripping undeclared keys. If a future zod, or a future
         * `.passthrough()`, changed that, the ids would silently become
         * available to render and nothing else would notice.
         */
        const parsed = CuratorTaskBuildResultSchema.parse(
            tally({ created_count: 2, task_ids: [4471, 4472] }),
        )
        expect("task_ids" in parsed).toBe(false)
        expect(parsed.created_count).toBe(2)
    })
})

describe("building curator tasks for a submission", () => {
    it("is not offered before a submission has been inspected", () => {
        render(
            <MemoryRouter>
                <QueryClientProvider client={new QueryClient()}>
                    <MachineReviewInspectionPage />
                </QueryClientProvider>
            </MemoryRouter>,
        )
        expect(screen.queryByRole("button", { name: BUILD_BUTTON })).toBeNull()
    })

    it("builds for the submission on screen, not some other one", async () => {
        serveAnyInspection()
        const build = serveBuild(tally({ created_count: 1 }))
        const user = await inspect("9")
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        // The id is in the URL path, so a page that built for a constant, or
        // for whatever was typed last, would still show a plausible tally.
        expect(build.calledFor).toEqual([9])
    })

    it("reports each count, with refreshed nested inside reused", async () => {
        serveAnyInspection()
        serveBuild(
            tally({
                created_count: 3,
                reused_count: 2,
                refreshed_count: 1,
                skipped_terminal_count: 4,
                skipped_info_count: 5,
                skipped_unmapped_count: 6,
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        const lines = within(status)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())

        expect(lines).toContain("new tasks created: 3")
        // Nested, not a sibling line: `refreshed_count` is a SUB-count of
        // `reused_count`, so listing it alongside would describe 2 reused
        // findings and 1 more refreshed one -- three findings where the
        // server saw two.
        expect(lines).toContain(
            "findings that already had an open task: 2 (1 of them refreshed with the latest snapshot)",
        )
        expect(lines).toContain(
            "findings whose task is already closed, left closed: 4",
        )
        expect(lines).toContain("info findings, which never become tasks: 5")
        expect(lines).toContain(
            "unmapped findings, which are about no record: 6",
        )
        expect(
            within(status).getByText(/3 new tasks are now in the curator queue/),
        ).toBeInTheDocument()

        // And somewhere to go and look at them. A tally with no way through
        // to the queue leaves the admin to remember the URL.
        expect(within(status).getByRole("link", { name: /curator queue/i })).toHaveAttribute(
            "href",
            "/admin/curator-queue",
        )
    })

    it("says on the page that nothing builds these on upload", async () => {
        /**
         * The reason this button exists at all. An admin who assumes tasks
         * appear by themselves reads an empty queue as "no problems found",
         * when it means "nobody has run this". The same paragraph says a
         * task endorses nothing, which keeps it apart from `record_review`.
         */
        serveAnyInspection()
        await inspect()
        await screen.findByRole("button", { name: BUILD_BUTTON })

        expect(screen.getByText(/created on upload/i)).toBeInTheDocument()
        expect(screen.getByText(/endorses nothing/i)).toBeInTheDocument()
    })

    it("counts one task in the singular", async () => {
        // "1 new task(s) are now in the queue" is the sort of thing a
        // reader stops trusting the rest of the sentence over.
        serveAnyInspection()
        serveBuild(tally({ created_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(/1 new task is now in the curator queue/),
        ).toBeInTheDocument()
    })

    it("never prints a task id", async () => {
        serveAnyInspection()
        serveBuild(tally({ created_count: 2, task_ids: [4471, 4472] }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        // `textContent` covers the rendered words; `outerHTML` covers the
        // places an id can hide from it -- a `title`, a `data-` attribute,
        // an `aria-label`. A reviewer landed exactly that mutation and the
        // textContent-only check passed with both ids in the DOM.
        // DR-0028 Req 2.
        const text = document.body.textContent ?? ""
        const markup = document.body.outerHTML
        for (const id of ["4471", "4472"]) {
            expect(text).not.toContain(id)
            expect(markup).not.toContain(id)
        }
        expect(text).toContain("new tasks created: 2")
    })

    it("says nothing was queued when there was nothing to queue", async () => {
        serveAnyInspection()
        serveBuild(tally({ skipped_info_count: 9 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(
                /nothing in this submission is a warning or critical finding/i,
            ),
        ).toBeInTheDocument()
        // The other empty-handed sentence would be a lie here: there was no
        // warning or critical finding at all, so nothing "already has" a task.
        expect(within(status).queryByText(/already has a task/i)).toBeNull()
    })

    it("distinguishes 'nothing to queue' from 'already queued'", async () => {
        serveAnyInspection()
        serveBuild(tally({ reused_count: 3, refreshed_count: 3 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(
                /every warning or critical finding here already has a task/i,
            ),
        ).toBeInTheDocument()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
    })

    it("does not imply a closed task is waiting in the queue", async () => {
        /**
         * `created = reused = 0, skipped_terminal = 3`. "Already has one"
         * on its own invites the inference that it is therefore in the
         * queue -- and the queue's open filter will show nothing for this
         * submission, because all three tasks are closed.
         */
        serveAnyInspection()
        serveBuild(tally({ skipped_terminal_count: 3 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(/already has a task, open or closed/i),
        ).toBeInTheDocument()
    })

    it("does not say there was nothing to build from when there was", async () => {
        /**
         * A finding on a record the builder could not key -- no resolved
         * internal id, an unknown record type -- reaches NONE of the six
         * counts and lands in `warnings` instead. Saying "nothing here is
         * a warning or critical finding" would contradict the warning
         * printed two inches below it.
         */
        serveAnyInspection()
        serveBuild(
            tally({
                warnings: ["Record species/spc_x has no resolved internal id; skipped"],
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
        expect(
            within(status).getByText(/the warnings below say what was skipped/i),
        ).toBeInTheDocument()
    })

    it("drops the refreshed aside when nothing was reused", async () => {
        // "0 (0 of them refreshed with the latest snapshot)" is noise
        // about a thing that did not happen.
        serveAnyInspection()
        serveBuild(tally({ created_count: 2 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        const lines = within(status)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())
        expect(lines).toContain("findings that already had an open task: 0")
    })

    it("counts a finding whose task is already closed as considered", async () => {
        /**
         * A submission whose only warning was dismissed months ago has
         * `created = reused = 0` and `skipped_terminal = 1`. Saying "nothing
         * here is a warning or critical finding" would contradict the
         * findings table directly above it.
         */
        serveAnyInspection()
        serveBuild(tally({ skipped_terminal_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
    })

    it("lists the build's own warnings", async () => {
        serveAnyInspection()
        serveBuild(
            tally({ created_count: 1, warnings: ["finding 2 named no record"] }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        expect(
            await screen.findByText("finding 2 named no record"),
        ).toBeInTheDocument()
    })

    it("says nothing was built when the server refused", async () => {
        serveAnyInspection()
        serveBuild({ detail: "Submission not found." }, { status: 404 })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        await alertSays(/No tasks were built: Submission not found/)
        // No tally: a refused build must not leave a row of zeroes looking
        // like a run that happened and found nothing.
        expect(screen.getByRole("status")).toBeEmptyDOMElement()
        expect(screen.getByRole("button", { name: BUILD_BUTTON })).toBeEnabled()
    })

    it("does NOT say nothing was built when the build ran unreadably", async () => {
        /**
         * The 2xx-with-an-unparseable-body case. The build HAS run and may
         * have written rows. The page used to prefix every failure with
         * "No tasks were built:", which made this one assert a thing and
         * its opposite in a single sentence:
         *
         *   "No tasks were built: The build ran, but this page could not
         *    read the tally. Any tasks it made are in the curator queue."
         */
        serveAnyInspection()
        serveBuild({ this_is: "not a tally" })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const alert = await alertSays(/could not read the tally/)
        expect(alert).not.toHaveTextContent(/No tasks were built/)
        // And it must point at the one place the truth can be found.
        expect(alert).toHaveTextContent(/curator queue/)
    })

    it("admits it does not know when the request got no answer", async () => {
        // Offline, DNS, a dropped connection. The server may or may not
        // have committed, and guessing either way is a false report.
        serveAnyInspection()
        server.use(http.post(BUILD, () => HttpResponse.error()))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const alert = await alertSays(/may or may not have run/)
        expect(alert).not.toHaveTextContent(/No tasks were built/)
    })

    it("cannot be pressed twice while one build is in flight", async () => {
        serveAnyInspection()
        const held = gate()
        server.use(
            http.post(BUILD, async () => {
                await held.held
                return HttpResponse.json(tally({ created_count: 1 }))
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        // The build writes rows. A second press before the first answers is
        // a second write, and the admin has no way to know which tally they
        // are reading.
        const busy = await screen.findByRole("button", { name: /Building/ })
        expect(busy).toBeDisabled()

        held.release()
        await tallyShown()
    })

    it("does not follow the admin to another submission", async () => {
        /**
         * This assertion is the guard, not any one mechanism in the page.
         * Today the tally is dropped because the parent unmounts the whole
         * results block while the next inspection loads; the
         * `key={submissionId}` beside it catches the case where that stops
         * being true. Removing the key fails nothing -- measured -- so
         * without this test both could go and nobody would know until an
         * admin read one submission's tally under another's findings.
         */
        serveAnyInspection()
        serveBuild(tally({ created_count: 3 }))
        const user = await inspect("7")
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        await reinspect(user, "9")
        await showing("9")

        // Submission 9's findings with submission 7's tally under them is a
        // false report, and the more convincing for being partly true.
        expect(screen.getByRole("status")).toBeEmptyDOMElement()
    })

    it("does not follow the admin BACK to a submission already seen", async () => {
        /**
         * The same rule through the cached door, and the one that made the
         * first version of this feature's comments wrong.
         *
         * Walking to a submission this session has not fetched, `query.data`
         * goes undefined while it loads and the parent unmounts the whole
         * results block -- so the tally dies whether or not the component
         * carries a `key`. Walking BACK, react-query answers from its cache
         * (`gcTime`, five minutes by default): data never goes undefined,
         * nothing unmounts, and the `key` is the only thing left that drops
         * the tally. MEASURED: without the key this test fails and the
         * one above still passes.
         */
        serveAnyInspection()
        serveBuild(tally({ created_count: 3 }))
        const user = await inspect("7")
        await showing("7")

        await reinspect(user, "9")
        await showing("9")
        await user.click(screen.getByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        await reinspect(user, "7")
        await showing("7")
        expect(screen.getByRole("status")).toBeEmptyDOMElement()
    })
})
