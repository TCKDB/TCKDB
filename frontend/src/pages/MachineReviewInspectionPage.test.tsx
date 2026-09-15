import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"
import { CuratorTaskBuildResultSchema } from "../types/curatorTask"
import {
    knownRunStatus,
    MachineReviewRunResultSchema,
    reviewFailed,
} from "../types/machineReviewRun"

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
 * `MemoryRouter` is here because the build tally offers a link into
 * Machine findings, and a `<Link>` outside a router throws. The page itself
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
 * The page's four live regions, by accessible name.
 *
 * There used to be two, and these helpers could say `getByRole("status")`.
 * The run control added a second pair, so an unqualified query now matches
 * two elements and throws -- which is the test suite noticing, correctly,
 * that "the status region" had stopped naming one thing. The regions carry
 * `aria-label`s for the same reason a screen-reader user needs them: to
 * tell which button a result answered.
 */
const BUILD_ALERT = "curator task build problem"
const BUILD_STATUS = "curator task build result"
const RUN_ALERT = "machine review request problem"
const RUN_STATUS = "machine review run outcome"

function liveRegion(role: "status" | "alert", name: string): HTMLElement {
    return screen.getByRole(role, { name })
}

/**
 * Wait until a live region has something in it.
 *
 * Every one of them is permanently mounted and initially EMPTY (see the
 * component), so its mere presence proves nothing and `findByRole` resolves
 * instantly whether anything has run or not. Emptiness is the signal.
 */
async function regionFills(region: HTMLElement): Promise<HTMLElement> {
    await waitFor(() => expect(region).not.toBeEmptyDOMElement())
    return region
}

async function regionSays(region: HTMLElement, pattern: RegExp): Promise<HTMLElement> {
    await waitFor(() => expect(region).toHaveTextContent(pattern))
    return region
}

/** The curator-task tally region, once it has something in it. */
function tallyShown(): Promise<HTMLElement> {
    return regionFills(liveRegion("status", BUILD_STATUS))
}

/** The curator-task alert region's text, once it has some. */
function alertSays(pattern: RegExp): Promise<HTMLElement> {
    return regionSays(liveRegion("alert", BUILD_ALERT), pattern)
}

/** The machine-review run outcome region, once it has something in it. */
function runShown(): Promise<HTMLElement> {
    return regionFills(liveRegion("status", RUN_STATUS))
}

/** The machine-review run alert region's text, once it has some. */
function runAlertSays(pattern: RegExp): Promise<HTMLElement> {
    return regionSays(liveRegion("alert", RUN_ALERT), pattern)
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
            within(status).getByText(/3 new tasks are now under Machine findings/),
        ).toBeInTheDocument()

        // And somewhere to go and look at them. A tally with no way through
        // to the queue leaves the admin to remember the URL.
        expect(within(status).getByRole("link", { name: "Open Machine findings" })).toHaveAttribute(
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
        const button = await screen.findByRole("button", { name: BUILD_BUTTON })
        // Scoped to this control's own section. The run control above it
        // makes the same promise about machine review, in nearly the same
        // words, so an unscoped `getByText(/endorses nothing/i)` now matches
        // two paragraphs -- and would have gone on passing while this
        // paragraph said nothing of the kind, satisfied by the other one.
        const section = button.closest("section")!

        expect(within(section).getByText(/created on upload/i)).toBeInTheDocument()
        expect(within(section).getByText(/endorses nothing/i)).toBeInTheDocument()
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
            within(status).getByText(/1 new task is now under Machine findings/),
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
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
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
         *    read the tally. Any tasks it made are under Machine findings."
         */
        serveAnyInspection()
        serveBuild({ this_is: "not a tally" })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const alert = await alertSays(/could not read the tally/)
        expect(alert).not.toHaveTextContent(/No tasks were built/)
        // And it must point at the one place the truth can be found.
        expect(alert).toHaveTextContent(/Machine findings/)
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
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
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
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
    })
})

/* ------------------------------------------------------------------ *
 * Running machine review (task #482)
 *
 * The other half of the same hole. The builder above turns machine-review
 * findings into work for a person, and until this control existed nothing
 * in the product could produce a finding to turn: the machine-review stack
 * is not wired into uploads, so the live deployment held 44 submissions
 * and zero machine reviews, and every screen built on top of them had
 * nothing to show.
 * ------------------------------------------------------------------ */

const INSPECTION = "/api/v1/admin/submissions/:submissionId/machine-review-inspection"

const RUN = "/api/v1/admin/machine-review/run-for-submission/:submissionId"

const RUN_BUTTON = "Run machine review for this submission"

/** One inspection payload, with whichever record summaries a test wants. */
function inspectionBody(submissionId: unknown, records: Record<string, unknown>[]) {
    return {
        submission_id: Number(submissionId),
        record_summaries: records,
        unmapped_findings_count: 0,
        mapping_warnings: [],
        parse_warnings: [],
        source_audit_event_ids: [11],
    }
}

/** A record summary naming where it came from, so a wait can assert on it. */
function record(publicRef: string): Record<string, unknown> {
    return {
        record_type: "species",
        record_public_ref: publicRef,
        record_id: 431,
        latest_summary: SUMMARY,
        all_record_reviews_count: 1,
    }
}

type RunReply = {
    submission_id?: number
    status?: string
    findings_count?: number
    summary?: string | null
    model?: string | null
    provider?: string | null
    audit_event_recorded?: boolean
    failure_reason?: string | null
    audit_event_id?: number
}

/** A run reply with every contract field present, overridable one at a time. */
function runReply(over: RunReply = {}): Record<string, unknown> {
    return {
        submission_id: 7,
        status: "machine_screened_warning",
        findings_count: 0,
        summary: null,
        model: "anthropic/claude-x",
        provider: "CloudMachineReviewProvider",
        audit_event_recorded: true,
        failure_reason: null,
        ...over,
    }
}

/**
 * Serve the run route. The returned array collects the submission ids it was
 * actually called with, which is how "ran for the one on screen" is asserted
 * rather than assumed.
 */
function serveRun(
    body: Record<string, unknown>,
    init?: { status: number },
): { calledFor: number[] } {
    const calledFor: number[] = []
    server.use(
        http.post(RUN, ({ params }) => {
            calledFor.push(Number(params.submissionId))
            return HttpResponse.json(body, init)
        }),
    )
    return { calledFor }
}

describe("the schema behind a machine-review run", () => {
    it("drops keys the contract does not list, so no page can print one", () => {
        /**
         * The contract carries no row id on purpose: `audit_event_recorded`
         * says WHETHER a run was written to the audit log, never which row it
         * became. This pins the mechanism that keeps it that way if the
         * server ever sends one anyway. zod strips what the schema does not
         * declare, so the id never reaches the object the page renders from.
         * DR-0028 Req 2.
         */
        const parsed = MachineReviewRunResultSchema.parse(
            runReply({ findings_count: 2, audit_event_id: 9911 }),
        )
        expect("audit_event_id" in parsed).toBe(false)
        expect(parsed.findings_count).toBe(2)
    })

    it("reads a status this build has never heard of rather than failing", () => {
        /**
         * The reply is ONE object, so a strict `z.enum` on `status` would
         * throw the whole result away over a token nothing branches on,
         * losing `findings_count`, `summary`, `model` and `failure_reason`,
         * every one of which is readable and is what the admin came for. The
         * page's single branch is a positive test for `machine_review_failed`,
         * so an unfamiliar token is honestly "not that".
         */
        const parsed = MachineReviewRunResultSchema.parse(
            runReply({ status: "machine_screened_something_new" }),
        )
        expect(parsed.status).toBe("machine_screened_something_new")
        expect(reviewFailed(parsed)).toBe(false)
        // ...and it is not dressed up as one of the five this build knows.
        expect(knownRunStatus(parsed.status)).toBeNull()
    })
})

describe("running machine review for a submission", () => {
    it("is not offered before a submission has been inspected", () => {
        render(
            <MemoryRouter>
                <QueryClientProvider client={new QueryClient()}>
                    <MachineReviewInspectionPage />
                </QueryClientProvider>
            </MemoryRouter>,
        )
        expect(screen.queryByRole("button", { name: RUN_BUTTON })).toBeNull()
    })

    it("runs for the submission on screen, not some other one", async () => {
        serveAnyInspection()
        const run = serveRun(runReply({ submission_id: 9, findings_count: 1 }))
        const user = await inspect("9")
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        // The id is in the URL path, so a page that ran for a constant, or
        // for whatever was typed first, would still show a plausible result.
        expect(run.calledFor).toEqual([9])
    })

    it("sends the admin session cookie, and no request body", async () => {
        /**
         * Two halves of the same contract. Without `credentials: "include"`
         * the browser attaches no `tckdb_session`, so a logged-in admin
         * reaches an admin-only route anonymously and is refused -- a failure
         * that looks like a permissions problem and is not one. And the route
         * takes no body, so sending one is the page inventing parameters the
         * contract has not got.
         */
        serveAnyInspection()
        const seen: { credentials?: string; body?: string | null }[] = []
        server.use(
            http.post(RUN, async ({ request }) => {
                seen.push({
                    credentials: request.credentials,
                    body: await request.text(),
                })
                return HttpResponse.json(runReply())
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        expect(seen).toHaveLength(1)
        expect(seen[0].credentials).toBe("include")
        expect(seen[0].body).toBe("")
    })

    it("reports what the run recorded", async () => {
        serveAnyInspection()
        serveRun(
            runReply({
                findings_count: 3,
                model: "anthropic/claude-x",
                provider: "CloudMachineReviewProvider",
                summary: "Two geometries lack a frequency calculation.",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        const lines = within(outcome)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())

        expect(lines).toContain("findings recorded: 3")
        expect(lines).toContain("model: anthropic/claude-x")
        expect(lines).toContain("provider: CloudMachineReviewProvider")
        expect(outcome).toHaveTextContent(/recorded 3 findings/)
        expect(outcome).toHaveTextContent(
            /Two geometries lack a frequency calculation/,
        )
        // The status badge, which carries the "not human-approved" wording.
        expect(outcome).toHaveTextContent(/screened: warning/)
    })

    it("counts one finding in the singular", async () => {
        serveAnyInspection()
        serveRun(runReply({ findings_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/recorded 1 finding\./)
        expect(outcome).not.toHaveTextContent(/recorded 1 findings/)
    })

    it("does not call an off switch a review that found nothing", async () => {
        /**
         * `not_run` is the one outcome with nothing behind it: the route's
         * service returns it "without calling anything" when the reviewer is
         * disabled, and writes no audit event. Reporting it as "the review ran
         * and recorded 0 findings" tells an admin the submission was looked at
         * and is clean. Zero findings from a reviewer that looked and zero
         * from one that is switched off are the same number and opposite news.
         */
        serveAnyInspection()
        serveRun(
            runReply({
                status: "not_run",
                findings_count: 0,
                summary: null,
                model: null,
                provider: null,
                audit_event_recorded: false,
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/No review happened/)
        expect(outcome).toHaveTextContent(/switched off/i)
        expect(outcome).not.toHaveTextContent(/The review ran/)
        expect(outcome).not.toHaveTextContent(/recorded 0 findings/)
        // And it says the absence of a journal entry, which is the archive's
        // own record that nothing happened.
        expect(outcome).toHaveTextContent(/not written to the audit log/)
    })

    it("presents a failed review as a review that ran, not a failed request", async () => {
        /**
         * The whole point of the route answering 200 here. Machine review is
         * advisory, so a reviewer that failed is an OUTCOME the archive
         * recorded, not a broken request, and the two send an admin to
         * different places. "The request failed" points at the network and
         * the deployment; the truth is that a review happened and the
         * reviewer did not manage to produce findings, which is what
         * `failure_reason` explains.
         */
        serveAnyInspection()
        serveRun(
            runReply({
                status: "machine_review_failed",
                findings_count: 0,
                failure_reason: "provider returned no usable JSON after 3 tries",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/The review ran/)
        expect(outcome).toHaveTextContent(/the reviewer failed/i)
        expect(outcome).toHaveTextContent(
            /provider returned no usable JSON after 3 tries/,
        )
        // Not this page's three request-failure sentences, any of which would
        // be a false report about a request that in fact succeeded.
        expect(outcome).not.toHaveTextContent(/did not run/i)
        expect(outcome).not.toHaveTextContent(/did not report back/i)
        expect(outcome).not.toHaveTextContent(/could not read/i)
        // And structurally, not in the region this page keeps for requests
        // that went wrong. A reviewer that failed is not one of those.
        expect(liveRegion("alert", RUN_ALERT)).toBeEmptyDOMElement()
    })

    it("does not blame the submission for the reviewer failing", async () => {
        serveAnyInspection()
        serveRun(
            runReply({
                status: "machine_review_failed",
                failure_reason: "rate limited",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/not of this submission/i)
    })

    it("shows the findings the run just produced, without a second inspect", async () => {
        /**
         * The findings table is a cached react-query read taken before the
         * run. Without invalidating it the admin is shown the run's own
         * "recorded 1 finding" directly above a table still saying no record
         * received any: the page contradicting itself about work it just
         * did, and an admin's reasonable conclusion is that the run failed.
         */
        const archive = { reviewed: false }
        server.use(
            http.get(INSPECTION, ({ params }) =>
                HttpResponse.json(
                    inspectionBody(
                        params.submissionId,
                        archive.reviewed ? [record("spc_found_by_the_run")] : [],
                    ),
                ),
            ),
            http.post(RUN, () => {
                archive.reviewed = true
                return HttpResponse.json(runReply({ findings_count: 1 }))
            }),
        )

        const user = await inspect()
        // Before: the table says the archive holds nothing for this
        // submission, which is the state 44 of 44 submissions were in.
        await screen.findByText(/No records received mapped machine-review findings/)

        await user.click(screen.getByRole("button", { name: RUN_BUTTON }))
        await runShown()

        expect(await screen.findByText("spc_found_by_the_run")).toBeInTheDocument()
    })

    it("cannot be pressed twice while one run is in flight", async () => {
        serveAnyInspection()
        const held = gate()
        server.use(
            http.post(RUN, async () => {
                await held.held
                return HttpResponse.json(runReply({ findings_count: 1 }))
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        // A run calls a paid provider and writes rows. A second press before
        // the first answers buys a second review of the same submission, and
        // the admin has no way to know which result they end up reading.
        const busy = await screen.findByRole("button", {
            name: /Running machine review/,
        })
        expect(busy).toBeDisabled()

        held.release()
        await runShown()
    })

    it("says the review did not run when the server refused", async () => {
        serveAnyInspection()
        serveRun({ detail: "Submission not found." }, { status: 404 })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        await runAlertSays(/The review did not run: Submission not found/)
        // No outcome: a refused request must not leave a row of zeroes
        // looking like a review that happened and found nothing.
        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
        expect(screen.getByRole("button", { name: RUN_BUTTON })).toBeEnabled()
    })

    it("does NOT say the review did not run when the reply was unreadable", async () => {
        /**
         * The 2xx-with-an-unparseable-body case. A review HAS run, the
         * provider has been paid and rows are written; only the reply could
         * not be read. Telling the admin it did not run invites them to press
         * again and buy a second one.
         */
        serveAnyInspection()
        serveRun({ this_is: "not a run result" })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const alert = await runAlertSays(/could not read its result/)
        expect(alert).not.toHaveTextContent(/did not run/i)
        // And it must point at the one place the truth can be found.
        expect(alert).toHaveTextContent(/Inspect this submission again/)
    })

    it("admits it does not know when the request got no answer", async () => {
        // Offline, DNS, a dropped connection. A review may or may not have
        // happened, and guessing either way is a false report.
        serveAnyInspection()
        server.use(http.post(RUN, () => HttpResponse.error()))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const alert = await runAlertSays(/may or may not have happened/)
        expect(alert).not.toHaveTextContent(/did not run/i)
    })

    it("says on the page that a machine review is advisory", async () => {
        /**
         * Three separate axes, and this paragraph is where the page keeps
         * them apart: machine review is not human review of a record, and
         * neither is moderation of the submission. An admin who reads a
         * screened-pass badge as an endorsement has been misled by this page,
         * not by the reviewer.
         */
        serveAnyInspection()
        await inspect()
        const button = await screen.findByRole("button", { name: RUN_BUTTON })
        const section = button.closest("section")!

        expect(within(section).getByText(/advisory/i)).toBeInTheDocument()
        expect(within(section).getByText(/endorses\s+nothing/i)).toBeInTheDocument()
        expect(within(section).getByText(/human\s+review/i)).toBeInTheDocument()
        expect(within(section).getByText(/moderation/i)).toBeInTheDocument()
        // And that nothing produces these by itself, which is why an archive
        // with no machine reviews means "nobody has run one", not "clean".
        expect(within(section).getByText(/on upload/i)).toBeInTheDocument()
    })

    it("never prints a row id the server sent anyway", async () => {
        serveAnyInspection()
        serveRun(runReply({ findings_count: 2, audit_event_id: 9911 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        // `textContent` covers the rendered words; `outerHTML` covers the
        // places an id can hide from it: a `title`, a `data-` attribute, an
        // `aria-label`. DR-0028 Req 2.
        expect(document.body.textContent ?? "").not.toContain("9911")
        expect(document.body.outerHTML).not.toContain("9911")
        expect(document.body.textContent ?? "").toContain("findings recorded: 2")
    })

    it("does not follow the admin to another submission", async () => {
        /**
         * Submission 9's findings under submission 7's run result is a false
         * report, and the more convincing for being partly true. Today the
         * result is dropped because the parent unmounts the whole results
         * block while the next inspection loads; the `key={submissionId}`
         * beside it catches the case where that stops being true.
         */
        serveAnyInspection()
        serveRun(runReply({ findings_count: 3 }))
        const user = await inspect("7")
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        await reinspect(user, "9")
        await showing("9")

        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
    })

    it("does not follow the admin BACK to a submission already seen", async () => {
        /**
         * The same rule through the cached door, and the only one of the two
         * the `key` is load-bearing for. Walking to a submission this session
         * has not fetched, `query.data` goes undefined while it loads and the
         * parent unmounts the whole results block, so the result dies whether
         * or not the component carries a key. Walking BACK, react-query
         * answers from its cache (`gcTime`, five minutes by default): data
         * never goes undefined, nothing unmounts, and the key is the only
         * thing left that drops the stale result.
         */
        serveAnyInspection()
        serveRun(runReply({ findings_count: 3 }))
        const user = await inspect("7")
        await showing("7")

        await reinspect(user, "9")
        await showing("9")
        await user.click(screen.getByRole("button", { name: RUN_BUTTON }))
        await runShown()

        await reinspect(user, "7")
        await showing("7")
        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
    })
})
